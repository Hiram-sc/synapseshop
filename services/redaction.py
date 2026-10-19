import re
from typing import Any, Dict, List, Union

TOKEN_POSSIVEIS = (
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "bearer",
    "api_key",
    "apikey",
    "secret",
    "password",
    "senha",
    "passwd",
    "client_secret",
    "private_key",
)

PII_PATTERNS = [
    re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
    re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/\d{4}-?\d{2}\b"),
    re.compile(r"\b\d{11}\b"),
    re.compile(r"\b\d{14}\b"),
    re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    re.compile(r"\b\d{5}-?\d{3}\b"),
]

SEGREDO_PADRAO = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|senha|bearer"
    r"|client[_-]?secret|private[_-]?key)\s*[:=]\s*[^\s,;]+"
)
JWT_PADRAO = re.compile(r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
CREDIT_CARD = re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b")

MASCARA = "[REDACTED]"


def _redigir_valor(valor: Any) -> Any:
    if valor is None:
        return valor
    if isinstance(valor, str):
        texto = valor
        texto = SEGREDO_PADRAO.sub(r"\1: [REDACTED]", texto)
        texto = JWT_PADRAO.sub(MASCARA, texto)
        texto = CREDIT_CARD.sub(MASCARA, texto)
        for padrao in PII_PATTERNS:
            texto = padrao.sub(MASCARA, texto)
        return texto
    if isinstance(valor, dict):
        return redigir_dicionario(valor)
    if isinstance(valor, list):
        return [redigir_valor(v) for v in valor]
    if isinstance(valor, tuple):
        return tuple(redigir_valor(v) for v in valor)
    return valor


def redigir_dicionario(dados: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(dados, dict):
        return dados
    resultado = {}
    for chave, valor in dados.items():
        chave_str = str(chave)
        if any(t in chave_str.lower() for t in TOKEN_POSSIVEIS):
            resultado[chave] = MASCARA
            continue
        resultado[chave] = _redigir_valor(valor)
    return resultado


def redigir_texto(texto: Union[str, None]) -> str:
    if not texto:
        return ""
    if isinstance(texto, str):
        return _redigir_valor(texto)
    return str(texto)


def truncar_texto(texto: str, limite: int) -> str:
    """Corta `texto` em `limite` caracteres, sinalizando o corte com `...`.

    Para `limite >= 3` o resultado tem exatamente `limite` caracteres
    (conteúdo + reticências); valores menores são tratados sem fatiar negativo.
    `limite <= 0` desliga o truncamento.
    """
    if limite <= 0 or not texto:
        return texto
    if len(texto) <= limite:
        return texto
    if limite < 3:
        return texto[:limite] + "..."
    return texto[: limite - 3] + "..."
