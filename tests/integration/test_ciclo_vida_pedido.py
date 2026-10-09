"""Testes de integração do ciclo de vida de um pedido (`POST`/`GET`)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from repositories.models import Pedido, PedidoItem, Role

pytestmark = pytest.mark.integration

URL = "/api/v1/pedidos/"


def _corpo(item_id, key, quantidade=1):
    return {
        "idempotency_key": key,
        "itens": [{"item_id": item_id, "quantidade": quantidade}],
    }


def _criar_pedido(usuario, key="direto-1", total="10.00"):
    return Pedido.objects.create(
        usuario=usuario, idempotency_key=key, total=Decimal(total)
    )


@pytest.mark.django_db(transaction=True)
def test_post_cria_persiste_e_publica(
    client, base, cabecalho_de, broker_isolado
):
    dados = base()
    resposta = client.post(
        URL,
        _corpo(dados.item_ativo.id, "ped-1", quantidade=2),
        format="json",
        **cabecalho_de(dados.usuario),
    )

    assert resposta.status_code == 201, resposta.data
    assert resposta.data["evento_publicado"] is True

    pedido = Pedido.objects.get(idempotency_key="ped-1")
    assert pedido.total == Decimal("699.80")
    assert PedidoItem.objects.filter(pedido=pedido).count() == 1

    assert len(broker_isolado.publicacoes) == 1
    canal, envelope = broker_isolado.publicacoes[0]
    assert canal == "kafka"
    assert envelope["event_type"] == "PedidoCriado"
    assert envelope["idempotency_key"] == "ped-1"


@pytest.mark.django_db(transaction=True)
def test_post_repetido_e_idempotente_e_nao_republica(
    client, base, cabecalho_de, broker_isolado
):
    dados = base()
    headers = cabecalho_de(dados.usuario)

    primeira = client.post(
        URL, _corpo(dados.item_ativo.id, "ped-1"), format="json", **headers
    )
    segunda = client.post(
        URL, _corpo(dados.item_ativo.id, "ped-1"), format="json", **headers
    )

    assert primeira.status_code == 201
    assert segunda.status_code == 200
    assert segunda.data["pedido"]["id"] == primeira.data["pedido"]["id"]
    assert Pedido.objects.filter(idempotency_key="ped-1").count() == 1
    assert len(broker_isolado.publicacoes) == 1


@pytest.mark.django_db(transaction=True)
def test_post_com_broker_recusando_responde_202(
    client, base, cabecalho_de, broker_isolado
):
    dados = base()
    broker_isolado.resultado = False

    resposta = client.post(
        URL,
        _corpo(dados.item_ativo.id, "ped-recusado"),
        format="json",
        **cabecalho_de(dados.usuario),
    )

    assert resposta.status_code == 202, resposta.data
    assert resposta.data["evento_publicado"] is False
    assert Pedido.objects.filter(idempotency_key="ped-recusado").exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "corpo",
    [
        {},
        {"idempotency_key": "k", "itens": []},
        {"idempotency_key": "k", "itens": [{"item_id": 999999, "quantidade": 1}]},
        {"idempotency_key": "k", "itens": [{"item_id": 1, "quantidade": 0}]},
    ],
)
def test_post_invalido_responde_400(client, base, cabecalho_de, corpo):
    dados = base()
    if corpo and corpo.get("itens") and corpo["itens"][0]["item_id"] == 1:
        corpo["itens"][0]["item_id"] = dados.item_ativo.id
    resposta = client.post(
        URL, corpo, format="json", **cabecalho_de(dados.usuario)
    )
    assert resposta.status_code == 400


@pytest.mark.django_db
def test_post_sem_token_responde_401(client):
    resposta = client.post(
        URL, _corpo(1, "k"), format="json"
    )
    assert resposta.status_code == 401


@pytest.mark.django_db
def test_get_pedido_e_cacheado_miss_depois_hit(client, base, cabecalho_de):
    dados = base()
    pedido = _criar_pedido(dados.usuario)
    headers = cabecalho_de(dados.usuario)

    primeira = client.get(f"{URL}{pedido.id}/", **headers)
    segunda = client.get(f"{URL}{pedido.id}/", **headers)

    assert primeira.status_code == 200
    assert primeira["X-Cache"] == "MISS"
    assert segunda.status_code == 200
    assert segunda["X-Cache"] == "HIT"


@pytest.mark.django_db
def test_get_pedido_inexistente_404(client, base, cabecalho_de):
    dados = base()
    resposta = client.get(f"{URL}999999/", **cabecalho_de(dados.usuario))
    assert resposta.status_code == 404


@pytest.mark.django_db
def test_get_pedido_de_outro_usuario_404(client, base, users_repo, cabecalho_de):
    dados = base()
    outro = users_repo.create_user("outro", "senha-outro", Role.USER)
    pedido = _criar_pedido(dados.usuario)
    resposta = client.get(f"{URL}{pedido.id}/", **cabecalho_de(outro))
    assert resposta.status_code == 404


@pytest.mark.django_db
def test_get_pedido_admin_ve_qualquer(client, base, cabecalho_de):
    dados = base()
    pedido = _criar_pedido(dados.usuario)
    resposta = client.get(f"{URL}{pedido.id}/", **cabecalho_de(dados.admin))
    assert resposta.status_code == 200


@pytest.mark.django_db
def test_hit_gravado_por_outro_usuario_nao_vaza(client, base, users_repo, cabecalho_de):
    dados = base()
    outro = users_repo.create_user("outro", "senha-outro", Role.USER)
    pedido = _criar_pedido(dados.usuario)

    # o dono aquece o cache (MISS)
    assert (
        client.get(f"{URL}{pedido.id}/", **cabecalho_de(dados.usuario))["X-Cache"]
        == "MISS"
    )
    # outro usuário bate no HIT, mas a view revalida o dono a partir do payload
    resposta = client.get(f"{URL}{pedido.id}/", **cabecalho_de(outro))
    assert resposta.status_code == 404
