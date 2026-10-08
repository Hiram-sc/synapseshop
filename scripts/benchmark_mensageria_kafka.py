#!/usr/bin/env python
"""Benchmark da mensageria Kafka da Aula 10.

Mede, contra o ambiente real (API + Apache Kafka + worker), os tempos de
execução que a spec pede observar, com os trade-offs entre dedupe, latência e
throughput:

* **Produção isolada (tópico temporário)** - quanto custa `produce + flush`
  até a confirmação do broker, sem API e sem worker. Isola o custo do
  produtor/broker Kafka do restante;
* **Ponta a ponta (POST -> tópico -> worker -> confirmado)** - latência da
  API (cria + publica) e do consumo observado pelo cliente, e o throughput de
  pedidos/s no fluxo completo;
* **Trade-off do dedupe** - o mesmo `ja_processado()` com o caminho rápido
  (Redis, TTL) e com o fallback (PostgreSQL), mais o custo de `registrar()`
  (durabilidade banco + Redis). É o preço da idempotência.

Como rodar (de dentro da rede do Compose, com API e worker no ar):

    docker compose exec api python scripts/benchmark_mensageria_kafka.py
    docker compose exec api python scripts/benchmark_mensageria_kafka.py --n 20 --json

Não é suíte de testes: é coleta de métrica sob demanda (mesma disciplina da
Aula 9). Os números são de uma máquina de desenvolvimento - servem para mostrar
a ordem de grandeza e o formato do relatório, não para prometer latência de
produção.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

from confluent_kafka import Producer  # noqa: E402
from confluent_kafka.admin import AdminClient, NewTopic  # noqa: E402

from services.mensageria import config  # noqa: E402

BASE_URL = os.environ.get("BENCH_BASE_URL", "http://localhost:8000").rstrip("/")
USUARIO = os.environ.get("BENCH_USUARIO", "admin")
SENHA = os.environ.get("BENCH_SENHA", "admin123")

ESPERA_CONSUMO_S = float(os.environ.get("BENCH_ESPERA_CONSUMO_S", "60"))
TICK_S = 0.25

TOPICO_TEMP = "benchmark.produtor"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _http(
    caminho: str,
    *,
    metodo: str = "GET",
    corpo: Optional[dict] = None,
    token: Optional[str] = None,
    timeout: float = 10.0,
) -> Tuple[int, Any]:
    url = f"{BASE_URL}{caminho}"
    data = None
    cabecalhos = {"Accept": "application/json"}
    if corpo is not None:
        data = json.dumps(corpo).encode("utf-8")
        cabecalhos["Content-Type"] = "application/json"
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    requisicao = urllib.request.Request(
        url, data=data, headers=cabecalhos, method=metodo
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
            bruto = resposta.read().decode("utf-8")
            status = resposta.status
    except urllib.error.HTTPError as erro:
        bruto = erro.read().decode("utf-8", "replace")
        status = erro.code
    if not bruto:
        return status, None
    try:
        return status, json.loads(bruto)
    except ValueError:
        return status, bruto


def api(caminho, *, metodo="GET", corpo=None, token=None):
    return _http(caminho, metodo=metodo, corpo=corpo, token=token)


def login() -> str:
    status, dados = api(
        "/api/v1/auth/login/",
        metodo="POST",
        corpo={"username": USUARIO, "password": SENHA},
    )
    if status != 200 or not isinstance(dados, dict) or "access" not in dados:
        raise SystemExit(f"login falhou: status={status} corpo={dados}")
    return dados["access"]


def garantir_item(token: str) -> int:
    status, dados = api("/api/v1/items/?is_active=true&limit=1", token=token)
    if status == 200 and isinstance(dados, dict) and dados.get("results"):
        return dados["results"][0]["id"]
    status, categoria = api(
        "/api/v1/categories/",
        metodo="POST",
        corpo={"name": f"Bench {uuid.uuid4().hex[:8]}"},
        token=token,
    )
    if status != 201 or not isinstance(categoria, dict):
        raise SystemExit(f"criação de categoria falhou: {status} {categoria}")
    status, item = api(
        "/api/v1/items/",
        metodo="POST",
        corpo={
            "name": f"Item bench {uuid.uuid4().hex[:8]}",
            "price": "10.00",
            "category": categoria["id"],
            "is_active": True,
        },
        token=token,
    )
    if status != 201 or not isinstance(item, dict):
        raise SystemExit(f"criação de item falhou: {status} {item}")
    return item["id"]


def criar_pedido(token, item_id):
    return api(
        "/api/v1/pedidos/",
        metodo="POST",
        corpo={
            "idempotency_key": f"bench-{uuid.uuid4()}",
            "itens": [{"item_id": item_id, "quantidade": 2}],
        },
        token=token,
    )


def aguardar_confirmado(token: str, pedido_id: int) -> float:
    inicio = time.monotonic()
    while time.monotonic() - inicio < ESPERA_CONSUMO_S:
        status, corpo = api(f"/api/v1/pedidos/{pedido_id}/", token=token)
        if status == 200 and isinstance(corpo, dict) and corpo.get("status") == "confirmado":
            return (time.monotonic() - inicio) * 1000
        time.sleep(TICK_S)
    raise RuntimeError(f"pedido {pedido_id} não confirmado em {ESPERA_CONSUMO_S}s")


# ---------------------------------------------------------------------------
# Kafka
# ---------------------------------------------------------------------------
def kafka_conf(sufixo: str) -> Dict[str, Any]:
    return {
        "bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS,
        "security.protocol": config.KAFKA_SECURITY_PROTOCOL,
        "client.id": f"bench-{sufixo}",
    }


def _produtor() -> Producer:
    return Producer(
        {
            **kafka_conf("produtor"),
            "acks": config.KAFKA_ACKS,
            "retries": config.KAFKA_RETRIES,
            "linger.ms": config.KAFKA_LINGER_MS,
        }
    )


def criar_topo_temp() -> None:
    admin = AdminClient(kafka_conf("admin"))
    futuros = admin.create_topics(
        [NewTopic(TOPICO_TEMP, num_partitions=1, replication_factor=1)]
    )
    for topico, futuro in futuros.items():
        try:
            futuro.result(15)
        except Exception as erro:  # noqa: BLE001 - já existia é aceitável
            if "already exists" not in str(erro).lower():
                raise


def remover_topo_temp() -> None:
    try:
        admin = AdminClient(kafka_conf("admin"))
        futuros = admin.delete_topics([TOPICO_TEMP])
        for _, futuro in futuros.items():
            futuro.result(10)
    except Exception:  # noqa: BLE001 - limpeza não deve derrubar o relatório
        pass


# ---------------------------------------------------------------------------
# mediações
# ---------------------------------------------------------------------------
def _estatisticas(tempos: List[float], rotulo: str) -> Dict[str, Any]:
    return {
        "rotulo": rotulo,
        "n": len(tempos),
        "media_ms": round(statistics.fmean(tempos), 3),
        "mediana_ms": round(statistics.median(tempos), 3),
        "p95_ms": round(_percentil(tempos, 95), 3),
        "min_ms": round(min(tempos), 3),
        "max_ms": round(max(tempos), 3),
    }


def medir_producao(n: int) -> Dict[str, Any]:
    """Produce+flush por mensagem, num tópico temporário (sem API/worker)."""
    criar_topo_temp()
    produtor = _produtor()
    tempos: List[float] = []
    try:
        for _ in range(n):
            inicio = time.perf_counter()
            produtor.produce(
                TOPICO_TEMP,
                value=os.urandom(64),
                key=os.urandom(8),
                headers=[(config.HEADER_RETRY, b"0")],
            )
            produtor.flush(15.0)  # confirmação do broker = latência real
            tempos.append((time.perf_counter() - inicio) * 1000)
    finally:
        remover_topo_temp()
    return _estatisticas(tempos, f"produce+flush (tópico temporário, n={n})")


def medir_ponta_a_ponta(token: str, item_id: int, n: int) -> Tuple[Dict[str, Any], float]:
    """N pedidos reais; mede API (POST) e consumo (até confirmado)."""
    tempos_post: List[float] = []
    tempos_consumo: List[float] = []
    inicio_total = time.monotonic()
    for _ in range(n):
        t0 = time.perf_counter()
        status, corpo = criar_pedido(token, item_id)
        tempos_post.append((time.perf_counter() - t0) * 1000)
        if status != 201 or not isinstance(corpo, dict):
            raise RuntimeError(f"POST devolveu {status}: {corpo}")
        pedido = (corpo.get("pedido") or {}).get("id")
        if not pedido:
            raise RuntimeError("POST 201 sem pedido no corpo")
        tempos_consumo.append(aguardar_confirmado(token, pedido))
    total_s = time.monotonic() - inicio_total
    return {
        "post": _estatisticas(tempos_post, f"POST pedido (n={n})"),
        "consumo": _estatisticas(tempos_consumo, f"publicado->confirmado (n={n})"),
        "throughput_pedidos_s": round(n / total_s, 2),
        "janela_s": round(total_s, 2),
    }, total_s


def medir_dedupe(
    n_rapido: int, n_fallback: int, n_registro: int
) -> Dict[str, Any]:
    """Trade-off do `ja_processado`: Redis (rápido) vs PostgreSQL (fallback)."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()

    from django.core.cache import cache

    from services.mensageria import idempotencia
    from repositories.models import EventoProcessado

    chaves_limpar: List[str] = []

    def _chave_redis(event_type: str, idempotency_key: str) -> str:
        return f"{config.PREFIXO_IDEMPOTENCIA}:{event_type}:{idempotency_key}"

    def _correr(funcao: Callable[[], None], n: int) -> List[float]:
        tempos: List[float] = []
        for _ in range(n):
            inicio = time.perf_counter()
            funcao()
            tempos.append((time.perf_counter() - inicio) * 1000000)  # µs
        return tempos

    tipo = f"Benchmark{uuid.uuid4().hex[:6]}"

    try:
        # caminho rápido: chave previamente registrada -> HIT no Redis
        def _hit() -> None:
            cache.set(_chave_redis(tipo, "h"), "bench", timeout=config.TTL_IDEMPOTENCIA_S)
            idempotencia.ja_processado(tipo, "h")

        rapido = _correr(_hit, n_rapido)

        # fallback: sem Redis e sem registro no banco -> SELECT no PostgreSQL
        def _fallback() -> None:
            idempotencia.ja_processado(tipo, f"m-{random.randrange(1_000_000)}")

        lento = _correr(_fallback, n_fallback)

        # registro: durabilidade (INSERT banco) + caminho rápido (Redis)
        def _registrar(indice: int) -> None:
            chave = f"r-{indice}"
            idempotencia.registrar(tipo, chave, event_id=str(uuid.uuid4()))
            chaves_limpar.append(chave)

        registros: List[float] = []
        for indice in range(n_registro):
            inicio = time.perf_counter()
            _registrar(indice)
            registros.append((time.perf_counter() - inicio) * 1000000)
    finally:
        try:
            EventoProcessado.objects.filter(event_type=tipo).delete()
        except Exception:  # noqa: BLE001 - limpeza
            pass
        for chave in chaves_limpar:
            try:
                cache.delete(_chave_redis(tipo, chave))
            except Exception:  # noqa: BLE001
                pass

    mediana_rapido = statistics.median(rapido)
    mediana_lento = statistics.median(lento)
    return {
        "caminho_rapido_redis": _estatisticas(
            rapido, "ja_processado() com chave no Redis (HIT)"
        ),
        "fallback_postgres": _estatisticas(
            lento, "ja_processado() sem Redis (SELECT PostgreSQL)"
        ),
        "registro": {
            "rotulo": "registrar() (INSERT banco + Redis)",
            "n": n_registro,
            "media_us": round(statistics.fmean(registros), 1),
            "mediana_us": round(statistics.median(registros), 1),
            "p95_us": round(_percentil(registros, 95), 1),
        },
        "proporcao_fallback_rapido": (
            round(mediana_lento / mediana_rapido, 1) if mediana_rapido else None
        ),
    }


