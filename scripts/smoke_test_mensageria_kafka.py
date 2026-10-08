#!/usr/bin/env python
"""Smoke test da mensageria Kafka da Aula 10.

Valida, contra o ambiente real (API + Apache Kafka + worker), o que a spec
pede comprovar nesta aula:

* criação normal de pedido e publicação do `PedidoCriado` no tópico;
* consumo assíncrono (o worker muda o status para `confirmado`);
* chave de partição = `idempotency_key`;
* topologia: `pedidos.pedidocriado` com 3 partições e `pedidos.pedidocriado.dlq`
  com 1 partição, com retenção de 7/28 dias;
* commit de offset **depois** do processamento (lag = 0 com
  `enable.auto.commit=false`);
* idempotência do POST (mesma `idempotency_key` não cria outro pedido);
* idempotência do consumo (republicar o mesmo evento não repete o efeito);
* com `--falha`: retry com backoff, republicação com `x-retry-count` e entrada
  na DLQ (o worker precisa estar iniciado com `PEDIDO_WORKER_FALHA_IDEM_KEYS`).

Como rodar (de dentro da rede do Compose):

    docker compose exec api python scripts/smoke_test_mensageria_kafka.py
    docker compose exec api python scripts/smoke_test_mensageria_kafka.py --falha

Sem suíte automatizada: é validação pontual do fluxo, executada sob demanda
(mesma disciplina da Aula 9).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple

# executado como `python scripts/smoke_test_mensageria_kafka.py`, o `sys.path[0]`
# é a pasta `scripts/`; a raiz do projeto entra aqui para os imports de
# `services.mensageria` (config/envio de envelope) funcionarem
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

from services.mensageria import config  # noqa: E402

from confluent_kafka import Consumer, Producer, TopicPartition  # noqa: E402
from confluent_kafka.admin import (  # noqa: E402
    AdminClient,
    ConfigResource,
    NewTopic,
    ResourceType,
)

BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://localhost:8000").rstrip("/")
USUARIO = os.environ.get("SMOKE_USUARIO", "admin")
SENHA = os.environ.get("SMOKE_SENHA", "admin123")

ESPERA_CONSUMO_S = float(os.environ.get("SMOKE_ESPERA_CONSUMO_S", "20"))
TICK_S = 0.25

resultados: List[Tuple[str, bool, str]] = []


def registrar(nome: str, ok: bool, detalhe: str = "") -> None:
    resultados.append((nome, ok, detalhe))
    marca = "PASS" if ok else "FAIL"
    print(f"  [{marca}] {nome}" + (f" — {detalhe}" if detalhe else ""))


def kafka_conf(sufixo: str) -> Dict[str, Any]:
    return {
        "bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS,
        "security.protocol": config.KAFKA_SECURITY_PROTOCOL,
        "client.id": f"smoke-{sufixo}",
    }


def _admin() -> AdminClient:
    return AdminClient(kafka_conf("admin"))


def grupo_do_worker() -> str:
    """group.id do worker: o mesmo do compose, sobreponível pelo ambiente."""
    return os.environ.get("KAFKA_GROUP_ID") or config.KAFKA_GROUP_ID


def _produtor() -> Producer:
    return Producer(
        {**kafka_conf("produtor"), "acks": "all", "retries": 3, "linger.ms": 5}
    )


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
    print(f"login OK como '{USUARIO}'")
    return dados["access"]


def garantir_item(token: str) -> int:
    status, dados = api("/api/v1/items/?is_active=true&limit=1", token=token)
    if status == 200 and isinstance(dados, dict) and dados.get("results"):
        return dados["results"][0]["id"]
    print("  catálogo vazio; criando categoria e item para o smoke test")
    status, categoria = api(
        "/api/v1/categories/", metodo="POST", corpo={"name": f"Smoke {uuid.uuid4().hex[:8]}"}, token=token
    )
    if status != 201 or not isinstance(categoria, dict):
        raise SystemExit(f"criação de categoria falhou: {status} {categoria}")
    status, item = api(
        "/api/v1/items/",
        metodo="POST",
        corpo={
            "name": f"Item smoke {uuid.uuid4().hex[:8]}",
            "price": "10.00",
            "category": categoria["id"],
            "is_active": True,
        },
        token=token,
    )
    if status != 201 or not isinstance(item, dict):
        raise SystemExit(f"criação de item falhou: {status} {item}")
    return item["id"]


def criar_pedido(token, item_id, idempotency_key, quantidade=2):
    return api(
        "/api/v1/pedidos/",
        metodo="POST",
        corpo={
            "idempotency_key": idempotency_key,
            "itens": [{"item_id": item_id, "quantidade": quantidade}],
        },
        token=token,
    )


def aguardar_consumo(token: str, pedido_id: int) -> Tuple[Optional[Dict], float]:
    inicio = time.monotonic()
    while time.monotonic() - inicio < ESPERA_CONSUMO_S:
        status, corpo = api(f"/api/v1/pedidos/{pedido_id}/", token=token)
        if status == 200 and isinstance(corpo, dict):
            if corpo.get("status") == "confirmado":
                return corpo, time.monotonic() - inicio
        time.sleep(TICK_S)
    return None, time.monotonic() - inicio


# ---------------------------------------------------------------------------
# Kafka
# ---------------------------------------------------------------------------
def publicar_no_topico(
    topico: str, envelope: Dict[str, Any], *, chave: str, cabecalhos: List[tuple]
) -> bool:
    produtor = _produtor()
    entregue: List[Optional[Exception]] = []

    def _cb(erro, message) -> None:  # noqa: ARG001
        entregue.append(erro)

    try:
        produtor.produce(
            topic=topico,
            value=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            key=chave.encode("utf-8"),
            headers=cabecalhos,
            callback=_cb,
        )
        produtor.flush(15.0)
    except Exception as erro:  # noqa: BLE001
        print(f"    produção falhou: {erro}")
        return False
    return all(erro is None for erro in entregue)


def ler_mensagem_dlq(idempotency_key: str) -> Optional[Dict[str, Any]]:
    """Lê a DLQ (desde o início) e devolve a mensagem desta rodada, se houver."""
    consumidor = Consumer(
        {
            **kafka_conf("dlq-leitor"),
            "group.id": f"smoke-dlq-{uuid.uuid4().hex[:12]}",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    consumidor.subscribe([config.KAFKA_TOPIC_DLQ])
    try:
        prazo = time.monotonic() + ESPERA_CONSUMO_S
        while time.monotonic() < prazo:
            msg = consumidor.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                continue
            try:
                corpo = json.loads(msg.value().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(corpo, dict) and corpo.get("idempotency_key") == idempotency_key:
                return {"payload": corpo, "headers": msg.headers(), "key": (msg.key() or b"").decode("utf-8", "replace")}
        return None
    finally:
        consumidor.close()


def verificar_lag_zero():
    """Lag (high watermark - committed) por partição do tópico principal.

    Com `enable.auto.commit=false` e commit pós-processamento, depois de um
    consumo completo todos os offsets committados devem coincidir com o fim do
    log. Devolve a soma dos lags (0 = tudo confirmado), ou `None` se o broker
    não responder (a leitura de offsets é somente consulta no coordinator, sem
    entrar no grupo de consumo).
    """
    consumidor = Consumer(
        {
            **kafka_conf("lag"),
            "group.id": grupo_do_worker(),
            "enable.auto.commit": False,
            "auto.offset.reset": config.KAFKA_AUTO_OFFSET_RESET,
        }
    )
    try:
        topicos = _admin().list_topics(timeout=15)
        quantidade = len(topicos.topics.get(config.KAFKA_TOPIC_PEDIDO_CRIADO, {}).partitions or [])
        particoes = [
            TopicPartition(config.KAFKA_TOPIC_PEDIDO_CRIADO, p) for p in range(quantidade)
        ]
        commitados = {tp.partition: tp.offset for tp in consumidor.committed(particoes, timeout=15)}
        lag_total = 0
        for tp in particoes:
            baixo, alto = consumidor.get_watermark_offsets(tp, timeout=15)
            commitado = commitados.get(tp.partition, -1001)
            if commitado < 0:
                commitado = baixo
            lag = max(0, alto - commitado)
            lag_total += lag
            print(f"    partição {tp.partition}: committed={commitado} high={alto} lag={lag}")
        return lag_total
    except Exception as erro:  # noqa: BLE001
        print(f"    leitura de offsets indisponível: {type(erro).__name__}: {erro}")
        return None
    finally:
        consumidor.close()


def topologia_por_topicos() -> Dict[str, Dict[str, Any]]:
    admin = _admin()
    metadados = admin.list_topics(timeout=15)
    retencao = {}
    for nome in (config.KAFKA_TOPIC_PEDIDO_CRIADO, config.KAFKA_TOPIC_DLQ):
        recurso = ConfigResource(ResourceType.TOPIC, nome)
        futuros = admin.describe_configs([recurso])
        try:
            entradas = futuros[recurso].result(timeout=15)
            retencao[nome] = entradas.get("retention.ms").value if "retention.ms" in entradas else None
        except Exception:  # noqa: BLE001 - sem metadata, reporta None
            retencao[nome] = None
    return {
        config.KAFKA_TOPIC_PEDIDO_CRIADO: {
            "particoes": len(metadados.topics.get(config.KAFKA_TOPIC_PEDIDO_CRIADO, {}).partitions or []),
            "retencao_ms": retencao.get(config.KAFKA_TOPIC_PEDIDO_CRIADO),
        },
        config.KAFKA_TOPIC_DLQ: {
            "particoes": len(metadados.topics.get(config.KAFKA_TOPIC_DLQ, {}).partitions or []),
            "retencao_ms": retencao.get(config.KAFKA_TOPIC_DLQ),
        },
    }


# ---------------------------------------------------------------------------
# cenários
# ---------------------------------------------------------------------------
def cenario_topologia() -> None:
    print("\n[1] Topologia Kafka")
    admin = _admin()
    metadados = admin.list_topics(timeout=30)
    registrar(
        "broker respondendo ao list_topics",
        len(metadados.brokers) > 0,
        f"brokers={list(metadados.brokers.values())[:1]}",
    )
    detalhes = topologia_por_topicos()
    principal = detalhes[config.KAFKA_TOPIC_PEDIDO_CRIADO]
    dlq = detalhes[config.KAFKA_TOPIC_DLQ]
    registrar(
        f"tópico {config.KAFKA_TOPIC_PEDIDO_CRIADO} com 3 partições",
        principal["particoes"] == 3,
        f"partições={principal['particoes']}",
    )
    registrar(
        "retenção de 7 dias no tópico principal",
        principal["retencao_ms"] == str(config.KAFKA_RETENTION_MS_MAIN),
        f"retention.ms={principal['retencao_ms']}",
    )
    registrar(
        f"DLQ {config.KAFKA_TOPIC_DLQ} com 1 partição",
        dlq["particoes"] == 1,
        f"partições={dlq['particoes']}",
    )
    registrar(
        "retenção de 28 dias na DLQ",
        dlq["retencao_ms"] == str(config.KAFKA_RETENTION_MS_DLQ),
        f"retention.ms={dlq['retencao_ms']}",
    )


def cenario_normal(token: str, item_id: int) -> Optional[Dict[str, Any]]:
    print("\n[2] Cenário normal: criar -> publicar -> consumir")
    chave = f"smoke-k-{uuid.uuid4()}"
    t0 = time.monotonic()
    status, corpo = criar_pedido(token, item_id, chave)
    t_criacao = (time.monotonic() - t0) * 1000

    registrar("POST /api/v1/pedidos/ responde 201", status == 201, f"status={status}")
    registrar(
        "evento_publicado=true (Kafka confirmou)",
        corpo.get("evento_publicado") is True,
        f"{corpo.get('evento_publicado')} ({t_criacao:.0f} ms na API)",
    )

    pedido = corpo.get("pedido") or {}
    pedido_id = pedido.get("id")
    if not pedido_id:
        registrar("pedido criado com id", False, str(corpo))
        return None

    confirmado, espera = aguardar_consumo(token, pedido_id)
    registrar(
        "worker consumiu e confirmou o pedido",
        confirmado is not None,
        f"status={confirmado and confirmado.get('status')} em {espera * 1000:.0f} ms",
    )
    if confirmado:
        registrar(
            "processado_em preenchido pelo worker",
            bool(confirmado.get("processado_em")),
            str(confirmado.get("processado_em")),
        )
    return confirmado


def cenario_duplicacao(token, item_id, chave, pedido_id) -> None:
    print("\n[3] Duplicação do POST: mesma idempotency_key")
    status, corpo = criar_pedido(token, item_id, chave)
    registrar("POST repetido responde 200", status == 200, f"status={status}")
    registrar(
        "mesmo pedido devolvido (sem duplicar)",
        (corpo.get("pedido") or {}).get("id") == pedido_id,
        f"id={(corpo.get('pedido') or {}).get('id')} esperado={pedido_id}",
    )
    registrar(
        "evento não é republicado no POST repetido",
        corpo.get("evento_publicado") is False,
        str(corpo.get("evento_publicado")),
    )


def cenario_evento_duplicado(token, pedido_id, chave, item_id) -> None:
    print("\n[4] Evento duplicado no tópico: reentrega não repete o efeito")
    antes, _ = aguardar_consumo(token, pedido_id)
    if not antes:
        registrar("pedido já confirmado antes da republicação", False, "")
        return

    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_type": "PedidoCriado",
        "version": "1.0",
        "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "correlation_id": chave,
        "idempotency_key": chave,
        "dados": {
            "pedido": {
                "id": pedido_id,
                "usuario_id": 1,
                "status": "criado",
                "created_at": antes.get("created_at"),
                "total": antes.get("total"),
                "itens": [
                    {
                        "item_id": item_id,
                        "nome": (antes.get("itens") or [{}])[0].get("nome_item", ""),
                        "quantidade": (antes.get("itens") or [{}])[0].get("quantidade", 1),
                        "preco_unitario": (antes.get("itens") or [{}])[0].get("preco_unitario", "0.00"),
                        "subtotal": (antes.get("itens") or [{}])[0].get("subtotal", "0.00"),
                    }
                ],
            }
        },
    }
    ok = publicar_no_topico(
        config.KAFKA_TOPIC_PEDIDO_CRIADO,
        envelope,
        chave=chave,
        cabecalhos=[(config.HEADER_RETRY, b"0"), ("motivo", b"smoke-duplicado")],
    )
    registrar("republicação do evento duplicado aceita pelo broker", ok, "")

    time.sleep(2.0)
    depois, _ = aguardar_consumo(token, pedido_id)
    registrar(
        "efeito não repetido (processado_em idêntico)",
        bool(depois) and antes.get("processado_em") == depois.get("processado_em"),
        f"antes={antes.get('processado_em')} depois={depois and depois.get('processado_em')}",
    )


def cenario_commit() -> None:
    print("\n[5] Commit de offset após o processamento (enable.auto.commit=false)")
    lag = verificar_lag_zero()
    registrar(
        "offset commitado acompanhando o fim do log (lag = 0)",
        lag == 0,
        f"lag total={lag}",
    )


def cenario_falha(token, item_id) -> None:
    print("\n[6] Falha forçada -> retry -> backoff -> DLQ")
    print(
        "  (exige worker com PEDIDO_WORKER_FALHA_IDEM_KEYS='falha-*'; "
        "se a variável estiver vazia este cenário falha de propósito)"
    )
    chave = f"falha-{uuid.uuid4()}"
    t0 = time.monotonic()
    status, corpo = criar_pedido(token, item_id, chave)
    registrar(
        "pedido com chave de falha criado (201)",
        status == 201,
        f"status={status} chave={chave}",
    )
    pedido_id = (corpo.get("pedido") or {}).get("id")

    mensagem = ler_mensagem_dlq(chave)
    decorrido = time.monotonic() - t0
    registrar(
        "mensagem da rodada chegou à DLQ após exceder as tentativas",
        mensagem is not None,
        f"em {decorrido:.2f}s (mín. 1.75s = backoff 250+500+1000 ms)",
    )
    if mensagem is None:
        return

    retry_count = None
    for nome, valor in mensagem["headers"] or []:
        n = nome.decode("utf-8") if isinstance(nome, bytes) else nome
        if n == config.HEADER_RETRY:
            retry_count = int(valor.decode("utf-8"))
    registrar(
        "contador x-retry-count presente na mensagem morta",
        retry_count is not None,
        f"x-retry-count={retry_count}",
    )
    registrar(
        "payload da DLQ é o envelope PedidoCriado",
        mensagem["payload"].get("event_type") == "PedidoCriado",
        f"event_type={mensagem['payload'].get('event_type')}",
    )
    if pedido_id:
        status_det, detalhe = api(f"/api/v1/pedidos/{pedido_id}/", token=token)
        registrar(
            "pedido NÃO confirmado (efeito não ocorreu)",
            status_det == 200
            and isinstance(detalhe, dict)
            and detalhe.get("status") == "criado",
            f"status={detalhe.get('status') if isinstance(detalhe, dict) else detalhe}",
        )


def cenario_chave_particao() -> None:
    print("\n[7] Chave de partição = idempotency_key")
    admin = _admin()
    topico_tmp = "smoke.particao"
    futuros = admin.create_topics(
        [NewTopic(topico_tmp, num_partitions=3, replication_factor=1)]
    )
    try:
        for futuro in futuros.values():
            futuro.result(15)
    except Exception as erro:  # noqa: BLE001 - só "já existe" é aceitável
        if "already exists" not in str(erro).lower():
            raise
    produtor = _produtor()
    chave = f"particao-{uuid.uuid4()}"
    particoes = set()
    for _ in range(8):
        resultado: List[Optional[str]] = []

        def _cb(erro, message) -> None:
            resultado.append(f"{error_nome(erro)}|{message.partition()}")

        produtor.produce(
            topic=topico_tmp,
            value=b"{}",
            key=chave.encode("utf-8"),
            headers=[(config.HEADER_RETRY, b"0")],
            callback=_cb,
        )
        produtor.flush(15.0)
        if resultado and resultado[0] and resultado[0].split("|")[0] == "ok":
            particoes.add(int(resultado[0].split("|")[1]))
    registrar(
        "mesma idempotency_key cai sempre na mesma partição",
        len(particoes) == 1,
        f"partições observadas={sorted(particoes)} (em 3 partições)",
    )
    try:
        limpeza = admin.delete_topics([topico_tmp])
        for lmpeza in limpeza.values():
            lmpeza.result(10)
    except Exception:  # noqa: BLE001 - limpeza não deve derrubar o smoke
        pass


def error_nome(erro) -> str:
    return str(erro) if erro is not None else "ok"


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--falha",
        action="store_true",
        help="roda também o cenário de falha forçada -> DLQ",
    )
    args = parser.parse_args()

    print(f"API: {BASE_URL} | Kafka: {config.KAFKA_BOOTSTRAP_SERVERS}")

    try:
        token = login()
        item_id = garantir_item(token)
        print(f"item ativo usado no teste: {item_id}")

        cenario_topologia()

        pedido = cenario_normal(token, item_id)
        if pedido:
            chave = pedido["idempotency_key"]
            cenario_duplicacao(token, item_id, chave, pedido["id"])
            cenario_evento_duplicado(token, pedido["id"], chave, item_id)

        cenario_commit()
        cenario_chave_particao()

        if args.falha:
            cenario_falha(token, item_id)
    except SystemExit:
        raise
    except Exception as erro:  # noqa: BLE001
        registrar("execução do smoke test", False, f"{type(erro).__name__}: {erro}")

    print("\n" + "=" * 60)
    for nome, ok, detalhe in resultados:
        if not ok:
            print(f"FALHA: {nome} — {detalhe}")
    ok_count = sum(1 for _, ok, _ in resultados if ok)
    print(f"{ok_count}/{len(resultados)} verificações passaram")
    return 1 if ok_count < len(resultados) else 0


if __name__ == "__main__":
    sys.exit(main())