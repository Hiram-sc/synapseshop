"""Consumidor do evento `PedidoCriado`.

Responsabilidade deste módulo é a máquina de estados de **uma mensagem**:

```text
validar contrato -> checar idempotência -> (duplicada? ack)
        -> falha forçada? -> efeito no pedido -> registrar idempotência -> ack
        -> falhou? sleep(backoff) -> republicar com x-retry-count -> ack
                                  -> republicação falhou? nack(requeue=True)
        -> tentativas esgotadas? nack(requeue=False) -> DLQ
```

Decisões que explicam o desenho:

* **Ack manual, sempre.** Só depois do processamento bem-sucedido (ou da
  confirmação da republicação). Mensagem nunca é confirmada por antecipação.
* **Retry por republicação, não por `nack(requeue=True)`.** Reenfileirar na
  hora devolve a mensagem imediatamente e não produz espera nenhuma - não há
  backoff. Republicar com `x-retry-count` incrementado permite medir a
  tentativa e dormir `250ms -> 500ms -> 1000ms` entre elas.
* **Se a republicação falhar, a original não some.** Ela segue não confirmada
  e é reenfileirada (`nack(requeue=True)`); no pior caso o broker a
  redeliveria após reconexão.
* **Contrato quebrado não faz retry.** Nenhuma tentativa conserta um JSON
  inválido ou `event_type` desconhecido: a mensagem vai direto para a DLQ,
  para inspeção operacional.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import time
from typing import Any, Dict

from django.utils import timezone

from repositories.models import Pedido, StatusPedido
from services.mensageria import config, idempotencia
from services.mensageria.envelope import (
    EnvelopeInvalido,
    desserializar,
    serializar,
    validar_envelope,
)

logger = logging.getLogger(__name__)


def _log(mensagem: str, *, nome_evento: str, **campos: Any) -> None:
    """Log estruturado (uma linha JSON) do fluxo de mensageria.

    O nome do campo é `nome_evento`, nunca `evento`: o parâmetro posicional
    já é a mensagem e um `evento=` repetido aqui foi justamente o defeito
    histórico de colisão de argumento.
    """
    registro = {"nome_evento": nome_evento, "mensagem": mensagem}
    registro.update(campos)
    logger.info("%s", json.dumps(registro, ensure_ascii=False, default=str))


class FalhaProcessamento(Exception):
    """Erro recuperável do processamento: dispara retry ou DLQ."""


class FalhaForcada(FalhaProcessamento):
    """Falha proposital, para validação do fluxo retry -> DLQ."""


def _falha_forcada(idempotency_key: str) -> None:
    """Levanta `FalhaForcada` se a chave casa com `PEDIDO_WORKER_FALHA_IDEM_KEYS`.

    Usa `fnmatch.fnmatchcase` **via módulo** (`fnmatch.fnmatchcase`), e não o
    import direto da função: foi assim que o defeito histórico de chamar a
    função como se fosse módulo foi evitado. Vazio por padrão - a falha
    forçada nunca fica ligada sozinha.
    """
    padroes = [
        padrao.strip()
        for padrao in config.FALHA_IDEM_KEYS.split(",")
        if padrao.strip()
    ]
    for padrao in padroes:
        if fnmatch.fnmatchcase(idempotency_key, padrao):
            raise FalhaForcada(
                f"falha forçada por PEDIDO_WORKER_FALHA_IDEM_KEYS (padrão {padrao!r})"
            )


def _executar_efeito(envelope: Dict[str, Any]) -> Pedido:
    """Persiste o estado do pedido - o efeito do evento.

    O efeito em si é idempotente (transição para `confirmado`), então uma
    corrida extrema entre dois consumidores no mesmo evento não duplica nada:
    o `UNIQUE` de `EventoProcessado` decide quem registra, e as duas
    escritariam o mesmo estado.
    """
    dados = envelope["dados"]["pedido"]
    pedido_id = dados["id"]

    pedido = Pedido.objects.filter(pk=pedido_id).first()
    if pedido is None:
        raise FalhaProcessamento(
            f"pedido {pedido_id} não existe no banco (reentrega órfã?)"
        )

    pedido.status = StatusPedido.CONFIRMADO
    pedido.processado_em = timezone.now()
    pedido.save(update_fields=["status", "processado_em"])
    return pedido


def _republicar(
    channel,
    envelope: Dict[str, Any],
    tentativa: int,
    properties,
) -> bool:
    """Republica a mensagem com `x-retry-count` atualizado.

    Com `confirm_delivery` no canal, o retorno só acontece com a confirmação
    do broker - é o que permite à chamada fazer o `ack` da original em
    seguida, sem perder mensagem.
    """
    headers = dict(properties.headers or {})
    headers[config.HEADER_RETRY] = tentativa

    import pika

    try:
        channel.basic_publish(
            exchange=config.EXCHANGE_EVENTOS,
            routing_key=config.ROUTING_KEY_PEDIDO_CRIADO,
            body=serializar(envelope),
            properties=pika.BasicProperties(
                content_type="application/json",
                content_encoding="utf-8",
                delivery_mode=2,
                message_id=envelope.get("event_id", ""),
                correlation_id=str(envelope.get("correlation_id", "")),
                type=config.ROUTING_KEY_PEDIDO_CRIADO,
                headers=headers,
            ),
            mandatory=True,
        )
        return True
    except Exception as erro:  # noqa: BLE001 - vira reenfileiramento seguro
        _log(
            "republicação não confirmada; a original será reenfileirada",
            nome_evento=config.ROUTING_KEY_PEDIDO_CRIADO,
            event_id=envelope.get("event_id"),
            idempotency_key=envelope.get("idempotency_key"),
            tentativa=tentativa,
            motivo=str(erro),
        )
        return False


def _tentativa_atual(properties) -> int:
    """Lê `x-retry-count` do cabeçalho; sem cabeçalho é a tentativa inicial."""
    headers = properties.headers or {}
    try:
        return int(headers.get(config.HEADER_RETRY, 0)) + 1
    except (TypeError, ValueError):
        return 1


def processar(channel, method, properties, body: bytes) -> None:
    """Processa uma mensagem e decide ack, retry ou DLQ.

    Único ponto que toca o canal: quem chama (o worker) só consome.
    """
    nome_evento = config.ROUTING_KEY_PEDIDO_CRIADO
    entrega = method.delivery_tag
    tentativa = _tentativa_atual(properties)
    inicio = time.monotonic()

    def duracao_ms() -> int:
        return int((time.monotonic() - inicio) * 1000)

    # 1) contrato ---------------------------------------------------------
    try:
        envelope = validar_envelope(desserializar(body))
    except EnvelopeInvalido as erro:
        _log(
            "contrato inválido; encaminhando para a DLQ sem retry",
            nome_evento=nome_evento,
            motivo=erro.motivo,
            tentativa=tentativa,
            duracao_ms=duracao_ms(),
            resultado="contrato_invalido",
        )
        _nack_seguro(channel, entrega, requeue=False)
        return

    idempotency_key = envelope["idempotency_key"]
    event_id = envelope["event_id"]

    # 2) idempotência -----------------------------------------------------
    if idempotencia.ja_processado(envelope["event_type"], idempotency_key):
        _log(
            "mensagem já processada; confirmada sem novo efeito",
            nome_evento=nome_evento,
            event_id=event_id,
            idempotency_key=idempotency_key,
            pedido=envelope["dados"]["pedido"]["id"],
            tentativa=tentativa,
            duracao_ms=duracao_ms(),
            resultado="duplicada",
        )
        _ack_seguro(channel, entrega)
        return

    # 3) efeito + registro + ack -----------------------------------------
    try:
        _falha_forcada(idempotency_key)
        pedido = _executar_efeito(envelope)
        idempotencia.registrar(
            event_type=envelope["event_type"],
            idempotency_key=idempotency_key,
            event_id=event_id,
            pedido=pedido,
        )
    except Exception as erro:  # noqa: BLE001 - qualquer erro vira retry
        _tratar_falha(
            channel=channel,
            entrega=entrega,
            envelope=envelope,
            properties=properties,
            erro=erro,
            tentativa=tentativa,
            duracao_ms=duracao_ms(),
            nome_evento=nome_evento,
        )
        return

    _log(
        "evento processado",
        nome_evento=nome_evento,
        event_id=event_id,
        idempotency_key=idempotency_key,
        pedido=pedido.pk,
        tentativa=tentativa,
        duracao_ms=duracao_ms(),
        resultado="sucesso",
    )
    _ack_seguro(channel, entrega)


def _tratar_falha(
    *,
    channel,
    entrega,
    envelope: Dict[str, Any],
    properties,
    erro: Exception,
    tentativa: int,
    duracao_ms: int,
    nome_evento: str,
) -> None:
    """Retry com backoff, ou DLQ quando as tentativas se esgotam."""
    idempotency_key = envelope.get("idempotency_key")
    event_id = envelope.get("event_id")

    if tentativa >= config.MAX_TENTATIVAS:
        _log(
            "tentativas esgotadas; encaminhando para a DLQ",
            nome_evento=nome_evento,
            event_id=event_id,
            idempotency_key=idempotency_key,
            pedido=envelope.get("dados", {}).get("pedido", {}).get("id"),
            tentativa=tentativa,
            max_tentativas=config.MAX_TENTATIVAS,
            duracao_ms=duracao_ms,
            resultado="dlq",
            motivo=str(erro),
        )
        _nack_seguro(channel, entrega, requeue=False)
        return

    backoff_s = config.BACKOFF_BASE_S * (2 ** (tentativa - 1))
    _log(
        "falha no processamento; programando reentrega",
        nome_evento=nome_evento,
        event_id=event_id,
        idempotency_key=idempotency_key,
        tentativa=tentativa,
        proxima_tentativa=tentativa + 1,
        backoff_ms=int(backoff_s * 1000),
        duracao_ms=duracao_ms,
        resultado="retry",
        motivo=str(erro),
    )

    # backoff acontece antes da republicação: a mensagem original continua
    # não confirmada durante a espera, então uma queda do worker no meio do
    # sleep não a perde
    time.sleep(backoff_s)

    if _republicar(channel, envelope, tentativa, properties):
        # só confirma a original depois da republicação confirmada
        _ack_seguro(channel, entrega)
    else:
        # sem confirmação da republicação: reenfileira a original para não
        # perder a mensagem (caminho seguro, ainda sem backoff adicional)
        _nack_seguro(channel, entrega, requeue=True)


def _ack_seguro(channel, entrega) -> None:
    try:
        channel.basic_ack(delivery_tag=entrega)
    except Exception as erro:  # noqa: BLE001
        _log(
            "falha ao confirmar a mensagem",
            nome_evento=config.ROUTING_KEY_PEDIDO_CRIADO,
            motivo=str(erro),
            resultado="ack_falhou",
        )


def _nack_seguro(channel, entrega, *, requeue: bool) -> None:
    try:
        channel.basic_nack(delivery_tag=entrega, requeue=requeue)
    except Exception as erro:  # noqa: BLE001
        # sem ack o broker redeliveria após reconexão: a mensagem não some
        _log(
            "falha ao dar nack",
            nome_evento=config.ROUTING_KEY_PEDIDO_CRIADO,
            requeue=requeue,
            motivo=str(erro),
            resultado="nack_falhou",
        )
