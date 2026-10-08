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

from typing import Any, Dict

from services.mensageria import config


def broker_ativo() -> str:
    """Nome do broker selecionado por `MENSAGERIA_BROKER`."""
    return config.MENSAGERIA_BROKER


def usando_kafka() -> bool:
    """`True` quando o broker ativo é o Apache Kafka."""
    return config.MENSAGERIA_BROKER == "kafka"


def publicar_pedido_criado(envelope: Dict[str, Any]) -> bool:
    """Publica o evento `PedidoCriado` no broker ativo.

    Contrato idêntico ao da Aula 9: devolve `True` somente com a confirmação
    do broker; qualquer falha vira `False` (o pedido já está persistido e a
    reposição do evento é decisão operacional). A view chama a fachada, nunca
    um broker específico.
    """
    if usando_kafka():
        from services.mensageria import produtor_kafka

        return produtor_kafka.publicar_pedido_criado(envelope)

    from services.mensageria import produtor

    return produtor.publicar_pedido_criado(envelope)