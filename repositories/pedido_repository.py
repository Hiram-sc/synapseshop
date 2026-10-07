"""Repositório de pedidos.

Mesmo desenho de `UserRepository`: a camada de serviço nunca escreve query
direto. As consultas aqui são as que sustentam o fluxo da Aula 9 - buscar por
`idempotency_key` (o POST repetido devolve o mesmo pedido) e carregar os itens
de catálogo que podem entrar num pedido.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from django.db import transaction

from repositories.models import Item, Pedido, PedidoItem, User


class PedidoJaExiste(Exception):
    """Outra requisição criou o mesmo `(usuario, idempotency_key)` antes."""

    def __init__(self, pedido: Optional[Pedido]) -> None:
        super().__init__("pedido já existente para esta idempotency_key")
        self.pedido = pedido


class PedidoRepository:
    def get_by_id(self, pedido_id: int) -> Optional[Pedido]:
        return Pedido.objects.filter(pk=pedido_id).first()

    def get_by_idempotency_key(
        self, usuario: User, idempotency_key: str
    ) -> Optional[Pedido]:
        return Pedido.objects.filter(
            usuario=usuario, idempotency_key=idempotency_key
        ).first()

    def get_itens_catalogo(self, item_ids: Sequence[int]) -> Dict[int, Item]:
        """Itens do catálogo pelos ids pedidos, já com o preço atual.

        Só os **existentes** voltam: quem valida se falta algum é o serviço.
        """
        return {
            item.pk: item
            for item in Item.objects.filter(pk__in=list(item_ids))
        }

    @transaction.atomic
    def criar_com_itens(
        self,
        *,
        usuario: User,
        idempotency_key: str,
        total: Decimal,
        linhas: Sequence[dict],
    ) -> Pedido:
        """Grava o pedido e as linhas em uma única transação.

        `linhas` já traz quantidade, preço congelado e nome congelado,
        resolvidos pelo serviço a partir do catálogo - o repositório apenas
        persiste. A violação de `UNIQUE (usuario, idempotency_key)` sobe como
        `IntegrityError` para o serviço resolver (corrida entre duas requisições).
        """
        pedido = Pedido.objects.create(
            usuario=usuario,
            idempotency_key=idempotency_key,
            total=total,
        )
        PedidoItem.objects.bulk_create(
            [
                PedidoItem(
                    pedido=pedido,
                    item_id=linha["item_id"],
                    quantidade=linha["quantidade"],
                    preco_unitario=linha["preco_unitario"],
                    nome_item=linha["nome_item"],
                )
                for linha in linhas
            ]
        )
        return pedido
