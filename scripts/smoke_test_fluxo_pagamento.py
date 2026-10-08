#!/usr/bin/env python
"""Smoke test do fluxo Pedido -> Pagamento -> Notificação (Aula 11).

Valida, contra o ambiente real (API + PostgreSQL + Redis + Kafka + workers),
o que a spec pede comprovar nesta aula:

* `/health` (liveness) e `/health/pronto` (readiness: PostgreSQL, Redis e
  broker) respondendo;
* cache-aside do `GET /pedidos/{id}/`: primeiro acesso `MISS`, segundo `HIT`,
  payload idêntico, e invalidação quando o pedido muda de estado;
* pagamento `APROVADO`: 201 com evento confirmado, evento `PagamentoProcessado`
  no tópico, pedido confirmado pelo worker e evento terminal
  `NotificacaoEnviada` (idempotency `notificacao:{pagamento_id}`);
* pagamento `RECUSADO`: 201, pedido `cancelado`, cache invalidado e **nenhum**
  `NotificacaoEnviada` para o pagamento recusado;
* pagamento duplicado -> 409 sem republicar; `status` inválido -> 400;
  pedido inexistente/estranho -> 404; sem token -> 401;
* HIT de cache não vaza pedido alheio: um usuário `user` recebe 404 mesmo com
  a chave quente gravada pelo dono.

Como rodar (de dentro da rede do Compose):

    docker compose exec api python scripts/smoke_test_fluxo_pagamento.py

O smoke cria (via ORM, rodando dentro do container da API) um usuário
temporário de role `user` para o cenário de visibilidade. Rodadas repetidas
em menos de um minuto podem esbarrar no throttle de login (5/min por IP):
espere 60s entre execuções. Sem suíte automatizada: é validação pontual do
fluxo, executada sob demanda (mesma disciplina das Aulas 9 e 10).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple

# executado como `python scripts/smoke_test_fluxo_pagamento.py`, o `sys.path[0]`
# é a pasta `scripts/`; a raiz do projeto entra aqui para os imports de
# `services.mensageria.config` (nomes de tópicos) funcionarem
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

from services.mensageria import config  # noqa: E402

from confluent_kafka import Consumer, Producer  # noqa: E402

BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://localhost:8000").rstrip("/")
USUARIO = os.environ.get("SMOKE_USUARIO", "admin")
SENHA = os.environ.get("SMOKE_SENHA", "admin123")

ESPERA_CONSUMO_S = float(os.environ.get("SMOKE_ESPERA_CONSUMO_S", "20"))
JANELA_NEGATIVA_S = 5.0
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
    timeout: float = 10.0,
) -> Tuple[int, Any, Dict[str, str]]:
    """Devolve `(status, corpo, cabeçalhos)` - o smoke lê `X-Cache`."""
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
            headers = dict(resposta.headers)
    except urllib.error.HTTPError as erro:
        bruto = erro.read().decode("utf-8", "replace")
        status = erro.code
        headers = dict(erro.headers)
    if not bruto:
        return status, None, headers
    try:
        return status, json.loads(bruto), headers
    except ValueError:
        return status, bruto, headers


def api(caminho, *, metodo="GET", corpo=None, token=None):
    return _http(caminho, metodo=metodo, corpo=corpo, token=token)


def login(usuario: str, senha: str) -> str:
    status, dados, _ = api(
        "/api/v1/auth/login/",
        metodo="POST",
        corpo={"username": usuario, "password": senha},
    )
    if status != 200 or not isinstance(dados, dict) or "access" not in dados:
        raise SystemExit(f"login falhou ({usuario}): status={status} corpo={dados}")
    print(f"login OK como '{usuario}'")
    return dados["access"]


def garantir_item(token: str) -> int:
    status, dados, _ = api("/api/v1/items/?is_active=true&limit=1", token=token)
    if status == 200 and isinstance(dados, dict) and dados.get("results"):
        return dados["results"][0]["id"]
    print("  catálogo vazio; criando categoria e item para o smoke test")
    status, categoria, _ = api(
        "/api/v1/categories/",
        metodo="POST",
        corpo={"name": f"Smoke {uuid.uuid4().hex[:8]}"},
        token=token,
    )
    if status != 201 or not isinstance(categoria, dict):
        raise SystemExit(f"criação de categoria falhou: {status} {categoria}")
    status, item, _ = api(
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


def criar_pedido(token: str, item_id: int, idempotency_key: str):
    return api(
        "/api/v1/pedidos/",
        metodo="POST",
        corpo={
            "idempotency_key": idempotency_key,
            "itens": [{"item_id": item_id, "quantidade": 2}],
        },
        token=token,
    )


def pagar(token: str, pedido_id: int, status: Optional[str] = None):
    corpo = {} if status is None else {"status": status}
    return api(
        f"/api/v1/pedidos/{pedido_id}/pagamento/",
        metodo="POST",
        corpo=corpo,
        token=token,
    )


def get_pedido(token: str, pedido_id: int):
    return _http(f"/api/v1/pedidos/{pedido_id}/", token=token)


def aguardar_status(token: str, pedido_id: int, esperado: str) -> Tuple[Optional[Dict], float]:
    inicio = time.monotonic()
    while time.monotonic() - inicio < ESPERA_CONSUMO_S:
        status, corpo, _ = get_pedido(token, pedido_id)
        if status == 200 and isinstance(corpo, dict):
            if corpo.get("status") == esperado:
                return corpo, time.monotonic() - inicio
        time.sleep(TICK_S)
    return None, time.monotonic() - inicio


# ---------------------------------------------------------------------------
# Kafka
# ---------------------------------------------------------------------------
def kafka_conf(sufixo: str) -> Dict[str, Any]:
    return {
        "bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS,
        "security.protocol": config.KAFKA_SECURITY_PROTOCOL,
        "client.id": f"smoke-pag-{sufixo}",
    }


def _produtor() -> Producer:
    return Producer({**kafka_conf("produtor"), "acks": "all", "retries": 3, "linger.ms": 5})


def _ler(
    topico: str,
    *,
    chave_esperada: str,
    janela_s: float,
    precisa: bool,
) -> Optional[Dict[str, Any]]:
    """Lê `topico` do início e procura a mensagem com a chave esperada.

    Com `precisa=True` varre até `janela_s` (procurando a mensagem); com
    `precisa=False` varre a mesma janela para **confirmar a ausência** da
    chave (cenário negativo de pagamento recusado).
    """
    consumidor = Consumer(
        {
            **kafka_conf("leitor"),
            "group.id": f"smoke-pag-{uuid.uuid4().hex[:12]}",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    consumidor.subscribe([topico])
    try:
        prazo = time.monotonic() + janela_s
        while time.monotonic() < prazo:
            msg = consumidor.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                continue
            chave = (msg.key() or b"").decode("utf-8", "replace")
            if precisa and chave == chave_esperada:
                try:
                    corpo = json.loads(msg.value().decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    corpo = {}
                return {"payload": corpo, "headers": msg.headers() or [], "key": chave}
            if not precisa and chave == chave_esperada:
                return {"payload": {}, "headers": msg.headers() or [], "key": chave}
        return None
    finally:
        consumidor.close()


def aguardar_evento(topico: str, chave: str) -> Optional[Dict[str, Any]]:
    return _ler(topico, chave_esperada=chave, janela_s=ESPERA_CONSUMO_S, precisa=True)


def confirmar_ausencia(topico: str, chave: str) -> bool:
    return _ler(topico, chave_esperada=chave, janela_s=JANELA_NEGATIVA_S, precisa=False) is None


# ---------------------------------------------------------------------------
# cenários
# ---------------------------------------------------------------------------
def cenario_health() -> None:
    print("\n[1] Healthcheck")
    status, corpo, _ = api("/health")
    registrar(
        "liveness /health responde 200",
        status == 200 and isinstance(corpo, dict) and corpo.get("status") == "ok",
        f"status={status} corpo={corpo}",
    )
    status, corpo, _ = api("/health/pronto")
    registrar(
        "readiness /health/pronto responde 200",
        status == 200 and isinstance(corpo, dict) and corpo.get("status") == "ok",
        f"status={status} corpo={corpo}",
    )
    if isinstance(corpo, dict):
        for nome_dep in ("database", "redis", "broker"):
            registrar(
                f"dependência {nome_dep} ok",
                corpo.get(nome_dep) == "ok",
                str(corpo.get(nome_dep)),
            )
    else:
        for nome_dep in ("database", "redis", "broker"):
            registrar(f"dependência {nome_dep} ok", False, "corpo fora do padrão")


def cenario_aprovado(token: str, item_id: int) -> Optional[Dict[str, Any]]:
    print("\n[2] Pagamento APROVADO: 201 -> PagamentoProcessado -> confirmado -> NotificacaoEnviada")
    chave = f"smoke-aprovado-{uuid.uuid4()}"
    status, corpo, _ = criar_pedido(token, item_id, chave)
    pedido_id = (corpo.get("pedido") or {}).get("id") if isinstance(corpo, dict) else None
    if status != 201 or not pedido_id:
        registrar("pedido do fluxo aprovado criado", False, f"status={status} corpo={corpo}")
        return None

    status, pagamento, _ = pagar(token, pedido_id, "APROVADO")
    registrar("POST pagamento responde 201", status == 201, f"status={status}")
    registrar(
        "evento_publicado=true (Kafka confirmou)",
        isinstance(pagamento, dict) and pagamento.get("evento_publicado") is True,
        str(pagamento),
    )
    pagamento_id = (pagamento or {}).get("pagamento", {}).get("id")

    chave_evento = f"pagamento:{pedido_id}"
    evento = aguardar_evento(config.KAFKA_TOPIC_PAGAMENTO_PROCESSADO, chave_evento)
    registrar(
        "PagamentoProcessado publicado com idempotency pagamento:{pedido_id}",
        evento is not None,
        f"chave={chave_evento}",
    )
    if evento:
        registrar(
            "payload do PagamentoProcessado é o grande envelope correto",
            isinstance(evento["payload"], dict)
            and evento["payload"].get("event_type") == "PagamentoProcessado"
            and (evento["payload"].get("dados") or {}).get("pagamento", {}).get("status") == "APROVADO",
            str(evento["payload"].get("event_type")),
        )

    confirmado, espera = aguardar_status(token, pedido_id, "confirmado")
    registrar(
        "pedido confirmado pelo pedido-worker",
        confirmado is not None,
        f"em {espera * 1000:.0f} ms",
    )

    chave_notif = f"notificacao:{pagamento_id}"
    notificado = aguardar_evento(config.KAFKA_TOPIC_NOTIFICACAO_ENVIADA, chave_notif)
    registrar(
        "NotificacaoEnviada publicado com idempotency notificacao:{pagamento_id}",
        notificado is not None,
        f"chave={chave_notif}",
    )
    if notificado:
        dados = isinstance(notificado["payload"], dict) and notificado["payload"].get("dados") or {}
        registrar(
            "payload da notificação tem a mensagem de confirmação",
            (dados.get("notificacao") or {}).get("mensagem") == f"Pagamento aprovado para o pedido {pedido_id}.",
            str(dados.get("notificacao", {}).get("mensagem")),
        )

    status, duplicado, _ = pagar(token, pedido_id, "APROVADO")
    registrar(
        "pagamento duplicado responde 409 sem republicar",
        status == 409
        and isinstance(duplicado, dict)
        and (duplicado.get("pagamento") or {}).get("id") == pagamento_id,
        f"status={status}",
    )
    return {"pedido_id": pedido_id, "pagamento_id": pagamento_id, "chave": chave}


def cenario_recusado(token: str, item_id: int) -> Optional[Dict[str, Any]]:
    print("\n[3] Pagamento RECUSADO: 201 -> cancelado + cache invalidado + sem notificação")
    chave = f"smoke-recusado-{uuid.uuid4()}"
    status, corpo, _ = criar_pedido(token, item_id, chave)
    pedido_id = (corpo.get("pedido") or {}).get("id") if isinstance(corpo, dict) else None
    if status != 201 or not pedido_id:
        registrar("pedido do fluxo recusado criado", False, f"status={status} corpo={corpo}")
        return None

    primeiro_status, primeiro, headers = get_pedido(token, pedido_id)
    registrar(
        "primeiro GET do pedido é MISS (cache frio)",
        primeiro_status == 200 and headers.get("X-Cache") == "MISS",
        f"X-Cache={headers.get('X-Cache')}",
    )
    segundo_status, segundo, headers = get_pedido(token, pedido_id)
    registrar(
        "segundo GET é HIT com payload idêntico",
        segundo_status == 200 and headers.get("X-Cache") == "HIT" and segundo == primeiro,
        f"X-Cache={headers.get('X-Cache')}",
    )

    status, pagamento, _ = pagar(token, pedido_id, "RECUSADO")
    registrar("POST pagamento RECUSADO responde 201", status == 201, f"status={status}")
    pagamento_id = (pagamento or {}).get("pagamento", {}).get("id")

    pos_status, pos, headers = get_pedido(token, pedido_id)
    registrar(
        "GET após o cancelamento é MISS (cache invalidado) e mostra cancelado",
        pos_status == 200
        and headers.get("X-Cache") == "MISS"
        and pos.get("status") == "cancelado",
        f"X-Cache={headers.get('X-Cache')} status={pos.get('status')}",
    )
    quente_status, quente, headers = get_pedido(token, pedido_id)
    registrar(
        "GET seguinte é HIT do pedido cancelado",
        quente_status == 200
        and headers.get("X-Cache") == "HIT"
        and quente.get("status") == "cancelado",
        f"X-Cache={headers.get('X-Cache')}",
    )

    assert pagamento_id is not None, "pagamento recusado sem id"
    ausente = confirmar_ausencia(
        config.KAFKA_TOPIC_NOTIFICACAO_ENVIADA, f"notificacao:{pagamento_id}"
    )
    registrar(
        "nenhum NotificacaoEnviada para o pagamento recusado",
        ausente,
        f"chave=notificacao:{pagamento_id}",
    )
    return {"pedido_id": pedido_id, "pagamento_id": pagamento_id}


def cenario_validacoes(token: str, item_id: int) -> None:
    print("\n[4] Validações: status inválido, pedido inexistente e sem token")
    chave = f"smoke-val-{uuid.uuid4()}"
    status, corpo, _ = criar_pedido(token, item_id, chave)
    pedido_id = (corpo.get("pedido") or {}).get("id")
    status, corpo, _ = pagar(token, pedido_id, "PENDENTE")
    registrar(
        "status de pagamento inválido responde 400",
        status == 400,
        f"status={status}",
    )
    status, corpo, _ = pagar(token, 99999999, "APROVADO")
    registrar(
        "pagamento em pedido inexistente responde 404",
        status == 404,
        f"status={status}",
    )
    status, corpo, _ = _http(
        f"/api/v1/pedidos/{pedido_id}/pagamento/",
        metodo="POST",
        corpo={"status": "APROVADO"},
    )
    registrar(
        "pagamento sem token responde 401",
        status == 401,
        f"status={status}",
    )


def cenario_visibilidade(token_admin: str, pedido_id: int) -> None:
    print("\n[5] HIT não vaza pedido alheio (usuário user recebe 404)")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()

    from django.contrib.auth import get_user_model as _gum
    from repositories.models import Role

    User = _gum()
    usuario = f"smoke_cj_{uuid.uuid4().hex[:8]}"
    senha = "smoke123"
    try:
        criado = User.objects.create_user(username=usuario, password=senha, role=Role.USER)
        print(f"  usuário temporário criado: {usuario}")
    except Exception as erro:  # noqa: BLE001
        registrar("criação do usuário espectador", False, f"{type(erro).__name__}: {erro}")
        return

    try:
        token_espectador = login(usuario, senha)

        _, _, headers_dono = get_pedido(token_admin, pedido_id)
        registrar(
            "cache do dono ainda quente (HIT) antes da checagem",
            headers_dono.get("X-Cache") == "HIT",
            f"X-Cache={headers_dono.get('X-Cache')}",
        )
        status, corpo, headers = get_pedido(token_espectador, pedido_id)
        registrar(
            "usuário user recebe 404 mesmo com a chave quente",
            status == 404,
            f"status={status} corpo={corpo}",
        )
        status, corpo, _ = pagar(token_espectador, pedido_id, "APROVADO")
        registrar(
            "usuário user não consegue pagar pedido alheio (404)",
            status == 404,
            f"status={status}",
        )
    finally:
        criado.delete()


# ---------------------------------------------------------------------------
def main() -> int:
    print(f"API: {BASE_URL} | Kafka: {config.KAFKA_BOOTSTRAP_SERVERS}")

    try:
        token = login(USUARIO, SENHA)
        item_id = garantir_item(token)
        print(f"item ativo usado no teste: {item_id}")

        cenario_health()

        fluxo = cenario_aprovado(token, item_id)
        recusado = cenario_recusado(token, item_id)
        cenario_validacoes(token, item_id)

        if fluxo and recusado:
            cenario_visibilidade(token, recusado["pedido_id"])
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