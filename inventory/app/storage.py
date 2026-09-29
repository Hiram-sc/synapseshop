"""Armazenamento em memória do microsserviço de inventário.

O scaffold não abstrai banco de dados: o estado é
mantido em dicionário dentro do processo.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from .schemas import InventoryItem, InventoryItemCreate, InventoryItemUpdate


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InventoryStore:
    """Repositório em memória com contador simples de identificadores."""

    def __init__(self) -> None:
        self._items: dict[int, InventoryItem] = {}
        self._next_id: int = 1

    def list_items(self) -> List[InventoryItem]:
        return list(self._items.values())

    def get_item(self, item_id: int) -> Optional[InventoryItem]:
        return self._items.get(item_id)

    def create_item(self, payload: InventoryItemCreate) -> InventoryItem:
        item = InventoryItem(
            id=self._next_id,
            sku=payload.sku,
            name=payload.name,
            quantity=payload.quantity,
            created_at=_utc_now(),
        )
        self._items[item.id] = item
        self._next_id += 1
        return item

    def update_item(self, item_id: int, payload: InventoryItemUpdate) -> Optional[InventoryItem]:
        current = self._items.get(item_id)
        if current is None:
            return None
        updated = current.model_copy(
            update=payload.model_dump(exclude_unset=True, exclude_defaults=True)
        )
        self._items[item_id] = updated
        return updated

    def delete_item(self, item_id: int) -> bool:
        return self._items.pop(item_id, None) is not None


_store: Optional[InventoryStore] = None


def get_store() -> InventoryStore:
    """Dependency do FastAPI: devolve a instância única do armazenamento."""
    global _store
    if _store is None:
        _store = InventoryStore()
    return _store