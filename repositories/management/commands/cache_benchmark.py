"""Mede o ganho do cache comparando as mesmas requisições com e sem Redis.

    python manage.py cache_benchmark
    python manage.py cache_benchmark --n 500 --limit 50
    python manage.py cache_benchmark --json

O que é medido, e por quê:

* **sem cache** (`CACHE_ENABLED=false`): o custo real do PostgreSQL - filtro,
  ordenação, `COUNT` e paginação. É a linha de base de tudo;
* **cache frio** (`MISS`): o custo do banco **mais** a escrita no Redis. É o
  preço a pagar na primeira leitura depois de uma invalidação;
* **cache quente** (`HIT`): o custo de ler do Redis e remontar a resposta. É o
  número que o cliente sente na maior parte do tempo.

Cada cenário roda exatamente o mesmo número de requisições contra a mesma URL,
passando pelo cliente HTTP do Django (o mesmo caminho da requisição real, com
URL, filtros, serializer e paginação). O que muda entre eles é só o estado do
cache.

Duas escolhas que evitam medir a coisa errada:

* o throttling é desligado durante a medição, trocando o atributo
  `throttle_classes` da view. Não dá para usar `override_settings` aqui: o DRF
  resolve `APIView.throttle_classes = api_settings.DEFAULT_THROTTLE_CLASSES` na
  importação do módulo, então mudar a configuração depois não muda a lista de
  classes já congelada. Sem esse cuidado, a partir de 60 requisições o
  benchmark estaria medindo o `429` do throttle da Aula 7, que agora mora no
  mesmo Redis;
* o cenário `MISS` renova a geração da listagem antes de cada requisição, em vez
  de inventar uma URL nova por iteração. Assim todas as requisições medem
  exatamente a mesma consulta do banco.

Estes números são de uma máquina de desenvolvimento, com poucos itens no catálogo.
Eles servem para mostrar a ordem de grandeza e o formato do relatório - não para
prometer latência de produção.
"""

from __future__ import annotations

import json
import statistics
import time
from typing import Any, Callable, Dict, List
from unittest import mock

from django.conf import settings
from django.core.management.base import BaseCommand
from django.test import Client
from django.test.utils import override_settings

from api.views import ItemViewSet
from repositories.models import Item
from services import cache as cache_service


