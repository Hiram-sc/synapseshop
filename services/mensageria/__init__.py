"""Mensageria assíncrona do SynapseShop (Aula 9).

Pacote com a topologia RabbitMQ, o envelope do evento `PedidoCriado`, o
produtor usado pela API e o consumidor usado pelo worker. Nada fora daqui fala
com o broker.
"""

from services.mensageria.envelope import (  # noqa: F401
    EVENTO_TYPE,
    EVENTO_VERSION,
    EnvelopeInvalido,
    montar_pedido_criado,
    validar_envelope,
)
from services.mensageria.produtor import publicar_pedido_criado  # noqa: F401
