"""Modelo relacional do item de estoque do microsserviço de inventário.

Mapeia a entidade única do microsserviço para a tabela `inventory_items`.
As restrições de unicidade e de quantidade não negativa são garantidas pelo
próprio PostgreSQL, e não apenas pela validação da camada HTTP.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class InventoryItemModel(Base):
    """Item de estoque persistido na tabela `inventory_items`."""

    __tablename__ = "inventory_items"

    __table_args__ = (
        # a unicidade do SKU é nomeada para permitir identificar a violação
        UniqueConstraint("sku", name="uq_inventory_items_sku"),
        # defense no banco: a API também valida, mas a regra não depende dela
        CheckConstraint("quantity >= 0", name="ck_inventory_items_quantity_non_negative"),
        # sustenta a ordenação padrão da listagem e consultas por período
        Index("ix_inventory_items_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"InventoryItemModel(id={self.id}, sku={self.sku!r}, "
            f"quantity={self.quantity})"
        )