# ---------------------------------------------------------------------------
# saída
# ---------------------------------------------------------------------------
def _imprimir(valores: List[Dict[str, Any]]) -> None:
    cabecalho = f"{'métrica':<42}{'n':>5}{'média':>10}{'mediana':>10}{'p95':>10}{'min':>10}{'max':>10}"
    print(cabecalho)
    print("-" * len(cabecalho))
    for d in valores:
        print(
            f"{d['rotulo']:<42}{d['n']:>5}{d['media_ms']:>9.2f}ms"
            f"{d['mediana_ms']:>9.2f}ms{d['p95_ms']:>9.2f}ms"
            f"{d['min_ms']:>9.2f}ms{d['max_ms']:>9.2f}ms"
        )


def _percentil(valores: List[float], percentual: int) -> float:
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, int(round(percentual / 100 * len(ordenados))) - 1)
    return ordenados[max(indice, 0)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=30, help="Pedidos no fluxo ponta a ponta.")
    parser.add_argument("--n-producao", type=int, default=100, help="Mensagens no teste de produção.")
    parser.add_argument("--no-dedupe", action="store_true", help="Pula a medição do dedupe.")
    parser.add_argument("--json", action="store_true", help="Devolve o resultado em JSON.")
    args = parser.parse_args()

    print(f"API: {BASE_URL} | Kafka: {config.KAFKA_BOOTSTRAP_SERVERS}")

    relatorio: Dict[str, Any] = {}

    token = login()
    item_id = garantir_item(token)

    print("\n[1] Produção isolada")
    producao = medir_producao(args.n_producao)
    relatorio["producao_isolada"] = producao

    print("\n[2] Ponta a ponta")
    e2e, janela = medir_ponta_a_ponta(token, item_id, args.n)
    relatorio["ponta_a_ponta"] = e2e

    if not args.no_dedupe:
        print("\n[3] Trade-off do dedupe")
        dedupe = medir_dedupe(n_rapido=args.n, n_fallback=args.n, n_registro=min(args.n, 20))
        relatorio["dedupe"] = dedupe

    if args.json:
        print(json.dumps(relatorio, indent=2, ensure_ascii=False))
        return 0

    print("\n" + "=" * 75)
    print("PRODUÇÃO ISOLADA (broker/produtor, sem API e sem worker)")
    _imprimir([producao])

    print("\nPONTA A PONTA (POST -> tópico -> worker -> confirmado)")
    _imprimir([e2e["post"], e2e["consumo"]])
    print(
        f"  throughput = {e2e['throughput_pedidos_s']} pedidos/s "
        f"({args.n} pedidos em {e2e['janela_s']}s)"
    )

    if not args.no_dedupe:
        print("\nTRADE-OFF DO DEDUPE (µs)")
        for bloco in (dedupe["caminho_rapido_redis"], dedupe["fallback_postgres"], dedupe["registro"]):
            print(
                f"  {bloco['rotulo']:<46}{bloco.get('n', '')}"
                f"  média {bloco.get('media_ms', bloco.get('media_us')):>9} "
                f"mediana {bloco.get('mediana_ms', bloco.get('mediana_us')):>9} "
                f"p95 {bloco.get('p95_ms', bloco.get('p95_us')):>9}"
            )
        proporcao = dedupe["proporcao_fallback_rapido"]
        if proporcao is not None:
            print(
                f"  fallback PostgreSQL é ~{proporcao}x mais lento que o "
                "caminho rápido Redis (a idempotência custa pouco quando a "
                "chave está no cache)"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())