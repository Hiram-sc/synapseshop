"""Declaração da topologia RabbitMQ.

Topologia (exchange, filas e bindings):

```text
produtor --(pedidos.events / pedidos.pedidocriado)--> pedidos.pedidocriado
                                                          |
                                       falha definitiva (nack sem requeue)
                                                          v
                                              pedidos.dlx (direct)
                                                          |
                                                          v
                                            pedidos.pedidocriado.dlq
```

Todas as declarações são `durable` e, por serem idempotentes no próprio
RabbitMQ, podem ser reexecutadas a cada conexão: declarar de novo a mesma
entidade com os mesmos argumentos é um `no-op`; divergir de argumentos é erro
do broker (`PRECONDITION_FAILED`), e não silêncio.

A fila principal declara `x-dead-letter-exchange` com routing key explícito:
é assim que um `basic_nack(requeue=False)` chega à DLQ sem depender do
routing key original da mensagem.
"""

from __future__ import annotations

from services.mensageria import config

#: argumentos que fazem a fila principal dead-letterar para a DLQ
ARGUMENTOS_FILA = {
    "x-dead-letter-exchange": config.EXCHANGE_DLX,
    "x-dead-letter-routing-key": config.QUEUE_DLQ,
}


def declarar_topologia(channel) -> None:
    """Declara exchange, filas e bindings (idempotente)."""
    channel.exchange_declare(
        exchange=config.EXCHANGE_EVENTOS, exchange_type="direct", durable=True
    )
    channel.queue_declare(
        queue=config.QUEUE_PEDIDO_CRIADO,
        durable=True,
        arguments=ARGUMENTOS_FILA,
    )
    channel.queue_bind(
        queue=config.QUEUE_PEDIDO_CRIADO,
        exchange=config.EXCHANGE_EVENTOS,
        routing_key=config.ROUTING_KEY_PEDIDO_CRIADO,
    )

    # dead-letter: exchange direta ligada à DLQ, sem consumidor nesta etapa
    channel.exchange_declare(
        exchange=config.EXCHANGE_DLX, exchange_type="direct", durable=True
    )
    channel.queue_declare(queue=config.QUEUE_DLQ, durable=True)
    channel.queue_bind(
        queue=config.QUEUE_DLQ,
        exchange=config.EXCHANGE_DLX,
        routing_key=config.QUEUE_DLQ,
    )
