"""Testes de integração do ciclo de vida de um pagamento (`POST pagamento`)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from repositories.models import (
    Pagamento,
    Pedido,
    Role,
    StatusPagamento,
    StatusPedido,
)

pytestmark = pytest.mark.integration

URL = "/api/v1/pedidos/"


def _criar_pedido(usuario, key="pg-1"):
    return Pedido.objects.create(
        usuario=usuario, idempotency_key=key, total=Decimal("10.00")
    )


def _url(pedido) -> str:
    return f"{URL}{pedido.id}/pagamento/"


@pytest.mark.django_db(transaction=True)
def test_pagamento_aprovado_201_persiste_e_publica(
    client, base, cabecalho_de, broker_isolado
):
    dados = base()
    pedido = _criar_pedido(dados.usuario)

    resposta = client.post(
        _url(pedido),
        {"status": "APROVADO"},
        format="json",
        **cabecalho_de(dados.usuario),
    )

    assert resposta.status_code == 201, resposta.data
    assert resposta.data["evento_publicado"] is True
    assert Pagamento.objects.filter(pedido=pedido).exists()

    pedido.refresh_from_db()
    assert pedido.status == StatusPedido.CRIADO

    assert len(broker_isolado.publicacoes) == 1
    assert broker_isolado.publicacoes[0][1]["event_type"] == "PagamentoProcessado"


@pytest.mark.django_db
def test_pagamento_repetido_responde_409(client, base, cabecalho_de):
    dados = base()
    pedido = _criar_pedido(dados.usuario)
    existente = Pagamento.objects.create(
        pedido=pedido, status=StatusPagamento.APROVADO
    )

    resposta = client.post(
        _url(pedido), {}, format="json", **cabecalho_de(dados.usuario)
    )

    assert resposta.status_code == 409
    assert resposta.data["pagamento"]["id"] == existente.id
    assert Pagamento.objects.filter(pedido=pedido).count() == 1


@pytest.mark.django_db
def test_pagamento_status_invalido_responde_400(client, base, cabecalho_de):
    dados = base()
    pedido = _criar_pedido(dados.usuario)
    resposta = client.post(
        _url(pedido),
        {"status": "TALVEZ"},
        format="json",
        **cabecalho_de(dados.usuario),
    )
    assert resposta.status_code == 400


@pytest.mark.django_db
def test_pagamento_pedido_inexistente_responde_404(client, base, cabecalho_de):
    dados = base()
    resposta = client.post(
        f"{URL}999999/pagamento/",
        {},
        format="json",
        **cabecalho_de(dados.usuario),
    )
    assert resposta.status_code == 404


@pytest.mark.django_db
def test_pagamento_de_pedido_alheio_responde_404(
    client, base, users_repo, cabecalho_de
):
    dados = base()
    outro = users_repo.create_user("outro", "senha-outro", Role.USER)
    pedido = _criar_pedido(dados.usuario)

    resposta = client.post(
        _url(pedido), {}, format="json", **cabecalho_de(outro)
    )
    assert resposta.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_pagamento_recusado_cancela_pedido_e_invalida_cache(
    client, base, cabecalho_de
):
    dados = base()
    pedido = _criar_pedido(dados.usuario)
    headers = cabecalho_de(dados.usuario)

    assert client.get(f"{URL}{pedido.id}/", **headers)["X-Cache"] == "MISS"
    assert client.get(f"{URL}{pedido.id}/", **headers)["X-Cache"] == "HIT"

    resposta = client.post(
        _url(pedido), {"status": "RECUSADO"}, format="json", **headers
    )
    assert resposta.status_code == 201

    pedido.refresh_from_db()
    assert pedido.status == StatusPedido.CANCELADO

    depois = client.get(f"{URL}{pedido.id}/", **headers)
    assert depois["X-Cache"] == "MISS"
    assert depois.data["status"] == "cancelado"
