"""Fachada da mensageria: escolhe o broker pelo ambiente.

A API e o worker **não** conversam com um broker específico: chamam a fachada,
e é ela que decide, por `MENSAGERIA_BROKER` (padrão `kafka` na Aula 10), qual
implementação acionar:

* `kafka`    -> Apache Kafka (`produtor_kafka`, `consumidor_kafka`);
* qualquer outro valor (ex.: `rabbitmq`) -> RabbitMQ da Aula 9 (`produtor`,
  `consumidor`), preservado como alternativa funcional.

Os imports são lazy dentro das funções: importar a fachada (e portanto o
pacote `services.mensageria`) não exige que uma das bibliotecas de broker
esteja instalada, e nem uma nem outra é carregada quando a outra estiver ativa.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from services.mensageria import config
from services.mensageria.envelope import EVENTO_TYPE

logger = logging.getLogger(__name__)


def broker_ativo() -> str:
    """Nome do broker selecionado por `MENSAGERIA_BROKER`."""
    return config.MENSAGERIA_BROKER


def usando_kafka() -> bool:
    """`True` quando o broker ativo é o Apache Kafka."""
    return config.MENSAGERIA_BROKER == "kafka"


def publicar_evento(envelope: Dict[str, Any]) -> bool:
    """Publica qualquer evento suportado no broker ativo.

    Contrato idêntico ao da Aula 9: devolve `True` somente com a
    confirmação do broker; qualquer falha vira `False` (o registro já está
    persistido e a reposição do evento é decisão operacional). A view chama a
    fachada, nunca um broker específico.

    A paridade de eventos vale por broker: no Kafka, todos os eventos do
    registro `EVENTOS` têm tópico; no RabbitMQ da Aula 9, só o
    `PedidoCriado` tem rota — os demais voltam `False` com log, para o
    chamador tratar como publicação não confirmada (nunca como sucesso
    silencioso).
    """
    event_type = envelope.get("event_type")

    if usando_kafka():
        from services.mensageria import produtor_kafka

        return produtor_kafka.publicar_evento(envelope)

    if event_type == EVENTO_TYPE:
        from services.mensageria import produtor

        return produtor.publicar_pedido_criado(envelope)

    logger.error(
        "evento %r não tem rota no broker %r (só o %r tem); nada foi publicado",
        event_type,
        broker_ativo(),
        EVENTO_TYPE,
    )
    return False


def publicar_pedido_criado(envelope: Dict[str, Any]) -> bool:
    """Publica o evento `PedidoCriado` no broker ativo (caminho da Aula 9/10).

    Delega para `publicar_evento`, que resolve o tópico/rota pelo
    `event_type` — para este evento o comportamento é o mesmo da Aula 9:
    `True` com confirmação, `False` em qualquer falha.
    """
    return publicar_evento(envelope)