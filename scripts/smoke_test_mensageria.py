#!/usr/bin/env python
"""Smoke test da mensageria da Aula 9.

Valida, contra o ambiente real (API + RabbitMQ + worker), exatamente o que a
spec pede comprovar:

* criação normal de pedido e publicação do `PedidoCriado`;
* consumo assíncrono (o worker muda o status para `confirmado`);
* idempotência do POST (mesma `idempotency_key` não cria outro pedido);
* idempotência do consumo (republicar o mesmo evento não repete o efeito);
* topologia (exchange, fila, DLQ sem consumidor);
* com `--falha`: retry com backoff e entrada na DLQ, usando a falha
  forçada `PEDIDO_WORKER_FALHA_IDEM_KEYS` (o worker precisa estar iniciado
  com essa variável - ver README).

Não é suíte de testes automatizados: é uma validação pontual do fluxo de
mensageria, executada sob demanda.

Como rodar (de dentro da rede do Compose):

    docker compose exec api python scripts/smoke_test_mensageria.py
    docker compose exec api python scripts/smoke_test_mensageria.py --falha

Acesso à Management API: as respostas são **desserializadas com `json.loads`
antes de qualquer `.get()`** - ler o corpo cru como texto e chamar `.get()`
foi um defeito histórico deste projeto.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple

# executado como `python scripts/smoke_test_mensageria.py`, o `sys.path[0]`
# é a pasta `scripts/`; a raiz do projeto entra aqui para que o script possa
# importar `services.mensageria` (publicação da republicação duplicada)
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

# dentro do container da API: localhost:8000 é o próprio serviço
BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://localhost:8000").rstrip("/")
# dentro da rede do Compose o broker se chama `rabbitmq`
MGMT_URL = os.environ.get(
    "SMOKE_RABBITMQ_MANAGEMENT", "http://rabbitmq:15672"
).rstrip("/")
MGMT_USER = os.environ.get("SMOKE_RABBITMQ_USER", os.environ.get("RABBITMQ_USER", "synapseshop"))
MGMT_PASS = os.environ.get(
    "SMOKE_RABBITMQ_PASSWORD", os.environ.get("RABBITMQ_PASSWORD", "synapseshop")
)

USUARIO = os.environ.get("SMOKE_USUARIO", "admin")
SENHA = os.environ.get("SMOKE_SENHA", "admin123")

VHOST = "%2F"  # vhost padrão, urlencoded
EXCHANGE = os.environ.get("PEDIDO_EXCHANGE", "pedidos.events")
ROUTING_KEY = os.environ.get("PEDIDO_ROUTING_KEY", "pedidos.pedidocriado")
QUEUE = os.environ.get("PEDIDO_QUEUE", "pedidos.pedidocriado")
DLQ = os.environ.get("PEDIDO_DLQ", "pedidos.pedidocriado.dlq")

ESPERA_CONSUMO_S = float(os.environ.get("SMOKE_ESPERA_CONSUMO_S", "20"))
TICK_S = 0.25

resultados: List[Tuple[str, bool, str]] = []


def registrar(nome: str, ok: bool, detalhe: str = "") -> None:
    resultados.append((nome, ok, detalhe))
    marca = "PASS" if ok else "FAIL"
    print(f"  [{marca}] {nome}" + (f" — {detalhe}" if detalhe else ""))


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _http(
    caminho: str,
    *,
    metodo: str = "GET",
    corpo: Optional[dict] = None,
    token: Optional[str] = None,
    base: str = "",
    auth_basica: Optional[Tuple[str, str]] = None,
    timeout: float = 10.0,
) -> Tuple[int, Any]:
    """Requisição HTTP; devolve (status, JSON já desserializado ou None)."""
    url = f"{base}{caminho}"
    data = None
    cabecalhos = {"Accept": "application/json"}
    if corpo is not None:
        data = json.dumps(corpo).encode("utf-8")
        cabecalhos["Content-Type"] = "application/json"
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    if auth_basica:
        credencial = base64.b64encode(
            f"{auth_basica[0]}:{auth_basica[1]}".encode("utf-8")
        ).decode("ascii")
        cabecalhos["Authorization"] = f"Basic {credencial}"

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
        # SEMPRE desserializar antes de acessar atributos (defeito histórico:
        # chamar .get() na string crua levava a AttributeError)
        return status, json.loads(bruto)
    except ValueError:
        return status, bruto


def api(caminho: str, *, metodo: str = "GET", corpo: Optional[dict] = None,
        token: Optional[str] = None) -> Tuple[int, Any]:
    return _http(caminho, metodo=metodo, corpo=corpo, token=token, base=BASE_URL)


def mq(caminho: str, *, metodo: str = "GET", corpo: Optional[dict] = None):
    return _http(
        caminho,
        metodo=metodo,
        corpo=corpo,
        base=MGMT_URL,
        auth_basica=(MGMT_USER, MGMT_PASS),
    )


# ---------------------------------------------------------------------------
# passos
# ---------------------------------------------------------------------------
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
    """Devolve o id de um item ativo; cria catálogo mínimo se não houver."""
    status, dados = api("/api/v1/items/?is_active=true&limit=1", token=token)
    if status == 200 and isinstance(dados, dict) and dados.get("results"):
        return dados["results"][0]["id"]

    print("  catálogo vazio; criando categoria e item para o smoke test")
    status, categoria = api(
        "/api/v1/categories/",
        metodo="POST",
        corpo={"name": f"Smoke {uuid.uuid4().hex[:8]}"},
        token=token,
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


def criar_pedido(
    token: str, item_id: int, idempotency_key: str, quantidade: int = 2
) -> Tuple[int, Dict[str, Any]]:
    status, corpo = api(
        "/api/v1/pedidos/",
        metodo="POST",
        corpo={
            "idempotency_key": idempotency_key,
            "itens": [{"item_id": item_id, "quantidade": quantidade}],
        },
        token=token,
    )
    return status, corpo if isinstance(corpo, dict) else {}


def aguardar_consumo(token: str, pedido_id: int) -> Tuple[Optional[Dict], float]:
    """Aguarda o worker confirmar o consumo; devolve o pedido e o tempo (s)."""
    inicio = time.monotonic()
    while time.monotonic() - inicio < ESPERA_CONSUMO_S:
        status, corpo = api(f"/api/v1/pedidos/{pedido_id}/", token=token)
        if status == 200 and isinstance(corpo, dict):
            if corpo.get("status") == "confirmado":
                return corpo, time.monotonic() - inicio
        time.sleep(TICK_S)
    return None, time.monotonic() - inicio


def fila(nome: str) -> Dict[str, Any]:
    status, dados = mq(f"/api/queues/{VHOST}/{nome}")
    if status != 200 or not isinstance(dados, dict):
        raise SystemExit(f"management API não respondeu para {nome}: {status}")
    return dados


def mensagens_dlq(quantidade: int = 5) -> List[Dict[str, Any]]:
    status, dados = mq(
        f"/api/queues/{VHOST}/{DLQ}/get",
        metodo="POST",
        corpo={
            "count": quantidade,
            "ackmode": "ack_requeue_true",  # só lê; a mensagem continua na DLQ
            "encoding": "auto",
        },
    )
    if status != 200 or not isinstance(dados, list):
        raise SystemExit(f"leitura da DLQ falhou: status={status} corpo={dados}")
    return dados


def publicar_duplicado(envelope: Dict[str, Any]) -> None:
    """Republica o envelope no exchange, simulando uma reentrega do broker."""
    import pika

    from services.mensageria import config, topologia

    conexao = pika.BlockingConnection(config.parametros_conexao())
    try:
        canal = conexao.channel()
        topologia.declarar_topologia(canal)
        canal.confirm_delivery()
        canal.basic_publish(
            exchange=EXCHANGE,
            routing_key=ROUTING_KEY,
            body=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,
                headers={"x-retry-count": 0},
            ),
            mandatory=True,
        )
    finally:
        conexao.close()


# ---------------------------------------------------------------------------
# cenários
# ---------------------------------------------------------------------------
def cenario_normal(token: str, item_id: int) -> Optional[Dict[str, Any]]:
    print("\n[1] Cenário normal: criar -> publicar -> consumir")
    chave = f"smoke-{uuid.uuid4()}"
    t0 = time.monotonic()
    status, corpo = criar_pedido(token, item_id, chave)
    t_criacao = (time.monotonic() - t0) * 1000

    registrar(
        "POST /api/v1/pedidos/ responde 201",
        status == 201,
        f"status={status}",
    )
    registrar(
        "evento_publicado=true (broker confirmou)",
        corpo.get("evento_publicado") is True,
        f"{corpo.get('evento_publicado')} ({t_criacao:.0f} ms na API)",
    )

    pedido = corpo.get("pedido") or {}
    pedido_id = pedido.get("id")
    if not pedido_id:
        registrar("pedido criado com id", False, str(corpo))
        return None

    # o total é do servidor, nunca do cliente
    status_det, detalhe = api(f"/api/v1/pedidos/{pedido_id}/", token=token)
    if status_det == 200 and isinstance(detalhe, dict):
        esperado = sum(
            float(l["subtotal"]) for l in detalhe.get("itens", [])
        )
        registrar(
            "total calculado no servidor = soma dos subtotais",
            abs(float(detalhe.get("total", 0)) - esperado) < 0.001,
            f"total={detalhe.get('total')} soma={esperado:.2f}",
        )

    confirmado, espera = aguardar_consumo(token, pedido_id)
    registrar(
        "worker consumiu e confirmou o pedido",
        confirmado is not None,
        f"status={confirmado and confirmado.get('status')} "
        f"em {espera * 1000:.0f} ms",
    )
    if confirmado:
        registrar(
            "processado_em preenchido pelo worker",
            bool(confirmado.get("processado_em")),
            str(confirmado.get("processado_em")),
        )
        registrar(
            "duração do consumo dentro da espera",
            espera < ESPERA_CONSUMO_S,
            f"{espera * 1000:.0f} ms (limite {ESPERA_CONSUMO_S * 1000:.0f} ms)",
        )
    return confirmado


def cenario_duplicacao(token: str, item_id: int, chave: str,
                       pedido_id: int) -> None:
    print("\n[2] Duplicação do POST: mesma idempotency_key")
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


def cenario_evento_duplicado(
    token: str, pedido_id: int, chave: str, item_id: int
) -> None:
    print("\n[3] Evento duplicado na fila: republicação não repete o efeito")
    antes, _ = aguardar_consumo(token, pedido_id)
    if not antes:
        registrar("pedido já confirmado antes da republicação", False, "")
        return

    envelope = {
        "event_id": str(uuid.uuid4()),  # event_id novo, mesma chave de negócio
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
                        "nome": (antes.get("itens") or [{}])[0].get(
                            "nome_item", ""
                        ),
                        "quantidade": (antes.get("itens") or [{}])[0].get(
                            "quantidade", 1
                        ),
                        "preco_unitario": (antes.get("itens") or [{}])[0].get(
                            "preco_unitario", "0.00"
                        ),
                        "subtotal": (antes.get("itens") or [{}])[0].get(
                            "subtotal", "0.00"
                        ),
                    }
                ],
            }
        },
    }

    try:
        publicar_duplicado(envelope)
    except Exception as erro:  # noqa: BLE001
        registrar("republicação do evento duplicado", False, str(erro))
        return

    # dá tempo ao worker de consumir e, se fosse duplicar, de alterar o pedido
    time.sleep(2.0)
    depois, _ = aguardar_consumo(token, pedido_id)
    registrar(
        "republicação confirmada pelo broker",
        True,
        "mensagem publicada no exchange",
    )
    registrar(
        "efeito não repetido (processado_em idêntico)",
        bool(depois)
        and antes.get("processado_em") == depois.get("processado_em"),
        f"antes={antes.get('processado_em')} depois={depois and depois.get('processado_em')}",
    )


def cenario_topologia() -> None:
    print("\n[4] Topologia RabbitMQ")
    # logo após um `docker compose up` a Management API pode ainda estar
    # subindo; espera em vez de abortar o smoke test inteiro
    espera_mgmt = time.monotonic()
    while True:
        status, _ = mq("/api/overview")
        if status == 200:
            break
        if time.monotonic() - espera_mgmt > 60:
            registrar(
                "Management API do RabbitMQ acessível",
                False,
                f"status={status} url={MGMT_URL}",
            )
            return
        time.sleep(2)

    status, exchange = mq(f"/api/exchanges/{VHOST}/{EXCHANGE}")
    registrar(
        f"exchange {EXCHANGE} existe e é durável",
        status == 200 and isinstance(exchange, dict) and exchange.get("durable"),
        f"status={status}",
    )
    dados_fila = fila(QUEUE)
    registrar(
        f"fila {QUEUE} durável",
        dados_fila.get("durable") is True,
        f"consumers={dados_fila.get('consumers')}",
    )
    # o worker pode ainda estar subindo (container + boot do Django), então a
    # presença de consumidor é aguardada em vez de conferida uma única vez
    consumidores = dados_fila.get("consumers", 0)
    espera = time.monotonic()
    while consumidores < 1 and time.monotonic() - espera < 20:
        time.sleep(1.0)
        consumidores = fila(QUEUE).get("consumers", 0)
    registrar(
        "fila principal com prefetch/consumidor ativo",
        consumidores >= 1,
        f"consumers={consumidores} em {time.monotonic() - espera:.1f}s",
    )
    dados_dlq = fila(DLQ)
    registrar(
        f"DLQ {DLQ} sem consumidor automático",
        dados_dlq.get("consumers", 0) == 0,
        f"consumers={dados_dlq.get('consumers')}",
    )


def cenario_falha(token: str, item_id: int) -> None:
    print("\n[5] Falha forçada -> retry -> backoff -> DLQ")
    print(
        "  (exige worker com PEDIDO_WORKER_FALHA_IDEM_KEYS='falha-*'; "
        "se a variável estiver vazia este cenário falha de propósito)"
    )
    chave = f"falha-{uuid.uuid4()}"
    profundidade_antes = fila(DLQ).get("messages", 0)

    t0 = time.monotonic()
    status, corpo = criar_pedido(token, item_id, chave)
    registrar(
        "pedido com chave de falha criado (201)",
        status == 201,
        f"status={status} chave={chave}",
    )
    pedido_id = (corpo.get("pedido") or {}).get("id")

    # espera: tentativas + backoffs (250+500+1000 ms) + margem
    prazo = 30.0
    profundidade = profundidade_antes
    while time.monotonic() - t0 < prazo:
        profundidade = fila(DLQ).get("messages", 0)
        if profundidade > profundidade_antes:
            break
        time.sleep(0.5)
    decorrido = time.monotonic() - t0

    registrar(
        "mensagem chegou à DLQ após exceder as tentativas",
        profundidade > profundidade_antes,
        f"DLQ {profundidade_antes} -> {profundidade} em {decorrido:.2f}s",
    )
    registrar(
        "backoff aplicado (não foi retry imediato)",
        decorrido >= 1.75,
        f"{decorrido:.2f}s (mínimo esperado 1.75s = 250+500+1000 ms)",
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

    mensagens = mensagens_dlq()
    # a DLQ pode acumular mensagens de execuções anteriores do próprio smoke
    # test; procura a desta rodada pela chave de negócio
    alvo: Optional[Dict[str, Any]] = None
    corpo_msg: Dict[str, Any] = {}
    for mensagem in mensagens:
        try:
            candidato = json.loads(mensagem.get("payload", "{}"))
        except ValueError:
            continue
        if isinstance(candidato, dict) and candidato.get("idempotency_key") == chave:
            alvo = mensagem
            corpo_msg = candidato
            break

    registrar(
        "mensagem desta rodada encontrada na DLQ",
        alvo is not None,
        f"lidas={len(mensagens)} chave={chave}",
    )
    if alvo is None:
        return

    propriedades = alvo.get("properties") or {}
    cabecalhos = propriedades.get("headers") or {}
    retry_count = cabecalhos.get("x-retry-count")
    registrar(
        "contador x-retry-count presente na mensagem morta",
        retry_count is not None,
        f"x-retry-count={retry_count}",
    )
    registrar(
        "payload da DLQ é o envelope PedidoCriado",
        corpo_msg.get("event_type") == "PedidoCriado",
        f"event_type={corpo_msg.get('event_type')}",
    )


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--falha",
        action="store_true",
        help="roda também o cenário de falha forçada -> DLQ",
    )
    args = parser.parse_args()

    print(f"API: {BASE_URL} | Management: {MGMT_URL}")

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

        if args.falha:
            cenario_falha(token, item_id)
    except SystemExit:
        raise
    except Exception as erro:  # noqa: BLE001
        registrar("execução do smoke test", False, f"{type(erro).__name__}: {erro}")

    print("\n" + "=" * 60)
    falhas = [r for r in resultados if not r[1]]
    for nome, ok, detalhe in resultados:
        if not ok:
            print(f"FALHA: {nome} — {detalhe}")
    print(
        f"{len(resultados) - len(falhas)}/{len(resultados)} verificações passaram"
    )
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