class Command(BaseCommand):
    help = "Benchmark do cache do catálogo (sem cache vs MISS vs HIT)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--n",
            type=int,
            default=200,
            help="Requisições por cenário (padrão: 200).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="Itens por página nos cenários de listagem (padrão: 20).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Devolve o resultado em JSON.",
        )

    # ------------------------------------------------------------------
    def handle(self, *args: Any, **options: Dict[str, Any]) -> None:
        n = options["n"]
        limite = options["limit"]

        if Item.objects.count() == 0:
            self.stdout.write(
                self.style.ERROR(
                    "O catálogo está vazio: crie itens antes de medir, senão o "
                    "benchmark compara banco em branco contra cache em branco."
                )
            )
            return

        listagem = f"/api/v1/items/?limit={limite}"
        item_id = Item.objects.order_by("id").first().id
        detalhe = f"/api/v1/items/{item_id}/"

        cenario: Dict[str, Any] = {}
        with mock.patch.object(ItemViewSet, "throttle_classes", []):
            cliente = Client()

            self._limpar_cache()
            cenario["listagem_sem_cache"] = self._medir(
                cliente,
                listagem,
                n,
                esperado=(cache_service.BYPASS,),
                settings_overrides={"CACHE_ENABLED": False},
            )
            cenario["detalhe_sem_cache"] = self._medir(
                cliente,
                detalhe,
                n,
                esperado=(cache_service.BYPASS,),
                settings_overrides={"CACHE_ENABLED": False},
            )
            cenario["listagem_miss"] = self._medir(
                cliente,
                listagem,
                n,
                esperado=(cache_service.MISS,),
                antes_de_cada=lambda: self._forcar_miss(),
            )
            cenario["detalhe_miss"] = self._medir(
                cliente,
                detalhe,
                n,
                esperado=(cache_service.MISS,),
                antes_de_cada=lambda: self._forcar_miss_detalhe(),
            )
            cenario["listagem_hit"] = self._medir(
                cliente, listagem, n, esperado=(cache_service.HIT,), aquecer=True
            )
            cenario["detalhe_hit"] = self._medir(
                cliente, detalhe, n, esperado=(cache_service.HIT,), aquecer=True
            )
            cenario["ciclo_de_invalidação"] = self._ciclo_de_invalidação(cliente, listagem)

        if options["json"]:
            self.stdout.write(json.dumps(cenario, indent=2, ensure_ascii=False))
            return

        self._imprimir(cenario)

    # ------------------------------------------------------------------
    # medição
    # ------------------------------------------------------------------
    def _medir(
        self,
        cliente: Client,
        url: str,
        n: int,
        esperado: tuple,
        settings_overrides: Dict[str, Any] | None = None,
        antes_de_cada: Callable[[], None] | None = None,
        aquecer: bool = False,
    ) -> Dict[str, Any]:
        """Roda `n` requisições e devolve média, p95 e RPS.

        `esperado` é o conjunto de valores de `X-Cache` que o cenário promete.
        Se a API responder outra coisa, o comando falha: medir 100 `MISS` e
        publicar o resultado como "cache quente" seria um relatório bonito e
        falso - e o número errado seria jogado na documentação da aula.
        """
        contexto = override_settings(**settings_overrides) if settings_overrides else _nulo()
        with contexto:
            if aquecer:
                cliente.get(url)  # primeira chamada é o MISS que "aquece"
            tempos: List[float] = []
            estados: Dict[str, int] = {}

            for _ in range(n):
                if antes_de_cada:
                    antes_de_cada()
                inicio = time.perf_counter()
                resposta = cliente.get(url)
                tempos.append((time.perf_counter() - inicio) * 1000)

                if resposta.status_code != 200:
                    raise RuntimeError(
                        f"requisição devolviu {resposta.status_code}: {url}"
                    )
                estado = resposta.headers.get("X-Cache", "?")
                estados[estado] = estados.get(estado, 0) + 1

        if set(estados) != set(esperado):
            raise RuntimeError(
                f"cenário esperava X-Cache {esperado} e viu {tuple(estados)} "
                f"em {url}; o número medido seria de outro caminho"
            )

        return {
            "requisicoes": n,
            "url": url,
            "media_ms": round(statistics.fmean(tempos), 3),
            "mediana_ms": round(statistics.median(tempos), 3),
            "p95_ms": round(_percentil(tempos, 95), 3),
            "min_ms": round(min(tempos), 3),
            "max_ms": round(max(tempos), 3),
            "rps": round(1000 / statistics.fmean(tempos), 1),
            "x_cache": estados,
        }

    def _forcar_miss(self) -> None:
        """Nova geração = todas as listagens são cache miss."""
        cache_service.invalidar_listagem()

    def _forcar_miss_detalhe(self) -> None:
        """Apaga o detalhe para a próxima leitura ser um miss."""
        cache_service.invalidar_detalhe(Item.objects.order_by("id").first().id)

    def _ciclo_de_invalidação(self, cliente: Client, url: str) -> Dict[str, Any]:
        """MISS -> HIT -> invalidação -> MISS, com o estado lido a cada passo.

        É o teste comportamental do cache: os dois primeiros passos mostram que
        a segunda leitura não foi ao banco, e o terceiro mostra que a escrita
        (aqui, a invalidação) realmente jogou fora o que estava em cache.
        """
        self._limpar_cache()
        passos = []

        def registrar(rotulo: str) -> None:
            resposta = cliente.get(url)
            passos.append(
                {
                    "passo": rotulo,
                    "x_cache": resposta.headers.get("X-Cache"),
                    "count": resposta.json().get("count"),
                }
            )

        registrar("cache vazio")
        registrar("repetida")
        cache_service.invalidar_listagem()
        registrar("após invalidação")
        return {"passos": passos}

    @staticmethod
    def _limpar_cache() -> None:
        cache_service.limpar_namespace()

    # ------------------------------------------------------------------
    # saída
    # ------------------------------------------------------------------
    def _imprimir(self, cenario: Dict[str, Any]) -> None:
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Benchmark do cache - {Item.objects.count()} itens no catálogo, "
                f"TTL listagem {settings.CACHE_TTL_LISTA}s / detalhe "
                f"{settings.CACHE_TTL_DETALHE}s"
            )
        )
        cabecalho = f"{'cenário':<24}{'n':>5}{'média':>10}{'p95':>10}{'RPS':>9}  X-Cache"
        self.stdout.write(cabecalho)
        self.stdout.write("-" * len(cabecalho))

        for nome, dados in cenario.items():
            if "passos" in dados:
                continue
            estados = ", ".join(f"{k}={v}" for k, v in dados["x_cache"].items())
            self.stdout.write(
                f"{nome:<24}{dados['requisicoes']:>5}{dados['media_ms']:>9.2f}ms"
                f"{dados['p95_ms']:>9.2f}ms{dados['rps']:>9.1f}  {estados}"
            )

        self.stdout.write(self.style.MIGRATE_HEADING("Ganho do cache"))
        for recurso in ("listagem", "detalhe"):
            base = cenario.get(f"{recurso}_sem_cache", {}).get("media_ms")
            hit = cenario.get(f"{recurso}_hit", {}).get("media_ms")
            if base and hit:
                self.stdout.write(
                    f"  {recurso:<9}: {base:.2f}ms (banco) -> {hit:.2f}ms (HIT) = "
                    f"{base / hit:.1f}x mais rápido"
                )

        self.stdout.write(self.style.MIGRATE_HEADING("Ciclo de invalidação"))
        for passo in cenario["ciclo_de_invalidação"]["passos"]:
            self.stdout.write(
                f"  {passo['passo']:<20} X-Cache={passo['x_cache']:<8} "
                f"count={passo['count']}"
            )


def _percentil(valores: List[float], percentual: int) -> float:
    """Percentil por ordem (não precisa de numpy nem de SciPy)."""
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, int(round(percentual / 100 * len(ordenados))) - 1)
    return ordenados[max(indice, 0)]


class _nulo:
    """Contexto vazio, para não repetir o `if` do `override_settings`."""

    def __enter__(self):
        return None

    def __exit__(self, *_):
        return False
