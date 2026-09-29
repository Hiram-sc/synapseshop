"""Modelos Pydantic do microsserviço de inventário.

Especificam explicitamente os contratos de entrada (payloads), saída
(respostas padrão) e validações de cada endpoint.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Resposta padrão do endpoint de monitoramento `/health`."""

    status: str


class MessageResponse(BaseModel):
    """Resposta padrão para mensagens (ex.: recurso não encontrado)."""

    detail: str


class InventoryItem(BaseModel):
    """Item de inventário retornado pela API (response model tipado)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    quantity: int = Field(ge=0, description="Quantidade em estoque (não negativa).")
    created_at: datetime


class InventoryItemCreate(BaseModel):
    """Payload de criação de um item de inventário."""

    sku: str = Field(min_length=1, max_length=50, description="Código único do item (SKU).")
    name: str = Field(min_length=1, max_length=100, description="Nome do item.")
    quantity: int = Field(default=0, ge=0, description="Quantidade inicial em estoque.")


class InventoryItemUpdate(BaseModel):
    """Payload de atualização parcial de um item de inventário."""

    sku: Optional[str] = Field(
        default=None, min_length=1, max_length=50, description="Código único do item (SKU)."
    )
    name: Optional[str] = Field(
        default=None, min_length=1, max_length=100, description="Nome do item."
    )
    quantity: int = Field(default=None, ge=0, description="Quantidade em estoque.")