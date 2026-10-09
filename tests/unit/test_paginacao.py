"""Testes unitários da paginação cacheada (`api.pagination`)."""

from __future__ import annotations

import pytest
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from api.pagination import CatalogoPagination

factory = APIRequestFactory()


def _request(**params) -> Request:
    return Request(factory.get("/api/v1/itens/", params))


def _paginar(count, numero, **params):
    paginador = CatalogoPagination()
    paginador.preparar_pagina_cacheada(_request(**params), count, numero)
    return paginador


@pytest.mark.unit
def test_pagina_unica_nao_tem_links():
    paginador = _paginar(5, 1)
    assert paginador.page.paginator.count == 5
    assert paginador.get_next_link() is None
    assert paginador.get_previous_link() is None


@pytest.mark.unit
def test_primeira_pagina_tem_proximo():
    paginador = _paginar(25, 1)
    assert "page=2" in paginador.get_next_link()
    assert paginador.get_previous_link() is None


@pytest.mark.unit
def test_pagina_do_meio_tem_anterior_e_proximo():
    paginador = _paginar(45, 2)
    assert "page=3" in paginador.get_next_link()
    # a volta para a primeira página remove o parâmetro, como no DRF
    assert "page=" not in paginador.get_previous_link()


@pytest.mark.unit
def test_ultima_pagina_nao_tem_proximo():
    paginador = _paginar(45, 3)
    assert paginador.get_next_link() is None
    assert "page=2" in paginador.get_previous_link()


@pytest.mark.unit
def test_limite_do_cliente_muda_o_tamanho_da_pagina():
    paginador = _paginar(5, 1, limit=2)
    assert "page=2" in paginador.get_next_link()


@pytest.mark.unit
def test_limite_acima_do_teto_e_ignorado():
    paginador = _paginar(150, 1, limit=9999)
    assert "page=2" in paginador.get_next_link()


@pytest.mark.unit
@pytest.mark.parametrize("numero", ["abc", 9999])
def test_pagina_invalida_responde_not_found(numero):
    with pytest.raises(NotFound):
        _paginar(5, numero)
