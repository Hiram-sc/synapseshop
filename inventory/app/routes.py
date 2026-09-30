"""Rotas mínimas obrigatórias do microsserviço de inventário.

CRUD de itens de estoque sob o prefixo `/inventory/items`, com tipagem
estática (`response_model`), validação via modelos Pydantic e respostas
padrão (201 Created, 200 OK, 204 No Content, 404 Not Found, 409 Conflict).

As rotas não conhecem o SQLAlchemy: recebem um `InventoryStore` por
dependência e traduzem apenas os erros de domínio em respostas HTTP.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from .schemas import InventoryItem, InventoryItemCreate, InventoryItemUpdate
from .storage import DuplicateSkuError, InventoryStore, get_store

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get(
    "/items",
    response_model=List[InventoryItem],
    status_code=status.HTTP_200_OK,
    summary="Lista todos os itens de estoque",
)
def list_items(store: InventoryStore = Depends(get_store)) -> List[InventoryItem]:
    return store.list_items()


@router.post(
    "/items",
    response_model=InventoryItem,
    status_code=status.HTTP_201_CREATED,
    summary="Cria um novo item de estoque",
    responses={status.HTTP_409_CONFLICT: {"description": "SKU já cadastrado."}},
)
def create_item(
    payload: InventoryItemCreate, store: InventoryStore = Depends(get_store)
) -> InventoryItem:
    try:
        return store.create_item(payload)
    except DuplicateSkuError as erro:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(erro)
        ) from erro


@router.get(
    "/items/{item_id}",
    response_model=InventoryItem,
    status_code=status.HTTP_200_OK,
    summary="Detalha um item de estoque",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Item não encontrado."}},
)
def get_item(item_id: int, store: InventoryStore = Depends(get_store)) -> InventoryItem:
    item = store.get_item(item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item não encontrado."
        )
    return item


@router.patch(
    "/items/{item_id}",
    response_model=InventoryItem,
    status_code=status.HTTP_200_OK,
    summary="Atualiza parcialmente um item de estoque",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Item não encontrado."},
        status.HTTP_409_CONFLICT: {"description": "SKU já cadastrado."},
    },
)
def update_item(
    item_id: int,
    payload: InventoryItemUpdate,
    store: InventoryStore = Depends(get_store),
) -> InventoryItem:
    try:
        item = store.update_item(item_id, payload)
    except DuplicateSkuError as erro:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(erro)
        ) from erro
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item não encontrado."
        )
    return item


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove um item de estoque",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Item não encontrado."}},
)
def delete_item(item_id: int, store: InventoryStore = Depends(get_store)) -> None:
    if not store.delete_item(item_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item não encontrado."
        )