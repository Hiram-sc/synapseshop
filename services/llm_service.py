import json
import logging
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

from django.conf import settings

from services.prompts import montar_prompt, serializar_logs
from services.redaction import redigir_texto, truncar_texto

logger = logging.getLogger("llm_service")


@dataclass
class TelemetriaLLM:
    latencia_ms: int
    tokens_prompt: int = 0
    tokens_output: int = 0
    custo_usd: float = 0.0
    sucesso: bool = True
    erro: Optional[str] = None
    tentativas: int = 0


class CircuitBreakerError(Exception):
    pass


class CircuitBreaker:
    """Circuit breaker com os três estados exigidos pela spec.

    * ``closed``    - chamadas liberadas; falhas consecutivas são contadas.
    * ``open``      - chamadas bloqueadas até passar ``reset_s`` desde a última
      falha; vencido o intervalo, entra em ``half-open``.
    * ``half-open`` - libera **uma** chamada-sonda; sucesso volta a ``closed``,
      falha reabre (``open``).

    O objeto é sincronizado apenas por thread (o serviço é síncrono); não há
    lock porque a aplicação roda um único worker por processo no Compose.
    """

    FECHADO = "closed"
    ABERTO = "open"
    MEIO_ABERTO = "half_open"

    def __init__(self, limite_falhas: int, reset_s: float):
        self.limite_falhas = limite_falhas
        self.reset_s = reset_s
        self.falhas = 0
        self.ultima_falha = 0.0
        self.estado = self.FECHADO
        self._sonda_permitida = False

    @property
    def aberto(self) -> bool:
        # compatibilidade com o código/testes que liam `circuit.aberto`
        return self.estado == self.ABERTO

    def pode_chamar(self) -> bool:
        if self.estado == self.FECHADO:
            return True
        if self.estado == self.ABERTO:
            if time.time() - self.ultima_falha > self.reset_s:
                # fim do bloqueio: abre uma janela de sonda (half-open)
                self.estado = self.MEIO_ABERTO
                self._sonda_permitida = True
            else:
                return False
        if self.estado == self.MEIO_ABERTO:
            if self._sonda_permitida:
                self._sonda_permitida = False
                return True
            return False
        return False

    def registrar_sucesso(self) -> None:
        self.falhas = 0
        self.estado = self.FECHADO
        self._sonda_permitida = False

    def registrar_falha(self) -> None:
        self.falhas += 1
        self.ultima_falha = time.time()
        if self.estado == self.MEIO_ABERTO:
            # a sonda falhou: reabre sem esperar acumular novas falhas
            self.estado = self.ABERTO
        elif self.falhas >= self.limite_falhas:
            self.estado = self.ABERTO


RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}
NORETRY_STATUSES = {400, 401, 403}


def _categoria_erro(exc: Exception) -> str:
    """Categoria estável do erro, sem expor o corpo devolvido pelo provedor."""
    if isinstance(exc, TimeoutError):
        return "timeout"
    msg = str(exc)
    if "http_" in msg:
        try:
            return f"http_{int(msg.split('http_')[1].split(':')[0])}"
        except Exception:
            return "http_desconhecido"
    if "resposta_invalida" in msg:
        return "resposta_invalida"
    return "erro_interno"


