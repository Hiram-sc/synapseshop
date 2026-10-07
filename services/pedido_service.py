"""Serviço de criação de pedidos.

Regras da Aula 9 concentradas aqui:

* **o total é calculado no servidor**, a partir do preço do catálogo no
  momento da compra - nunca aceitado do cliente;
* **preço e nome entram congelados** na linha do pedido, para o histórico não
  mudar quando o catálogo mudar;
* **item precisa existir e estar ativo**, e **não pode repetir** dentro do
  mesmo pedido;
* **`idempotency_key` identifica a requisição**: o POST repetido devolve o
  pedido já criado em vez de criar outro.

Nenhuma dessas regras escreve query: as consultas estão no repositório.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Sequence, Tuple

from django.db import IntegrityError

from repositories.models import Pedido, User
from repositories.pedido_repository import PedidoRepository
from services.mensageria.envelope import decimal_para_str


class PedidoInvalido(Exception):
    """Lista de erros de validação do pedido, na linguagem do cliente."""

    def __init__(self, erros: Sequence[str]) -> None:
        super().__init__("; ".join(erros))
        self.erros = list(erros)


@dataclass(frozen=True)
class LinhaValidada:
    item_id: int
    quantidade: int
    preco_unitario: Decimal
    nome_item: str

    @property
    def subtotal(self) -> Decimal:
        return self.preco_unitario * self.quantidade


class PedidoService:
    def __init__(self, pedidos: PedidoRepository | None = None) -> None:
        self._pedidos = pedidos or PedidoRepository()

    # -- validação ---------------------------------------------------------
    def _validar_itens(self, itens: Sequence[dict]) -> List[LinhaValidada]:
        erros: List[str] = []
        vistos: set[int] = set()

        for linha in itens:
            item_id = int(linha["item_id"])
            if item_id in vistos:
                erros.append(f"item {item_id} repetido no pedido")
            vistos.add(item_id)

        if erros:
            raise PedidoInvalido(erros)

        catalogo = self._pedidos.get_itens_catalogo(sorted(vistos))

        linhas: List[LinhaValidada] = []
        for linha in itens:
            item_id = int(linha["item_id"])
            quantidade = int(linha["quantidade"])
            if quantidade < 1:
                erros.append(f"quantidade do item {item_id} deve ser no mínimo 1")
                continue

            item = catalogo.get(item_id)
            if item is None:
                erros.append(f"item {item_id} não existe")
                continue
            if not item.is_active:
                erros.append(f"item {item_id} está inativo")
                continue

            linhas.append(
                LinhaValidada(
                    item_id=item.id,
                    quantidade=quantidade,
                    preco_unitario=item.price,
                    nome_item=item.name,
                )
            )

        if erros:
            raise PedidoInvalido(erros)
        return linhas

    # -- criação -----------------------------------------------------------
    def criar(
        self, usuario: User, idempotency_key: str, itens: Sequence[dict]
    ) -> Tuple[Pedido, bool]:
        """Cria o pedido; devolve `(pedido, criado)`.

        `criado=False` significa que esta `idempotency_key` já tinha pedido -
        nesse caso nada é publicado de novo, e o pedido existente é devolvido.
        """
        existente = self._pedidos.get_by_idempotency_key(usuario, idempotency_key)
        if existente is not None:
            return existente, False

        linhas = self._validar_itens(itens)
        total = sum((linha.subtotal for linha in linhas), Decimal("0.00"))

        try:
            pedido = self._pedidos.criar_com_itens(
                usuario=usuario,
                idempotency_key=idempotency_key,
                total=total,
                linhas=[
                    {
                        "item_id": linha.item_id,
                        "quantidade": linha.quantidade,
                        "preco_unitario": linha.preco_unitario,
                        "nome_item": linha.nome_item,
                    }
                    for linha in linhas
                ],
            )
        except IntegrityError:
            # corrida entre duas requisições com a mesma chave: quem perdeu
            # devolve o pedido de quem venceu
            existente = self._pedidos.get_by_idempotency_key(
                usuario, idempotency_key
            )
            if existente is not None:
                return existente, False
            raise

        return pedido, True


def total_formatado(pedido: Pedido) -> str:
    """Total como string decimal, igual ao formato do evento."""
    return _decimal_para_str(pedido.total)
