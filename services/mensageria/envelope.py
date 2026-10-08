"""Envelope dos eventos do SynapseShop.

O envelope é o contrato entre produtor e consumidor: campos de metadados
para rastreio/deduplicação mais os dados do evento. Três eventos vivem aqui
(Aula 11 consolidou o fluxo), e duas regras estão concentradas neste módulo:

* **`event_type` e `version` são validados antes de qualquer escrita no
  banco** - `validar_envelope` é chamada pelo consumidor logo na entrada, e um
  contrato desconhecido vai para a DLQ em vez de virar efeito colateral. A
  validação é genérica: o registro `EVENTOS` mapeia cada `event_type` para a
  sua versão e para o validador dos `dados`.
* **Valores monetários são string, nunca float.** `99.90` em IEEE-754 não é
  representável exatamente e soma errado; em `Decimal` é. O JSON carrega
  `"99.90"` e quem lê faz `Decimal(valor)`.

Eventos suportados:

* `PedidoCriado` - Aula 9/10, inalterado;
* `PagamentoProcessado` - um único evento com o resultado em
  `dados.status` (`APROVADO`/`RECUSADO`), nunca eventos por desfecho;
* `NotificacaoEnviada` - terminal, publicado pelo notificacao-worker.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any, Dict, List

from django.utils import timezone

EVENTO_TYPE = "PedidoCriado"
EVENTO_VERSION = "1.0"
PAGAMENTO_PROCESSADO = "PagamentoProcessado"
NOTIFICACAO_ENVIADA = "NotificacaoEnviada"
EVENTO_VERSION_NOVOS = "1.0"

#: desfechos aceitos em `dados.status` de `PagamentoProcessado`. Espelha
#: `repositories.models.StatusPagamento` sem importar o modelo: este módulo
#: também é carregado por scripts que não inicializam o Django.
STATUS_PAGAMENTO = ("APROVADO", "RECUSADO")

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


def montar_pagamento_processado(pagamento, pedido) -> Dict[str, Any]:
    """Monta o envelope `PagamentoProcessado` após o pagamento persistir.

    Um único evento para os dois desfechos: `dados.status` carrega
    `APROVADO` ou `RECUSADO`. `idempotency_key` é derivada do pedido
    (`pagamento:{pedido_id}`), nunca do pagamento - é a chave que impede a
    segunda tentativa de publicar de novo, e `correlation_id` mantém o elo
    com a requisição original do cliente.
    """
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": PAGAMENTO_PROCESSADO,
        "version": EVENTO_VERSION_NOVOS,
        "occurred_at": timezone.now().isoformat(),
        "correlation_id": pedido.idempotency_key,
        "idempotency_key": f"pagamento:{pedido.pk}",
        "dados": {
            "status": pagamento.status,
            "pedido": {
                "id": pedido.pk,
                "status": pedido.status,
                "usuario_id": pedido.usuario_id,
                "total": decimal_para_str(pedido.total),
            },
            "pagamento": {
                "id": pagamento.pk,
                "status": pagamento.status,
                "created_at": pagamento.created_at.isoformat(),
            },
        },
    }


def montar_notificacao_enviada(notificacao, pagamento, pedido) -> Dict[str, Any]:
    """Monta o envelope `NotificacaoEnviada` após o registro persistir.

    Evento terminal (nada o consome ainda): sai no broker para observabilidade
    e fecha o fluxo Pedido -> Pagamento -> Notificação. `idempotency_key` é
    derivada do pagamento - se o consumo do `PagamentoProcessado` for
    reentregue, a idempotência impede o segundo registro e, com ele, a segunda
    publicação.
    """
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": NOTIFICACAO_ENVIADA,
        "version": EVENTO_VERSION_NOVOS,
        "occurred_at": timezone.now().isoformat(),
        "correlation_id": pedido.idempotency_key,
        "idempotency_key": f"notificacao:{pagamento.pk}",
        "dados": {
            "notificacao": {
                "id": notificacao.pk,
                "mensagem": notificacao.mensagem,
                "created_at": notificacao.created_at.isoformat(),
            },
            "pagamento": {
                "id": pagamento.pk,
                "status": pagamento.status,
            },
            "pedido": {
                "id": pedido.pk,
                "status": pedido.status,
            },
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


def _validar_dados_pedido_criado(dados: Dict[str, Any]) -> None:
    """Contrato dos `dados` do `PedidoCriado` (Aula 9, inalterado)."""
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


def _validar_dados_pagamento_processado(dados: Dict[str, Any]) -> None:
    """Contrato dos `dados` do `PagamentoProcessado`.

    `status` é o desfecho do pagamento (o evento é um só, com o resultado
    dentro); `pedido` e `pagamento` são os dois vínculos que o worker de
    notificação precisa para registrar o efeito.
    """
    status = dados.get("status")
    if status not in STATUS_PAGAMENTO:
        raise EnvelopeInvalido(
            f"dados.status deve ser um de {list(STATUS_PAGAMENTO)} (recebido {status!r})"
        )

    pedido = dados.get("pedido")
    if not isinstance(pedido, dict) or not isinstance(pedido.get("id"), int):
        raise EnvelopeInvalido("dados.pedido.id deve ser um inteiro")

    pagamento = dados.get("pagamento")
    if not isinstance(pagamento, dict) or not isinstance(pagamento.get("id"), int):
        raise EnvelopeInvalido("dados.pagamento.id deve ser um inteiro")


def _validar_dados_notificacao_enviada(dados: Dict[str, Any]) -> None:
    """Contrato dos `dados` do `NotificacaoEnviada` (evento terminal)."""
    notificacao = dados.get("notificacao")
    if not isinstance(notificacao, dict) or not isinstance(
        notificacao.get("id"), int
    ):
        raise EnvelopeInvalido("dados.notificacao.id deve ser um inteiro")

    pagamento = dados.get("pagamento")
    if not isinstance(pagamento, dict) or not isinstance(pagamento.get("id"), int):
        raise EnvelopeInvalido("dados.pagamento.id deve ser um inteiro")

    pedido = dados.get("pedido")
    if not isinstance(pedido, dict) or not isinstance(pedido.get("id"), int):
        raise EnvelopeInvalido("dados.pedido.id deve ser um inteiro")


#: registro de eventos suportados: versão e validador dos `dados`. Novo
#: evento entra aqui - e só aqui - com a sua versão e o seu contrato.
EVENTOS: Dict[str, Dict[str, Any]] = {
    EVENTO_TYPE: {
        "version": EVENTO_VERSION,
        "validar_dados": _validar_dados_pedido_criado,
    },
    PAGAMENTO_PROCESSADO: {
        "version": EVENTO_VERSION_NOVOS,
        "validar_dados": _validar_dados_pagamento_processado,
    },
    NOTIFICACAO_ENVIADA: {
        "version": EVENTO_VERSION_NOVOS,
        "validar_dados": _validar_dados_notificacao_enviada,
    },
}


def validar_envelope(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Valida o contrato do evento e devolve o envelope.

    Campos de metadados são iguais para todos os eventos; `event_type`
    decide qual versão e qual validador de `dados` entram em jogo (registro
    `EVENTOS`). Levanta `EnvelopeInvalido` com o motivo exato - é ele que
    aparece no log e justifica o encaminhamento à DLQ quando o contrato está
    quebrado.
    """
    for campo in CAMPOS_OBRIGATORIOS:
        if campo not in payload:
            raise EnvelopeInvalido(f"campo obrigatório ausente: {campo}")

    event_type = _exige_str(payload, "event_type")
    registro = EVENTOS.get(event_type)
    if registro is None:
        raise EnvelopeInvalido(
            f"event_type desconhecido: {event_type!r} "
            f"(esperado um de {sorted(EVENTOS)})"
        )

    version = _exige_str(payload, "version")
    if version != registro["version"]:
        raise EnvelopeInvalido(
            f"version não suportada: {version!r} (esperada {registro['version']!r})"
        )

    for campo in ("event_id", "occurred_at", "correlation_id", "idempotency_key"):
        _exige_str(payload, campo)

    dados = payload.get("dados")
    if not isinstance(dados, dict):
        raise EnvelopeInvalido("dados deve ser um objeto")

    registro["validar_dados"](dados)
    return payload
