"""Testes de integração do contrato OpenAPI servido pela API (Aula 13).

Garantem que o que *de fato* é servido em `/api/schema/` (e consumido por
`/api/docs/` e `/api/redoc/`) seja estável e esteja alinhado às convenções
documentadas:

* é um documento OpenAPI 3.0.3 com o título/versão do projeto;
* expõe os componentes de erro, páginas e enums que o integrador usa
  (`Error`, `PaginatedItemList`, `PedidoStatus`, `PagamentoStatus`);
* autentica com `bearerAuth` (JWT) nas rotas protegidas e deixa públicas as
  rotas de health/documentação/login;
* documenta os dois health checks e o fluxo de pagamento.

A suíte compila o schema pela própria view (sem tocar o arquivo versionado),
então o teste não envelhece se o contrato mudar de lugar.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

SCHEMA_URL = "/api/schema/"
DOCS_URL = "/api/docs/"
REDOC_URL = "/api/redoc/"

#: rotas que exigem `bearerAuth` no contrato (escrever o contrato real da API:
#: catálogo é leitura pública/escrita admin, pedidos e `me` são autenticados).
ROTAS_PROTEGIDAS = {
    ("/api/v1/auth/me/", "get"),
    ("/api/v1/categories/", "post"),
    ("/api/v1/categories/{id}/", "put"),
    ("/api/v1/categories/{id}/", "patch"),
    ("/api/v1/categories/{id}/", "delete"),
    ("/api/v1/items/", "post"),
    ("/api/v1/items/{id}/", "put"),
    ("/api/v1/items/{id}/", "patch"),
    ("/api/v1/items/{id}/", "delete"),
    ("/api/v1/pedidos/", "post"),
    ("/api/v1/pedidos/{id}/", "get"),
    ("/api/v1/pedidos/{pedido_id}/pagamento/", "post"),
    ("/api/v1/assist/", "post"),
}

#: rotas explicitamente públicas (sem `security` no contrato).
ROTAS_PUBLICAS = {
    ("/health", "get"),
    ("/health/pronto", "get"),
    ("/api/v1/auth/login/", "post"),
    ("/api/v1/categories/", "get"),
    ("/api/v1/categories/{id}/", "get"),
    ("/api/v1/items/", "get"),
    ("/api/v1/items/{id}/", "get"),
}


def _schema(client) -> dict:
    import yaml

    resposta = client.get(SCHEMA_URL)
    assert resposta.status_code == 200, resposta.content
    conteudo = yaml.safe_load(resposta.content)
    assert conteudo["openapi"] == "3.0.3"
    return conteudo


def _security_de(conteudo: dict, rota: str, metodo: str) -> list | None:
    operacao = conteudo["paths"][rota][metodo]
    return operacao.get("security")


def test_schema_e_openapi_30_com_metadados(client):
    conteudo = _schema(client)
    assert conteudo["info"]["title"] == "SynapseShop API"
    assert conteudo["info"]["version"] == "1.0.0"
    assert conteudo["info"].get("description"), "a descrição documenta as convenções"
    assert conteudo["servers"] and conteudo["servers"][0].get("url")


def test_schema_declara_os_componentes_do_contrato(client):
    conteudo = _schema(client)
    componentes = conteudo["components"]["schemas"]
    for nome in (
        "Error",
        "PaginatedItemList",
        "PaginatedCategoryList",
        "PedidoStatus",
        "PagamentoStatus",
    ):
        assert nome in componentes, f"esperava o componente {nome!r}"


def test_bearer_auth_declarado(client):
    conteudo = _schema(client)
    auth = conteudo["components"]["securitySchemes"]["bearerAuth"]
    assert auth["type"] == "http"
    assert auth["scheme"] == "bearer"
    assert auth["bearerFormat"] == "JWT"


def test_rotas_protegidas_exigem_bearer_auth(client):
    conteudo = _schema(client)
    for rota, metodo in ROTAS_PROTEGIDAS:
        assert rota in conteudo["paths"], f"rota {rota} ausente no contrato"
        assert _security_de(conteudo, rota, metodo) == [{"bearerAuth": []}], (
            f"{metodo.upper()} {rota} deveria exigir bearerAuth"
        )


def test_rotas_publicas_nao_exigem_auth(client):
    conteudo = _schema(client)
    for rota, metodo in ROTAS_PUBLICAS:
        assert rota in conteudo["paths"], f"rota {rota} ausente no contrato"
        assert _security_de(conteudo, rota, metodo) in (None, []), (
            f"{metodo.upper()} {rota} deveria ser pública"
        )


def test_health_check_documentado_com_estados(client):
    conteudo = _schema(client)
    pronto = conteudo["paths"]["/health/pronto"]["get"]
    assert pronto["summary"]
    assert pronto["responses"]["200"]["description"]
    assert pronto["responses"]["503"]["description"]


def test_pagamento_documenta_o_estado_ja_processado(client):
    conteudo = _schema(client)
    pagamento = conteudo["paths"]["/api/v1/pedidos/{pedido_id}/pagamento/"]["post"]
    assert "409" in pagamento["responses"], (
        "pagamento repetido deve declarar 409 no contrato"
    )
    referencia = pagamento["responses"]["409"]["content"]["application/json"]["schema"]
    assert "$ref" in referencia


def test_swagger_ui_servido_localmente(client):
    resposta = client.get(DOCS_URL)
    assert resposta.status_code == 200, resposta.content
    corpo = resposta.content.decode("utf-8")
    assert "swagger-ui" in corpo
    assert "swagger-ui-bundle.js" in corpo


def test_redoc_servido_localmente(client):
    resposta = client.get(REDOC_URL)
    assert resposta.status_code == 200, resposta.content
    corpo = resposta.content.decode("utf-8")
    assert "redoc.standalone.js" in corpo
