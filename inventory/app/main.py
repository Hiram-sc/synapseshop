"""Aplicação FastAPI do microsserviço de inventário.

Scaffold: endpoints estruturados com tipagem estática e
documentação automática (OpenAPI) disponível em `/docs`.
"""

from __future__ import annotations

from fastapi import FastAPI

from .routes import router as inventory_router
from .schemas import HealthResponse

app = FastAPI(
    title="SynapseShop Inventory API",
    description=(
        "Microsserviço complementar de estoque (inventory) do SynapseShop. "
        "Endpoints estruturados com modelos Pydantic e respostas padrão. "
        "Documentação interativa em /docs."
    ),
    version="0.1.0",
)

app.include_router(inventory_router)


@app.get(
    "/",
    tags=["info"],
    summary="Informações do serviço",
)
def root() -> dict[str, str]:
    return {"service": "synapseshop-inventory", "docs": "/docs", "version": app.version}


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["monitoring"],
    summary="Monitoramento de disponibilidade",
)
def health() -> HealthResponse:
    return HealthResponse(status="ok")