"""Paginação padrão das listas da API.

Formato: `?page=1&limit=20`.

A resposta deixa de ser uma lista pura e passa a ser um envelope com `count`,
`next`, `previous` e `results`. Sem isso, qualquer cliente acabaria puxando a
tabela inteira para a memória - o problema clássico de `GET /itens/` sem
paginação.
"""

from __future__ import annotations

from django.core.paginator import EmptyPage, PageNotAnInteger
from django.core.paginator import Paginator as DjangoPaginator
from rest_framework.exceptions import NotFound
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

    def preparar_pagina_cacheada(self, request, count, numero) -> None:
        """Prepara `self.page` e `self.request` usando o `count` do cache.

        Sem isto, os links `next`/`previous` dependeriam de um `COUNT` novo no
        banco - exatamente a consulta que o cache existe para evitar. O objeto
        `Page` montado aqui é o do próprio Django, então `get_next_link()` e
        `get_previous_link()` continuam funcionando sem nenhuma cópia do código
        do DRF; só o envelope muda de forma (o `count` vem do cache).

        A validação do número da página é a mesma do caminho sem cache:
        `?page=abc` e `?page=9999` continuam respondendo 404 com a mesma
        mensagem, em vez de devolverem uma lista vazia e mascararem o erro.
        """
        self.request = request
        paginador = DjangoPaginator([], self.get_page_size(request))
        # `count` do Django é um `cached_property`: sobrescrever o atributo antes
        # do primeiro acesso faz `num_pages` (e portanto `has_next`) usar o
        # total cacheado, sem tocar no banco.
        paginador.count = count
        try:
            self.page = paginador.page(numero)
        except (PageNotAnInteger, EmptyPage) as erro:
            # mesma mensagem do caminho com banco (ver `paginate_queryset` do DRF)
            raise NotFound(
                self.invalid_page_message.format(
                    page_number=numero, message=str(erro)
                )
            )
