"""Camada de persistência do microsserviço de inventário.

Encapsula as operações de banco sobre a tabela `inventory_items`. Cada
operação de escrita abre e fecha a sua própria transação: em caso de violação
de integridade a sessão é revertida e a exceção vira um erro de domínio, para
que nem a rota nem o cliente precisem conhecer o SQLAlchemy.
"""

from __future__ import annotations

from typing import Iterator, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import InventoryItemModel
from .schemas import InventoryItemCreate, InventoryItemUpdate


class DuplicateSkuError(Exception):
    """Levantada quando o SKU informado já existe na base."""

    def __init__(self, sku: str) -> None:
        super().__init__(f"Já existe um item com o SKU {sku!r}.")
        self.sku = sku


class InventoryStore:
    """Repositório transacional dos itens de estoque."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_items(self) -> List[InventoryItemModel]:
        """Lista todos os itens, do mais recente para o mais antigo."""
        consulta = select(InventoryItemModel).order_by(
            InventoryItemModel.created_at.desc(), InventoryItemModel.id.desc()
        )
        return list(self._session.execute(consulta).scalars())

    def get_item(self, item_id: int) -> Optional[InventoryItemModel]:
        """Busca um item pela chave primária."""
        return self._session.get(InventoryItemModel, item_id)

    def get_item_by_sku(self, sku: str) -> Optional[InventoryItemModel]:
        """Busca um item pelo SKU, que possui índice único."""
        consulta = select(InventoryItemModel).where(InventoryItemModel.sku == sku)
        return self._session.execute(consulta).scalars().first()

    def create_item(self, payload: InventoryItemCreate) -> InventoryItemModel:
        """Cria um item e confirma a transação."""
        item = InventoryItemModel(
            sku=payload.sku,
            name=payload.name,
            quantity=payload.quantity,
        )
        self._session.add(item)
        try:
            self._session.commit()
        except IntegrityError as erro:
            self._session.rollback()
            raise _traduzir_erro(erro, payload.sku) from erro
        # recarrega para trazer created_at e updated_at, gerados pelo banco
        self._session.refresh(item)
        return item

    def update_item(
        self, item_id: int, payload: InventoryItemUpdate
    ) -> Optional[InventoryItemModel]:
        """Atualiza parcialmente um item e confirma a transação."""
        item = self.get_item(item_id)
        if item is None:
            return None

        for campo, valor in payload.model_dump(
            exclude_unset=True, exclude_defaults=True
        ).items():
            setattr(item, campo, valor)

        try:
            self._session.commit()
        except IntegrityError as erro:
            self._session.rollback()
            raise _traduzir_erro(erro, item.sku) from erro
        self._session.refresh(item)
        return item

    def delete_item(self, item_id: int) -> bool:
        """Remove um item e confirma a transação."""
        item = self.get_item(item_id)
        if item is None:
            return False
        self._session.delete(item)
        self._session.commit()
        return True


def _traduzir_erro(erro: IntegrityError, sku: str) -> Exception:
    """Converte violações de integridade do banco em erro de domínio."""
    mensagem = str(getattr(erro, "orig", erro))
    if "uq_inventory_items_sku" in mensagem:
        return DuplicateSkuError(sku)
    # qualquer outra violação de integridade sobe como erro de domínio neutro,
    # sem excrever detalhe interno do banco na resposta da API
    return ValueError("Operação recusada pelas regras de integridade do banco.")


def get_store() -> Iterator[InventoryStore]:
    """Dependency do FastAPI: devolve um repositório por requisição."""
    sessao = SessionLocal()
    try:
        yield InventoryStore(sessao)
    finally:
        sessao.close()
