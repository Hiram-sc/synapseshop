"""Configuração da mensageria, lida do ambiente.

Tudo que é nome de topologia ou política de reentrega mora aqui, para que
produtor e consumidor não possam divergir: os dois importam estas constantes.
Os valores padrão são os de desenvolvimento; em produção nada disto é
chumbado em código.
"""

from __future__ import annotations

import os
from urllib.parse import quote_plus

# --- broker ---------------------------------------------------------------
RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_PORT = int(os.environ.get("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "synapseshop")
RABBITMQ_PASSWORD = os.environ.get("RABBITMQ_PASSWORD", "synapseshop")
RABBITMQ_VHOST = os.environ.get("RABBITMQ_VHOST", "/")


def url_amqp() -> str:
    """URL AMQP do broker para quem preferir URL às parâmetros do pika."""
    return (
        f"amqp://{quote_plus(RABBITMQ_USER)}:{quote_plus(RABBITMQ_PASSWORD)}"
        f"@{RABBITMQ_HOST}:{RABBITMQ_PORT}{quote_plus(RABBITMQ_VHOST)}"
    )


def parametros_conexao():
    """Parâmetros de conexão do `pika.BlockingConnection`.

    `connection_attempts=1` e `retry_delay=0` são deliberados: a política de
    reconexão é do worker, que espera de forma crescente e loga cada tentativa.
    Deixar o pika repetir sozinho criaria exatamente o loop de reconexão
    agressivo que a spec proíbe.
    """
    import pika

    return pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        port=RABBITMQ_PORT,
        virtual_host=RABBITMQ_VHOST,
        credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD),
        heartbeat=int(os.environ.get("RABBITMQ_HEARTBEAT", "60")),
        blocked_connection_timeout=float(
            os.environ.get("RABBITMQ_BLOCKED_TIMEOUT", "30")
        ),
        connection_attempts=1,
        retry_delay=0,
        socket_timeout=10,
    )


# --- topologia ------------------------------------------------------------
#: exchange durável onde os eventos de negócio são publicados
EXCHANGE_EVENTOS = os.environ.get("PEDIDO_EXCHANGE", "pedidos.events")
#: routing key (e nome da fila) do evento `PedidoCriado`
ROUTING_KEY_PEDIDO_CRIADO = os.environ.get(
    "PEDIDO_ROUTING_KEY", "pedidos.pedidocriado"
)
QUEUE_PEDIDO_CRIADO = os.environ.get("PEDIDO_QUEUE", "pedidos.pedidocriado")
#: dead-letter exchange e fila morta; sem consumidor nesta etapa
EXCHANGE_DLX = os.environ.get("PEDIDO_DLX", "pedidos.dlx")
QUEUE_DLQ = os.environ.get("PEDIDO_DLQ", "pedidos.pedidocriado.dlq")

# --- política de reentrega ------------------------------------------------
#: tentativas totais (inicial + retries). Com o backoff abaixo, a sequência é:
#: tentativa 1 (inicial), 2 (+250ms), 3 (+500ms), 4 (+1000ms) -> DLQ.
MAX_TENTATIVAS = int(os.environ.get("PEDIDO_WORKER_MAX_TENTATIVAS", "4"))
#: base do backoff exponencial, em segundos
BACKOFF_BASE_S = float(os.environ.get("PEDIDO_WORKER_BACKOFF_BASE_S", "0.25"))
#: cabeçalho AMQP que carrega o contador de tentativas
HEADER_RETRY = "x-retry-count"

# --- idempotência ---------------------------------------------------------
#: TTL do caminho rápido no Redis, em segundos
TTL_IDEMPOTENCIA_S = int(os.environ.get("PEDIDO_IDEMPOTENCIA_TTL_S", "3600"))
#: prefixo das chaves de deduplicação no Redis
PREFIXO_IDEMPOTENCIA = os.environ.get(
    "PEDIDO_IDEMPOTENCIA_PREFIXO", "mensageria:idempotencia"
)

# --- falha proposital (validação) ----------------------------------------
#: Padrões separados por vírgula (fnmatch) de `idempotency_key` que devem
#: falhar de propósito, para observar retry -> backoff -> DLQ. Vazio por
#: padrão: a falha forçada nunca pode ficar ligada no ambiente final.
FALHA_IDEM_KEYS = os.environ.get("PEDIDO_WORKER_FALHA_IDEM_KEYS", "")

# --- espera do worker -----------------------------------------------------
#: espera inicial (e entre tentativas de reconexão) do worker, em segundos
ESPERA_RECONEXAO_S = float(os.environ.get("PEDIDO_WORKER_ESPERA_RECONEXAO_S", "5"))
#: teto da espera exponencial de reconexão, em segundos
ESPERA_RECONEXAO_MAX_S = float(
    os.environ.get("PEDIDO_WORKER_ESPERA_RECONEXAO_MAX_S", "60")
)
