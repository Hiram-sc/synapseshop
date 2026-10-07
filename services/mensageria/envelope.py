"""Envelope do evento `PedidoCriado`.

O envelope é o contrato entre a API (produtor) e o worker (consumidor): campos
de metadados para rastreio/deduplicação mais os dados do pedido. Duas regras
da spec estão concentradas aqui:

* **`event_type` e `version` são validados antes de qualquer escrita no
  banco** - `validar_envelope` é chamada pelo consumidor logo na entrada, e um
  contrato desconhecido vai para a DLQ em vez de virar efeito colateral.
* **Valores monetários são string, nunca float.** `99.90` em IEEE-754 não é
  representável exatamente e soma errado; em `Decimal` é. O JSON carrega
  `"99.90"` e quem lê faz `Decimal(valor)`.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any, Dict, List

from django.utils import timezone

EVENTO_TYPE = "PedidoCriado"
EVENTO_VERSION = "1.0"

#: campos obrigatórios do envelope
CAMPOS_OBRIGATORIOS = (
    "event_id",
    "event_type",
    "version",
    "occurred_at",
    "correlation_id",
    "idempotency_key",
    "dados",
)


class EnvelopeInvalido(Exception):
    """Contrato do evento violado; a mensagem é recusada antes do banco."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


def decimal_para_str(valor: Decimal) -> str:
    """`Decimal` -> string sem notação científica e sem cauda fantasma."""
    return format(valor, "f")


def montar_pedido_criado(pedido) -> Dict[str, Any]:
    """Monta o envelope `PedidoCriado` a partir de um pedido persistido.

    Chamada **após o commit**: o pedido já tem id, total e itens gravados.
    `correlation_id` recebe a própria `idempotency_key` porque ela identifica
    a requisição do cliente - é o elo que liga o POST ao evento e, no futuro,
    aos eventos derivados dele.
    """
    itens: List[Dict[str, Any]] = []
    for linha in pedido.itens.all():
        subtotal = linha.preco_unitario * linha.quantidade
        itens.append(
            {
                "item_id": linha.item_id,
                "nome": linha.nome_item,
                "quantidade": linha.quantidade,
                "preco_unitario": decimal_para_str(linha.preco_unitario),
                "subtotal": decimal_para_str(subtotal),
            }
        )

    return {
        "event_id": str(uuid.uuid4()),
        "event_type": EVENTO_TYPE,
        "version": EVENTO_VERSION,
        "occurred_at": timezone.now().isoformat(),
        "correlation_id": pedido.idempotency_key,
        "idempotency_key": pedido.idempotency_key,
        "dados": {
            "pedido": {
                "id": pedido.pk,
                "usuario_id": pedido.usuario_id,
                "status": pedido.status,
                "created_at": pedido.created_at.isoformat(),
                "total": decimal_para_str(pedido.total),
                "itens": itens,
            }
        },
    }


def serializar(envelope: Dict[str, Any]) -> bytes:
    """JSON canônico do envelope, em UTF-8, pronto para o corpo da mensagem."""
    return json.dumps(
        envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def desserializar(corpo: bytes) -> Dict[str, Any]:
    """Lê o corpo da mensagem; qualquer lixo vira `EnvelopeInvalido`."""
    try:
        payload = json.loads(corpo.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as erro:
        raise EnvelopeInvalido(f"corpo não é JSON válido: {erro}") from erro
    if not isinstance(payload, dict):
        raise EnvelopeInvalido("corpo não é um objeto JSON")
    return payload


def _exige_str(campos: Dict[str, Any], nome: str) -> str:
    valor = campos.get(nome)
    if not isinstance(valor, str) or not valor.strip():
        raise EnvelopeInvalido(f"campo obrigatório ausente ou vazio: {nome}")
    return valor


def validar_envelope(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Valida o contrato do evento e devolve o envelope.

    Levanta `EnvelopeInvalido` com o motivo exato - é ele que aparece no log
    e justifica o encaminhamento à DLQ quando o contrato está quebrado.
    """
    for campo in CAMPOS_OBRIGATORIOS:
        if campo not in payload:
            raise EnvelopeInvalido(f"campo obrigatório ausente: {campo}")

    event_type = _exige_str(payload, "event_type")
    if event_type != EVENTO_TYPE:
        raise EnvelopeInvalido(
            f"event_type desconhecido: {event_type!r} (esperado {EVENTO_TYPE!r})"
        )

    version = _exige_str(payload, "version")
    if version != EVENTO_VERSION:
        raise EnvelopeInvalido(
            f"version não suportada: {version!r} (esperada {EVENTO_VERSION!r})"
        )

    for campo in ("event_id", "occurred_at", "correlation_id", "idempotency_key"):
        _exige_str(payload, campo)

    dados = payload.get("dados")
    if not isinstance(dados, dict):
        raise EnvelopeInvalido("dados deve ser um objeto")

    pedido = dados.get("pedido")
    if not isinstance(pedido, dict):
        raise EnvelopeInvalido("dados.pedido deve ser um objeto")

    for campo in ("id", "total"):
        if campo not in pedido:
            raise EnvelopeInvalido(f"dados.pedido.{campo} é obrigatório")

    total = pedido.get("total")
    if not isinstance(total, str):
        raise EnvelopeInvalido("dados.pedido.total deve ser string (não float)")

    itens = pedido.get("itens")
    if not isinstance(itens, list) or not itens:
        raise EnvelopeInvalido("dados.pedido.itens deve ser lista não vazia")

    for indice, linha in enumerate(itens):
        if not isinstance(linha, dict):
            raise EnvelopeInvalido(f"dados.pedido.itens[{indice}] deve ser objeto")
        if not isinstance(linha.get("nome"), str):
            raise EnvelopeInvalido(
                f"dados.pedido.itens[{indice}].nome deve ser string"
            )
        if not isinstance(linha.get("preco_unitario"), str):
            raise EnvelopeInvalido(
                f"dados.pedido.itens[{indice}].preco_unitario deve ser string"
            )
        quantidade = linha.get("quantidade")
        if not isinstance(quantidade, int) or isinstance(quantidade, bool):
            raise EnvelopeInvalido(
                f"dados.pedido.itens[{indice}].quantidade deve ser inteiro"
            )

    return payload
