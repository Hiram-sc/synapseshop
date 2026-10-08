"""Consumidor Kafka com especificação de evento injetável (Aula 10/11).

Espelha a máquina de estados do consumidor RabbitMQ da Aula 9, trocando o
handshake de fila pela semântica de offset do Kafka:

```text
validar contrato -> checar idempotência -> (duplicada? commit)
        -> falha forçada? -> efeito no pedido -> registrar idempotência -> commit
        -> falhou? sleep(backoff) -> republicar com x-retry-count -> commit
                                   -> republicação falhou? SEM commit (reentrega)
        -> tentativas esgotadas? publicar na DLQ -> commit
```

O que varia entre eventos está na `EspecEvento` (tópico, DLQ, group, efeito);
o resto - máquina de estados, backoff, DLQ, commit manual - é o mesmo para
`PedidoCriado` e `PagamentoProcessado`. `rodar_consumidor` usa a especificação
do `PedidoCriado` por padrão, então o pedido-worker da Aula 10 continua
chamando `rodar_consumidor(deve_parar=...)` sem mudar.

Decisões que explicam o desenho:

* **`enable.auto.commit=False` e commit manual sempre.** O offset só avança
  depois do processamento (ou da confirmação da reentrega/DLQ). Nunca por
  antecipação: com auto-commit, o offset avançaria antes do efeito e uma falha
  perderia a mensagem.
* **Retry por republicação, igual à Aula 9.** Re-publicar no tópico principal
  com `x-retry-count` incrementado e dormir `250ms -> 500ms -> 1000ms`; só
  depois da republicação confirmada o offset original é commitado. Sem
  commit, na próxima reconexão/rebalance a mensagem é reentregue — a
  mensagem nunca some.
* **Mesma chave de partição no retry.** `idempotency_key` leva o retry à
  mesma partição da original: a ordem por chave de negócio é preservada.
* **DLQ é publicação no tópico morto da própria especificação** (ex.:
  `pedidos.pedidocriado.dlq`), mantendo a payload original (inclusive contrato
  inválido) e o contador. Tópico morto **sem consumidor**: reprocessar é
  decisão operacional.
* **Contrato quebrado não faz retry.** Vai direto para a DLQ, idem Aula 9 —
  inclusive quando o `event_type` é válido, mas não é o esperado para o
  tópico (mensagem publicada no tópico errado).
* **O efeito vem na especificação** (`consumidor._executar_efeito` para o
  pedido, o efeito do worker de notificação para o pagamento): são puros, não
  tocam no canal do broker.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from confluent_kafka import KafkaError, Message
from confluent_kafka import Consumer

from services.mensageria import config, idempotencia, produtor_kafka, topologia_kafka
from services.mensageria.consumidor import _executar_efeito, _falha_forcada
from services.mensageria.envelope import (
    EVENTO_TYPE,
    EnvelopeInvalido,
    desserializar,
    serializar,
    validar_envelope,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EspecEvento:
    """Tudo que varia entre eventos no consumo Kafka.

    * `event_type` - contrato esperado no tópico; mensagem de outro evento é
      contrato inválida e vai para a DLQ;
    * `topico` / `topico_dlq` - onde consumir e para onde mortas as
      mensagens do evento;
    * `group_id` / `cliente_id` - cada worker com o seu group (histórico de
      offset independente) e o seu client.id rastreável;
    * `efeito` - função pura que aplica o evento no banco e devolve o pedido
      afetado (ou `None`), usado também no log e no registro de idempotência.
    """

    event_type: str
    topico: str
    topico_dlq: str
    group_id: str
    cliente_id: str
    efeito: Callable[[Dict[str, Any]], Any]


def espec_pedido_criado() -> EspecEvento:
    """Especificação do `PedidoCriado` (Aula 10) - default de `rodar_consumidor`."""
    return EspecEvento(
        event_type=EVENTO_TYPE,
        topico=config.KAFKA_TOPIC_PEDIDO_CRIADO,
        topico_dlq=config.KAFKA_TOPIC_DLQ,
        group_id=config.KAFKA_GROUP_ID,
        cliente_id=f"{config.KAFKA_CLIENT_ID}-consumidor",
        efeito=_executar_efeito,
    )


def _log(mensagem: str, *, nome_evento: str, **campos: Any) -> None:
    """Log estruturado (uma linha JSON), mesmo formato da Aula 9."""
    registro = {"nome_evento": nome_evento, "mensagem": mensagem}
    registro.update(campos)
    logger.info("%s", json.dumps(registro, ensure_ascii=False, default=str))


def _ler_retry_count(cabecalhos: Optional[List[tuple]]) -> int:
    """Lê `x-retry-count` dos cabeçalhos; sem cabeçalho é a tentativa inicial."""
    if not cabecalhos:
        return 0
    mapa: Dict[str, Any] = {}
    for chave, valor in cabecalhos:
        nome = chave.decode("utf-8") if isinstance(chave, bytes) else chave
        mapa[nome] = valor
    bruto = mapa.get(config.HEADER_RETRY)
    if bruto is None:
        return 0
    try:
        return int(bruto.decode("utf-8") if isinstance(bruto, bytes) else bruto)
    except (TypeError, ValueError, UnicodeDecodeError):
        return 0


def _criar_consumidor(espec: EspecEvento) -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS,
            "security.protocol": config.KAFKA_SECURITY_PROTOCOL,
            "group.id": espec.group_id,
            "client.id": espec.cliente_id,
            # commit manual, depois do processamento: auto-commit anteciparia o
            # offset e perderia a mensagem numa falha entre entrega e efeito
            "enable.auto.commit": config.KAFKA_ENABLE_AUTO_COMMIT,
            "auto.offset.reset": config.KAFKA_AUTO_OFFSET_RESET,
        }
    )


def _republicar(
    produtor,
    envelope: Dict[str, Any],
    *,
    topico: str,
    chave: str,
    tentativa: int,
) -> Optional[Exception]:
    """Publica o envelope no tópico principal com `x-retry-count` atualizado."""
    cabecalhos: List[tuple] = [
        (config.HEADER_RETRY, str(tentativa).encode("utf-8")),
        ("motivo", "reprocessamento".encode("utf-8")),
        ("event-id", str(envelope.get("event_id", "")).encode("utf-8")),
        ("correlation-id", str(envelope.get("correlation_id", "")).encode("utf-8")),
    ]
    return produtor_kafka._produzir(
        produtor,
        topico,
        serializar(envelope),
        chave,
        cabecalhos,
    )


def _enviar_dlq(
    produtor,
    message: Message,
    *,
    topico_dlq: str,
    chave: str,
    tentativa: int,
    motivo: str,
) -> Optional[Exception]:
    """Publica a mensagem original (payload cru) no tópico da DLQ.

    A payload é `message.value()`: mesmo contrato inválido é preservado para
    inspeção operacional. Cabeçalhos originais são mantidos, com
    `x-retry-count` e `motivo` atualizados.
    """
    cabecalhos: List[tuple] = []
    for nome, valor in message.headers() or []:
        chave_nome = nome.decode("utf-8") if isinstance(nome, bytes) else nome
        if chave_nome in (config.HEADER_RETRY, "motivo"):
            continue
        cabecalhos.append((chave_nome, valor))
    cabecalhos.append((config.HEADER_RETRY, str(tentativa).encode("utf-8")))
    cabecalhos.append(("motivo", motivo.encode("utf-8")))
    return produtor_kafka._produzir(
        produtor,
        topico_dlq,
        message.value(),
        chave,
        cabecalhos,
    )


def _commit_seguro(consumer: Consumer, message: Message, *, nome_evento: str) -> None:
    try:
        consumer.commit(message=message)
    except Exception as erro:  # noqa: BLE001
        _log(
            "falha ao confirmar o offset",
            nome_evento=nome_evento,
            particao=message.partition(),
            offset=message.offset(),
            motivo=str(erro),
            resultado="commit_falhou",
        )


def _tratar_falha(
    *,
    consumer: Consumer,
    message: Message,
    produtor,
    envelope: Dict[str, Any],
    erro: Exception,
    tentativa: int,
    duracao_ms: int,
    chave: str,
    espec: EspecEvento,
) -> None:
    """Retry com backoff, ou DLQ quando as tentativas se esgotam."""
    nome_evento = espec.topico
    idempotency_key = envelope.get("idempotency_key")
    event_id = envelope.get("event_id")
    pedido_id = envelope.get("dados", {}).get("pedido", {}).get("id")

    if tentativa >= config.MAX_TENTATIVAS:
        _log(
            "tentativas esgotadas; encaminhando para a DLQ",
            nome_evento=nome_evento,
            event_id=event_id,
            idempotency_key=idempotency_key,
            pedido=pedido_id,
            tentativa=tentativa,
            max_tentativas=config.MAX_TENTATIVAS,
            duracao_ms=duracao_ms,
            resultado="dlq",
            motivo=str(erro),
        )
        falha = _enviar_dlq(
            produtor,
            message,
            topico_dlq=espec.topico_dlq,
            chave=chave,
            tentativa=tentativa,
            motivo=str(erro),
        )
        if falha is None:
            # mensagem morta entregue à DLQ: o offset pode avançar
            _commit_seguro(consumer, message, nome_evento=nome_evento)
        else:
            _log(
                "DLQ rejeitou a mensagem; offset em aberto para reentrega",
                nome_evento=nome_evento,
                event_id=event_id,
                idempotency_key=idempotency_key,
                duracao_ms=duracao_ms,
                resultado="dlq_falhou",
                motivo=str(falha),
            )
        return

    backoff_s = config.BACKOFF_BASE_S * (2 ** (tentativa - 1))
    _log(
        "falha no processamento; programando reentrega",
        nome_evento=nome_evento,
        event_id=event_id,
        idempotency_key=idempotency_key,
        pedido=pedido_id,
        tentativa=tentativa,
        proxima_tentativa=tentativa + 1,
        backoff_ms=int(backoff_s * 1000),
        duracao_ms=duracao_ms,
        resultado="retry",
        motivo=str(erro),
    )

    # backoff acontece antes da republicação: o offset da original continua em
    # aberto durante a espera, então uma queda do worker não a perde
    time.sleep(backoff_s)

    falha = _republicar(
        produtor,
        envelope,
        topico=nome_evento,
        chave=chave,
        tentativa=tentativa,
    )
    if falha is None:
        # só avança o offset da original depois da republicação confirmada
        _commit_seguro(consumer, message, nome_evento=nome_evento)
    else:
        _log(
            "republicação não confirmada; offset em aberto (reentrega na "
            "reconexão/rebalance)",
            nome_evento=nome_evento,
            event_id=event_id,
            idempotency_key=idempotency_key,
            tentativa=tentativa,
            duracao_ms=duracao_ms,
            resultado="retry_pendente",
            motivo=str(falha),
        )


def _contrato_invalido(
    *,
    consumer: Consumer,
    message: Message,
    produtor,
    espec: EspecEvento,
    motivo: str,
    chave: str,
    tentativa: int,
    duracao_ms: int,
) -> None:
    """Mensagem de contrato quebrado vai para a DLQ sem retry; commit se der certo."""
    _log(
        "contrato inválido; encaminhando para a DLQ sem retry",
        nome_evento=espec.topico,
        chave=chave,
        tentativa=tentativa,
        duracao_ms=duracao_ms,
        resultado="contrato_invalido",
        motivo=motivo,
    )
    falha = _enviar_dlq(
        produtor,
        message,
        topico_dlq=espec.topico_dlq,
        chave=chave,
        tentativa=tentativa,
        motivo=motivo,
    )
    if falha is None:
        _commit_seguro(consumer, message, nome_evento=espec.topico)
    else:
        _log(
            "DLQ rejeitou a mensagem malformada; offset em aberto para "
            "reentrega",
            nome_evento=espec.topico,
            chave=chave,
            duracao_ms=duracao_ms,
            resultado="dlq_falhou",
            motivo=str(falha),
        )


def processar_mensagem(
    consumer: Consumer, message: Message, produtor, espec: EspecEvento
) -> None:
    """Processa uma mensagem e decide commit, retry ou DLQ."""
    nome_evento = espec.topico
    chave = (message.key() or b"").decode("utf-8", "replace")
    tentativa = _ler_retry_count(message.headers()) + 1
    inicio = time.monotonic()

    def duracao_ms() -> int:
        return int((time.monotonic() - inicio) * 1000)

    # 1) contrato ---------------------------------------------------------
    try:
        envelope = validar_envelope(desserializar(message.value()))
        if envelope["event_type"] != espec.event_type:
            raise EnvelopeInvalido(
                f"event_type {envelope['event_type']!r} inesperado para o "
                f"tópico {nome_evento!r} (esperado {espec.event_type!r})"
            )
    except EnvelopeInvalido as erro:
        _contrato_invalido(
            consumer=consumer,
            message=message,
            produtor=produtor,
            espec=espec,
            motivo=erro.motivo,
            chave=chave,
            tentativa=tentativa,
            duracao_ms=duracao_ms(),
        )
        return

    idempotency_key = envelope["idempotency_key"]
    event_id = envelope["event_id"]
    pedido_id = envelope["dados"]["pedido"]["id"]

    # 2) idempotência -------------------------------------------------------
    if idempotencia.ja_processado(envelope["event_type"], idempotency_key):
        _log(
            "mensagem já processada; confirmada sem novo efeito",
            nome_evento=nome_evento,
            event_id=event_id,
            idempotency_key=idempotency_key,
            pedido=pedido_id,
            tentativa=tentativa,
            duracao_ms=duracao_ms(),
            resultado="duplicada",
        )
        _commit_seguro(consumer, message, nome_evento=nome_evento)
        return

    # 3) efeito + registro + commit -----------------------------------------
    try:
        _falha_forcada(idempotency_key)
        pedido = espec.efeito(envelope)
        idempotencia.registrar(
            event_type=envelope["event_type"],
            idempotency_key=idempotency_key,
            event_id=event_id,
            pedido=pedido,
        )
    except Exception as erro:  # noqa: BLE001 - qualquer erro vira retry
        _tratar_falha(
            consumer=consumer,
            message=message,
            produtor=produtor,
            envelope=envelope,
            erro=erro,
            tentativa=tentativa,
            duracao_ms=duracao_ms(),
            chave=chave,
            espec=espec,
        )
        return

    _log(
        "evento processado",
        nome_evento=nome_evento,
        event_id=event_id,
        idempotency_key=idempotency_key,
        pedido=getattr(pedido, "pk", None),
        tentativa=tentativa,
        duracao_ms=duracao_ms(),
        resultado="sucesso",
    )
    _commit_seguro(consumer, message, nome_evento=nome_evento)


def rodar_consumidor(
    *, deve_parar: Callable[[], bool], espec: Optional[EspecEvento] = None
) -> None:
    """Loop principal do worker Kafka; retorna quando `deve_parar()` é `True`.

    Sem `espec`, consome `PedidoCriado` no group do pedido-worker (Aula 10).
    """
    espec = espec or espec_pedido_criado()
    topologia_kafka.criar_topicos()
    produtor = produtor_kafka.obter_produtor()
    consumidor = _criar_consumidor(espec)
    consumidor.subscribe([espec.topico])

    logger.info(
        "Worker consumindo %s (group=%s, auto.offset.reset=%s, "
        "enable.auto.commit=%s, max_tentativas=%s, backoff_base=%ss)",
        espec.topico,
        espec.group_id,
        config.KAFKA_AUTO_OFFSET_RESET,
        config.KAFKA_ENABLE_AUTO_COMMIT,
        config.MAX_TENTATIVAS,
        config.BACKOFF_BASE_S,
    )

    try:
        while not deve_parar():
            message = consumidor.poll(1.0)
            if message is None:
                continue
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    # fim de leitura da partição no polling inicial: normal
                    continue
                _log(
                    "erro de polling do broker; mensagem não processada",
                    nome_evento=espec.topico,
                    particao=message.partition(),
                    offset=message.offset(),
                    resultado="erro_broker",
                    motivo=str(message.error()),
                )
                continue
            try:
                processar_mensagem(consumidor, message, produtor, espec)
            except Exception as erro:  # noqa: BLE001 - falha inesperada
                # offset em aberto: na próxima conexão/rebalance a mensagem é
                # reentregue; o log é o rastro operacional do defeito
                _log(
                    "falha inesperada ao processar; offset em aberto para "
                    "reentrega",
                    nome_evento=espec.topico,
                    particao=message.partition(),
                    offset=message.offset(),
                    resultado="erro_inesperado",
                    motivo=f"{type(erro).__name__}: {erro}",
                )
                time.sleep(0.2)
    finally:
        consumidor.close()
        logger.info("Worker encerrado.")
