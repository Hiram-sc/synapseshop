"""Mensageria assíncrona do SynapseShop (Aulas 9 e 10).

Pacote com os dois brokers: o RabbitMQ da Aula 9 (topologia, envelope,
produtor, consumidor) e o Apache Kafka da Aula 10 (produtor, consumidor e
criação de tópicos), selecionados pela fachada conforme `MENSAGERIA_BROKER`
(padrão `kafka`). Nada fora daqui fala com o broker: produtores (API) e
consumidores (worker) passam pela fachada.
"""

from services.mensageria.envelope import (  # noqa: F401
    EVENTO_TYPE,
    EVENTO_VERSION,
    EnvelopeInvalido,
    montar_pedido_criado,
    validar_envelope,
)
from services.mensageria.facade import (  # noqa: F401
    broker_ativo,
    publicar_pedido_criado,
    usando_kafka,
)