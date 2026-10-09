"""Testes de integração do ciclo de vida de um item do catálogo."""

from __future__ import annotations

from decimal import Decimal

import pytest

from repositories.models import Item

pytestmark = pytest.mark.integration

ITENS = "/api/v1/items/"


@pytest.mark.django_db
def test_ciclo_de_vida_do_item_com_cache(client, base, cabecalho_de):
    dados = base()
    headers = cabecalho_de(dados.admin)

    criado = client.post(
        ITENS,
        {"name": "Mouse", "price": "50.00", "category": dados.categoria.id},
        format="json",
        **headers,
    )
    assert criado.status_code == 201, criado.data
    item_id = criado.data["id"]
    assert Item.objects.filter(pk=item_id).exists()

    detalhe = f"{ITENS}{item_id}/"
    assert client.get(detalhe)["X-Cache"] == "MISS"
    assert client.get(detalhe)["X-Cache"] == "HIT"

    lista = client.get(ITENS)
    assert lista["X-Cache"] == "MISS"
    assert client.get(ITENS)["X-Cache"] == "HIT"

    atualizado = client.patch(
        detalhe, {"price": "55.00"}, format="json", **headers
    )
    assert atualizado.status_code == 200
    assert client.get(detalhe)["X-Cache"] == "MISS"
    assert Item.objects.get(pk=item_id).price == Decimal("55.00")


@pytest.mark.django_db
def test_criar_item_preco_negativo_responde_400(client, base, cabecalho_de):
    dados = base()
    resposta = client.post(
        ITENS,
        {"name": "Mouse", "price": "-1.00", "category": dados.categoria.id},
        format="json",
        **cabecalho_de(dados.admin),
    )
    assert resposta.status_code == 400


@pytest.mark.django_db
def test_escrita_de_catalogo_exige_admin(client, base, cabecalho_de):
    dados = base()
    resposta = client.post(
        ITENS,
        {"name": "Mouse", "price": "50.00", "category": dados.categoria.id},
        format="json",
        **cabecalho_de(dados.usuario),
    )
    assert resposta.status_code == 403


@pytest.mark.django_db
def test_leitura_publica_do_catalogo(client, base):
    base()
    assert client.get(ITENS).status_code == 200
