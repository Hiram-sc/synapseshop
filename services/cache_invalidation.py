"""Regras de invalidação do cache do catálogo.

O `services/events.py` diz *que* algo aconteceu; este módulo diz *o que fazer
com o cache* quando acontece. A separação é o que permite testar as regras sem
passar pela API e escrever na view sem conhecer chave nenhuma.

As regras são pequenas de propósito:

* qualquer escrita em `Item` renova a geração da listagem **e** apaga o detalhe
  daquele item;
* criar ou renomear uma `Category` renova a geração da listagem, porque o
  `category_name` aparece dentro do payload de cada item;
* apagar uma `Category` faz os dois, e precisa dos ids dos itens porque o
  `CASCADE` do banco os apaga junto - depois do `DELETE` eles já não existem
  para ser consultados.

Repare no que **não** acontece aqui: nada de `KEYS`, `SCAN` ou `FLUSHDB` no
caminho da requisição. A invalidação da listagem é um `INCR` e a do detalhe é um
`DEL` por id conhecido.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from services import cache as cache_service
from services import events

logger = logging.getLogger(__name__)

_registrado = False


def _item_mudou(item_id: Any) -> None:
    """Comum a create/update/delete de item: a listagem e o detalhe envelhecem."""
    geracao = cache_service.invalidar_listagem()
    removido = cache_service.invalidar_detalhe(item_id)
    logger.info(
        "Item %s alterado: listagem -> geração %s, detalhe removido=%s.",
        item_id,
        geracao,
        removido,
    )


def _categoria_mudou(evento: events.Evento) -> None:
    """Listagem sempre; detalhe de todos os itens da categoria, quando houver."""
    geracao = cache_service.invalidar_listagem()
    item_ids: Iterable[Any] = evento.dados.get("item_ids") or ()
    afetados = cache_service.invalidar_detalhes(item_ids)
    logger.info(
        "Categoria %s alterada: listagem -> geração %s, %s detalhe(s) removido(s).",
        evento.dados.get("categoria_id"),
        geracao,
        afetados,
    )


def item_criado(evento: events.Evento) -> None:
    _item_mudou(evento.dados["item_id"])


def item_atualizado(evento: events.Evento) -> None:
    _item_mudou(evento.dados["item_id"])


def item_removido(evento: events.Evento) -> None:
    _item_mudou(evento.dados["item_id"])


def categoria_criada(evento: events.Evento) -> None:
    # categoria nova não aparece no detalhe de nenhum item ainda existente
    cache_service.invalidar_listagem()
    logger.info(
        "Categoria %s criada: listagem -> geração %s.",
        evento.dados.get("categoria_id"),
        cache_service.geracao_atual(),
    )


def categoria_atualizada(evento: events.Evento) -> None:
    # renomear a categoria muda o `category_name` de todos os seus itens
    _categoria_mudou(evento)


def categoria_removida(evento: events.Evento) -> None:
    _categoria_mudou(evento)


def registrar() -> None:
    """Assina os eventos do catálogo. Idempotente, para poder chamar à vontade.

    O `api.apps.ApiConfig.ready()` chama esta função no boot do processo.
    """
    global _registrado
    if _registrado:
        return
    events.inscrever(events.ITEM_CRIADO, item_criado)
    events.inscrever(events.ITEM_ATUALIZADO, item_atualizado)
    events.inscrever(events.ITEM_REMOVIDO, item_removido)
    events.inscrever(events.CATEGORIA_CRIADA, categoria_criada)
    events.inscrever(events.CATEGORIA_ATUALIZADA, categoria_atualizada)
    events.inscrever(events.CATEGORIA_REMOVIDA, categoria_removida)
    _registrado = True
    logger.info("Regras de invalidação de cache registradas.")
