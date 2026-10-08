"""Readiness da API: `/health/pronto`.

O `/health` em `api/views.py` é *liveness* - responde `{"status": "ok"}`
enquanto o processo estiver de pé, sem olhar para nenhuma dependência. Este
módulo é o *readiness*: olha para o que a API precisa para atender de verdade
e responde 503 se qualquer peça essencial falhar:

* **PostgreSQL** - `SELECT 1` via ORM;
* **Redis** - `PING` do cliente do cache (a leitura das rotas de catálogo e
  pedido cai no fail-open, mas um Redis fora do ar é sinal de aplicação
  degradada e deve sair do balanceamento);
* **broker ativo** - Kafka (metadados do `AdminClient`, que falha se os
  brokers não responderem) ou RabbitMQ (conexão AMQP), conforme
  `MENSAGERIA_BROKER`.

Cada checagem tem timeout curto e responde `False` a qualquer exceção: o
endpoint existe para o orquestrador decidir, não para explicar o defeito. É o
alvo do healthcheck da `api` no `docker-compose.yml`.
"""

from __future__ import annotations

from django.db import connection
from django.http import JsonResponse

from services.mensageria import config, facade

#: timeout curto de propósito: o healthcheck do Compose não pode pendurar
CHECK_TIMEOUT_S = 5.0


def _postgres_ok() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchall() is not None
    except Exception:  # noqa: BLE001 - readiness responde o estado, não o motivo
        return False


def _redis_ok() -> bool:
    try:
        from django_redis import get_redis_connection

        # com `IGNORE_EXCEPTIONS` o `ping` devolve `False` (e não exceção)
        # quando o Redis não responde; o `is True` cobre os dois casos
        return get_redis_connection("default").ping() is True
    except Exception:  # noqa: BLE001
        return False


def _broker_ok() -> bool:
    if facade.usando_kafka():
        return _kafka_ok()
    return _rabbit_ok()


def _kafka_ok() -> bool:
    try:
        from confluent_kafka.admin import AdminClient

        admin = AdminClient(
            {"bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS}
        )
        # `list_topics` levanta (ou devolve metadados sem brokers) quando o
        # cluster não responde dentro do timeout
        metadados = admin.list_topics(timeout=CHECK_TIMEOUT_S)
        return bool(metadados.brokers)
    except Exception:  # noqa: BLE001
        return False


def _rabbit_ok() -> bool:
    try:
        import pika

        conexao = pika.BlockingConnection(config.parametros_conexao())
        conexao.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def pronto(request) -> JsonResponse:
    """Responde o estado de prontidão da API: 200 quando tudo responde, 503 se
    qualquer dependência essencial falhar."""
    database = _postgres_ok()
    redis = _redis_ok()
    broker = _broker_ok()

    corpo = {
        "database": "ok" if database else "unavailable",
        "redis": "ok" if redis else "unavailable",
        "broker": "ok" if broker else "unavailable",
    }
    se_tudo_ok = database and redis and broker
    corpo["status"] = "ok" if se_tudo_ok else "unavailable"
    return JsonResponse(corpo, status=200 if se_tudo_ok else 503)