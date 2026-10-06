"""Mostra o estado do cache em dois níveis: o que está no Redis e o que a API mediu.

    python manage.py cache_stats
    python manage.py cache_stats --json
    python manage.py cache_stats --limpar     # apaga as chaves do catálogo
    python manage.py cache_stats --zerar      # zera os contadores do processo

Os dois níveis respondem perguntas diferentes, e vale saber qual é qual:

* **Redis** (`INFO`, `SCAN`, `TTL`) responde o que existe no cache agora: quantas
  chaves, de que tipo, quanto falta para expirar, quanto de memória está em uso
  e quanto o próprio Redis acertou ou errou desde que subiu;
* **processo** (`services/cache.py`) responde o que a API fez: quantas leituras
  foram `HIT`, `MISS` ou `BYPASS`, e quanto tempo foi gasto dentro do cache.

Só o segundo é o número que responde "o cache está ajudando?". O primeiro deixa
evidentes as chaves órfãs das gerações antigas, que continuam no Redis até o
TTL expirar.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand

from services import cache as cache_service


def _padrao_das_chaves() -> str:
    """Padrão `SCAN` das chaves do catálogo, já com prefixo e versão do Django.

    O Django monta as chaves como `<KEY_PREFIX>:<versão>:<chave>`; sem repetir
    esse prefixo aqui, o `SCAN` não encontraria nada.
    """
    return f"{settings.CACHE_KEY_PREFIX}:1:{cache_service.NAMESPACE}"


def _tipo_da_chave(chave: str) -> str:
    """Classifica uma chave do catálogo.

    A ordem importa: `catalogo:v1:itens:lista:geracao` também contém
    `:itens:lista:`, então sem checar o sufixo primeiro a chave de geração
    seria contada também como página de listagem.
    """
    if chave.endswith(":geracao"):
        return "geracao"
    if f":{cache_service.DETALHE}:" in chave:
        return "detalhe"
    if f":{cache_service.LISTA}:" in chave:
        return "listagem"
    return "outros"


class Command(BaseCommand):
    help = "Relatório do cache do catálogo (Redis + métricas do processo)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--json",
            action="store_true",
            help="Devolve o relatório em JSON, para consumo por script.",
        )
        parser.add_argument(
            "--limpar",
            action="store_true",
            help=(
                "Apaga as chaves `catalogo:v1:*` (listagem, detalhe e geração). "
                "Use para começar uma medição com o cache vazio."
            ),
        )
        parser.add_argument(
            "--zerar",
            action="store_true",
            help="Zera os contadores de hits/misses do processo.",
        )

    # ------------------------------------------------------------------
    def handle(self, *args: Any, **options: Dict[str, Any]) -> None:
        if options["limpar"]:
            removidas = cache_service.limpar_namespace()
            self.stdout.write(f"{removidas} chave(s) do catálogo removida(s).")

        if options["zerar"]:
            cache_service.zerar_metricas()
            self.stdout.write("Contadores do processo zerados.")

        relatorio = self.montar_relatorio()
        if options["json"]:
            self.stdout.write(json.dumps(relatorio, indent=2, ensure_ascii=False))
            return

        self.imprimir(relatorio)

    # ------------------------------------------------------------------
    def montar_relatorio(self) -> Dict[str, Any]:
        cliente = cache_service.cliente_redis()

        chaves: list = []
        redis_online = cliente is not None
        if redis_online:
            # o cliente cru devolve bytes (o `decode_responses` do Django cache é
            # outro cliente); aqui só interessa ler a chave
            chaves = [
                self._decodificar(chave)
                for chave in cliente.scan_iter(match=_padrao_das_chaves(), count=500)
            ]

        chaves_por_tipo = {"listagem": 0, "detalhe": 0, "geracao": 0, "outros": 0}
        for chave in chaves:
            chaves_por_tipo[_tipo_da_chave(chave)] += 1

        memoria: Dict[str, Any] = {}
        estatisticas: Dict[str, Any] = {}
        if redis_online:
            info_memoria = cliente.info("memory")
            info_estatisticas = cliente.info("stats")
            memoria = {
                "usada": info_memoria.get("used_memory_human"),
                "pico": info_memoria.get("used_memory_peak_human"),
                "politica": info_memoria.get("maxmemory_policy"),
            }
            acertos = info_estatisticas.get("keyspace_hits", 0)
            erros = info_estatisticas.get("keyspace_misses", 0)
            estatisticas = {
                "keyspace_hits": acertos,
                "keyspace_misses": erros,
                "taxa_de_acerto": (
                    acertos / (acertos + erros) if (acertos + erros) else 0.0
                ),
            }

        return {
            "configuracao": {
                "ativo": settings.CACHE_ENABLED,
                "backend": settings.CACHES["default"]["BACKEND"],
                "location": settings.CACHES["default"]["LOCATION"],
                "prefixo": settings.CACHE_KEY_PREFIX,
                "ttl_lista": settings.CACHE_TTL_LISTA,
                "ttl_detalhe": settings.CACHE_TTL_DETALHE,
                "redis_online": redis_online,
            },
            "redis": {
                "chaves_do_catalogo": len(chaves),
                "por_tipo": chaves_por_tipo,
                "memoria": memoria,
                "estatisticas": estatisticas,
                "geracao_atual": cache_service.geracao_atual(),
                "exemplos": chaves[:5],
            },
            "processo": {
                endpoint: {
                    "lookups": m.lookups,
                    "hits": m.hits,
                    "misses": m.misses,
                    "bypass": m.bypass,
                    "erros": m.erros,
                    "hit_rate": round(m.hit_rate, 4),
                    "tempo_medio_cache_ms": round(m.tempo_medio_cache_ms, 3),
                    "tempo_medio_total_ms": round(m.tempo_medio_total_ms, 3),
                }
                for endpoint, m in cache_service.metricas().items()
            },
        }

    @staticmethod
    def _decodificar(chave) -> str:
        return chave.decode("utf-8") if isinstance(chave, bytes) else str(chave)

    # ------------------------------------------------------------------
    def imprimir(self, relatorio: Dict[str, Any]) -> None:
        configuracao = relatorio["configuracao"]
        redis = relatorio["redis"]

        self.stdout.write(self.style.MIGRATE_HEADING("Configuração"))
        self.stdout.write(
            f"  cache ativo .......: {'sim' if configuracao['ativo'] else 'NÃO (kill-switch)'}"
        )
        self.stdout.write(f"  backend ...........: {configuracao['backend']}")
        self.stdout.write(f"  location ..........: {configuracao['location']}")
        self.stdout.write(f"  prefixo ...........: {configuracao['prefixo']}")
        self.stdout.write(
            f"  TTL ...............: listagem {configuracao['ttl_lista']}s / "
            f"detalhe {configuracao['ttl_detalhe']}s"
        )
        if not configuracao["redis_online"]:
            self.stdout.write(
                self.style.WARNING("  Redis ..............: SEM CONEXÃO (fail-open)")
            )

        self.stdout.write(self.style.MIGRATE_HEADING("Redis"))
        self.stdout.write(f"  chaves do catálogo : {redis['chaves_do_catalogo']}")
        for tipo, quantidade in redis["por_tipo"].items():
            self.stdout.write(f"    - {tipo:<13}: {quantidade}")
        self.stdout.write(f"  geração da listagem: {redis['geracao_atual']}")
        if redis["memoria"]:
            self.stdout.write(
                f"  memória ...........: {redis['memoria']['usada']} "
                f"(pico {redis['memoria']['pico']}, política "
                f"{redis['memoria']['politica']})"
            )
        if redis["estatisticas"]:
            estatisticas = redis["estatisticas"]
            self.stdout.write(
                f"  keyspace hits/miss.: {estatisticas['keyspace_hits']}/"
                f"{estatisticas['keyspace_misses']} "
                f"({estatisticas['taxa_de_acerto']:.1%} de acerto do próprio Redis)"
            )
        for exemplo in redis["exemplos"]:
            self.stdout.write(f"    e.g. {exemplo}")

        self.stdout.write(self.style.MIGRATE_HEADING("Processo (esta instância)"))
        if not relatorio["processo"]:
            self.stdout.write("  nenhuma leitura cacheada desde o boot do processo")
        for endpoint, dados in relatorio["processo"].items():
            self.stdout.write(
                f"  {endpoint:<8}: {dados['hits']} HIT / {dados['misses']} MISS / "
                f"{dados['bypass']} BYPASS ({dados['erros']} erro(s)) | "
                f"hit rate {dados['hit_rate']:.1%} | "
                f"cache {dados['tempo_medio_cache_ms']}ms de "
                f"{dados['tempo_medio_total_ms']}ms por requisição"
            )
