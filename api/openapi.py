"""Hooks de pós-processamento do contrato OpenAPI (Aula 13).

Centraliza o que o drf-spectacular não infere sozinho do código, evitando
repetir ``@extend_schema`` em cada método:

* respostas de erro padrão (``401``/``403``/``429``) em toda operação,
  respeitando se a operação exige autenticação ou não;
* os endpoints de infraestrutura ``/health`` e ``/health/pronto``, que são
  views Django puras (não DRF) e por isso ficariam fora do schema.

O hook roda sobre o documento já montado (``postprocessing``): não altera
nenhum comportamento em tempo de execução.
"""

from __future__ import annotations

#: referência ao componente gerado a partir de `api.serializers.ErrorSerializer`
ESQUEMA_ERRO = {"$ref": "#/components/schemas/Error"}

_METODOS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _resposta_erro(descricao: str) -> dict:
    return {
        "description": descricao,
        "content": {"application/json": {"schema": ESQUEMA_ERRO}},
    }


def _resposta_health(descricao: str) -> dict:
    return {
        "description": descricao,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                }
            }
        },
    }


def _operacoes(result: dict):
    for item in result.get("paths", {}).values():
        for metodo, operacao in item.items():
            if metodo in _METODOS and isinstance(operacao, dict):
                yield operacao


def erros_e_infra(result, generator, request, public):
    """Adiciona respostas de erro padrão e documenta os endpoints de saúde."""
    for operacao in _operacoes(result):
        respostas = operacao.setdefault("responses", {})
        if operacao.get("security"):
            respostas.setdefault(
                "401",
                _resposta_erro(
                    "Autenticação ausente ou token inválido/expirado."
                ),
            )
            respostas.setdefault(
                "403",
                _resposta_erro("Autenticado, mas sem permissão para a operação."),
            )
        respostas.setdefault(
            "429",
            _resposta_erro(
                "Limite de requisições excedido. Consulte o header Retry-After."
            ),
        )

    _info_contato(result)
    _incluir_health(result)
    return result


def _info_contato(result: dict) -> None:
    info = result.setdefault("info", {})
    info.setdefault(
        "contact",
        {"name": "Equipe SynapseShop", "email": "api@synapseshop.dev"},
    )
    info.setdefault(
        "license",
        {"name": "Propriedade do curso (uso acadêmico) - sem licença pública."},
    )


def _incluir_health(result: dict) -> None:
    paths = result.setdefault("paths", {})
    paths.setdefault(
        "/health",
        {
            "get": {
                "tags": ["health"],
                "summary": "Liveness da API",
                "description": (
                    "Responde `{\"status\": \"ok\"}` enquanto o processo está "
                    "de pé, sem consultar nenhuma dependência."
                ),
                "operationId": "health_liveness",
                "responses": {
                    "200": _resposta_health("API viva (processo respondendo).")
                },
            }
        },
    )
    paths.setdefault(
        "/health/pronto",
        {
            "get": {
                "tags": ["health"],
                "summary": "Readiness da API",
                "description": (
                    "Responde 200 só quando PostgreSQL, Redis e o broker "
                    "respondem; qualquer dependência essencial fora responde 503."
                ),
                "operationId": "health_readiness",
                "responses": {
                    "200": _resposta_health(
                        "Pronto: PostgreSQL, Redis e broker respondem."
                    ),
                    "503": {
                        "description": (
                            "Alguma dependência essencial (PostgreSQL, Redis "
                            "ou broker) está indisponível."
                        ),
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "database": {"type": "string"},
                                        "redis": {"type": "string"},
                                        "broker": {"type": "string"},
                                        "status": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                },
            }
        },
    )