class LLMService:
    def __init__(self) -> None:
        self.api_key = getattr(settings, "LLM_API_KEY", "")
        self.api_base_url = getattr(settings, "LLM_API_BASE_URL", "https://api.deepseek.com/v1")
        self.model = getattr(settings, "LLM_MODEL", "deepseek-chat")
        self.timeout_s = float(getattr(settings, "LLM_TIMEOUT_S", 5))
        self.max_tentativas = int(getattr(settings, "LLM_MAX_TENTATIVAS", 3))
        self.backoff_base_s = float(getattr(settings, "LLM_BACKOFF_BASE_S", 0.1))
        self.backoff_max_s = float(getattr(settings, "LLM_BACKOFF_MAX_S", 1.0))
        self.max_tokens_limite = int(getattr(settings, "LLM_MAX_TOKENS_LIMITE", 1024))
        self.max_logs_chars = int(getattr(settings, "LLM_MAX_LOGS_CARACTERES", 2000))
        self.redactar_envio = bool(getattr(settings, "LLM_REDACTAR_ENVIO", True))
        self.precio_entrada_1k = float(getattr(settings, "LLM_PRECO_ENTRADA_POR_1K", 0.001))
        self.precio_saida_1k = float(getattr(settings, "LLM_PRECO_SAIDA_POR_1K", 0.002))
        self.circuit = CircuitBreaker(
            int(getattr(settings, "LLM_CIRCUITO_LIMITE_FALHAS", 5)),
            float(getattr(settings, "LLM_CIRCUITO_RESET_S", 60)),
        )

    def _calcular_custo(self, tokens_prompt: int, tokens_output: int) -> float:
        return round(
            tokens_prompt / 1000.0 * self.precio_entrada_1k
            + tokens_output / 1000.0 * self.precio_saida_1k,
            6,
        )

    def montar_logs(self, logs: Any) -> str:
        """Serializa, redige (PII/segredos) e trunca os logs antes do envio.

        É a fronteira de privacidade do serviço: o que sai daqui - e só o que
        sai daqui - é enviado ao provedor de LLM.
        """
        texto = serializar_logs(logs)
        if self.redactar_envio:
            texto = redigir_texto(texto)
        return truncar_texto(texto, self.max_logs_chars)

    def _montar_payload(self, messages: list, temperature: float, max_tokens: int) -> Dict[str, Any]:
        mt = min(max_tokens, self.max_tokens_limite)
        mt = max(mt, 1)
        temp = max(0.0, min(1.0, float(temperature)))
        return {
            "model": self.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": mt,
        }

    def _chamar_api(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.api_base_url.rstrip('/')}/chat/completions"
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                status = resp.getcode()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            status = exc.code
            raise Exception(f"http_{status}:{body[:200]}")
        except urllib.error.URLError as exc:
            # indisponibilidade ou timeout de rede
            raise TimeoutError(str(exc))
        except Exception as exc:
            raise exc

        if status >= 400:
            raise Exception(f"http_{status}:{body[:200]}")

        try:
            return json.loads(body)
        except Exception as exc:
            raise ValueError(f"resposta_invalida:{exc}")

    def chat_completions(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.circuit.pode_chamar():
            raise CircuitBreakerError("circuit_aberto")

        ultima_exc: Optional[Exception] = None
        for tentativa in range(1, self.max_tentativas + 1):
            try:
                inicio = time.time()
                resp = self._chamar_api(payload)
                duracao_ms = int((time.time() - inicio) * 1000)
                self.circuit.registrar_sucesso()
                usage = resp.get("usage") or {}
                tokens_prompt = int(usage.get("prompt_tokens") or usage.get("promptTokens") or 0)
                tokens_output = int(usage.get("completion_tokens") or usage.get("completionTokens") or 0)
                answer = ""
                choices = resp.get("choices") or []
                if choices:
                    msg = choices[0].get("message") or {}
                    answer = msg.get("content") or ""
                telem = TelemetriaLLM(
                    latencia_ms=duracao_ms,
                    tokens_prompt=tokens_prompt,
                    tokens_output=tokens_output,
                    custo_usd=self._calcular_custo(tokens_prompt, tokens_output),
                    sucesso=True,
                    tentativas=tentativa,
                )
                self._log_telemetria(telem)
                return {"answer": answer, "meta": {"tokens_prompt": tokens_prompt, "tokens_output": tokens_output}, "telemetria": telem}
            except TimeoutError as exc:
                ultima_exc = exc
                self.circuit.registrar_falha()
                if tentativa >= self.max_tentativas:
                    self._log_falha(exc, tentativa)
                    raise
                self._esperar_backoff(tentativa)
                continue
            except Exception as exc:
                msg = str(exc)
                if "http_" in msg:
                    try:
                        status = int(msg.split("http_")[1].split(":")[0])
                    except Exception:
                        status = 0
                    if status in NORETRY_STATUSES:
                        self.circuit.registrar_falha()
                        self._log_falha(exc, tentativa)
                        raise
                    if status in RETRYABLE_STATUSES or status == 0:
                        self.circuit.registrar_falha()
                        if tentativa >= self.max_tentativas:
                            self._log_falha(exc, tentativa)
                            raise
                        self._esperar_backoff(tentativa)
                        continue
                self.circuit.registrar_falha()
                self._log_falha(exc, tentativa)
                raise

        if ultima_exc:
            raise ultima_exc
        raise RuntimeError("falha_llm")

    def _esperar_backoff(self, tentativa: int) -> None:
        tempo = min(
            self.backoff_base_s * (2 ** (tentativa - 1)) + random.uniform(0, 0.05),
            self.backoff_max_s,
        )
        time.sleep(tempo)

    def _log_telemetria(self, telem: TelemetriaLLM) -> None:
        # nenhum conteúdo de log/segredo é registrado: só métricas numéricas
        logger.info(
            "llm_chamada sucesso=%s latencia_ms=%s tokens_prompt=%s "
            "tokens_output=%s custo_usd=%s tentativas=%s",
            telem.sucesso,
            telem.latencia_ms,
            telem.tokens_prompt,
            telem.tokens_output,
            telem.custo_usd,
            telem.tentativas,
        )

    def _log_falha(self, exc: Exception, tentativa: int) -> None:
        logger.warning(
            "llm_chamada_falha categoria=%s tentativas=%s estado_circuito=%s",
            _categoria_erro(exc),
            tentativa,
            self.circuit.estado,
        )

    def assinar_e_resumir(
        self,
        mode: str,
        logs: Any,
        temperature: float = 0.2,
        max_tokens: int = 256,
    ) -> Dict[str, Any]:
        prompt = montar_prompt(mode, self.montar_logs(logs))
        mt = min(max_tokens, self.max_tokens_limite)
        payload = self._montar_payload(prompt["messages"], temperature, mt)
        return self.chat_completions(payload)


#: instância compartilhada pelo processo. Mantém o circuit breaker vivo **entre
#: requisições** - sem ela, cada `POST /assist` criaria um breaker zerado e a
#: proteção nunca dispararia.
_instancia_padrao: Optional[LLMService] = None


def obter_llm_service() -> LLMService:
    global _instancia_padrao
    if _instancia_padrao is None:
        _instancia_padrao = LLMService()
    return _instancia_padrao
