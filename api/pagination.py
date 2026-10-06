"""Paginação padrão das listas da API.

Formato: `?page=1&limit=20`.

A resposta deixa de ser uma lista pura e passa a ser um envelope com `count`,
`next`, `previous` e `results`. Sem isso, qualquer cliente acabaria puxando a
tabela inteira para a memória - o problema clássico de `GET /itens/` sem
paginação.
"""

from __future__ import annotations

from rest_framework.pagination import PageNumberPagination


class CatalogoPagination(PageNumberPagination):
    """Paginação por página, com limite máximo para proteger o banco.

    O envelope de resposta (`count`, `next`, `previous`, `results`) é o mesmo
    já implementado pelo DRF; aqui mudamos apenas os parâmetros aceitos.
    """

    # o padrão do projeto: 20 registros por página
    page_size = 20
    # o cliente escolhe o tamanho com `?limit=`, dentro do teto abaixo
    page_size_query_param = "limit"
    page_query_param = "page"
    # teto do `?limit=`, para que ninguém peça a tabela inteira de uma vez
    max_page_size = 100
