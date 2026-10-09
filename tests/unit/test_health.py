"""Testes do readiness da API (`api.health`)."""

from __future__ import annotations

import pika
import pytest
from confluent_kafka import admin as kafka_admin

from api import health
from services.mensageria import facade

pytestmark = pytest.mark.unit


@pytest.mark.django_db
def test_postgres_ok():
    assert health._postgres_ok() is True


def test_redis_ok():
    assert health._redis_ok() is True


def test_kafka_ok_com_brokers(monkeypatch):
    class _FakeAdmin:
        def __init__(self, _config):
            pass

        def list_topics(self, timeout=None):
            return type("Meta", (), {"brokers": {0: object()}})()

    monkeypatch.setattr(kafka_admin, "AdminClient", _FakeAdmin)

    assert health._kafka_ok() is True


def test_kafka_ok_sem_brokers(monkeypatch):
    class _FakeAdmin:
        def __init__(self, _config):
            pass

        def list_topics(self, timeout=None):
            return type("Meta", (), {"brokers": {}})()

    monkeypatch.setattr(kafka_admin, "AdminClient", _FakeAdmin)

    assert health._kafka_ok() is False


def test_kafka_ok_erro(monkeypatch):
    class _FakeAdmin:
        def __init__(self, _config):
            pass

        def list_topics(self, timeout=None):
            raise RuntimeError("sem cluster")

    monkeypatch.setattr(kafka_admin, "AdminClient", _FakeAdmin)

    assert health._kafka_ok() is False


def test_rabbit_ok_sucesso(monkeypatch):
    class _Conexao:
        def close(self):
            return None

    monkeypatch.setattr(pika, "BlockingConnection", lambda _p: _Conexao())

    assert health._rabbit_ok() is True


def test_rabbit_ok_erro(monkeypatch):
    def _explodir(_p):
        raise RuntimeError("sem broker")

    monkeypatch.setattr(pika, "BlockingConnection", _explodir)

    assert health._rabbit_ok() is False


def test_broker_ok_despacha_para_kafka(monkeypatch):
    monkeypatch.setattr(facade, "usando_kafka", lambda: True)
    monkeypatch.setattr(health, "_kafka_ok", lambda: True)
    monkeypatch.setattr(health, "_rabbit_ok", lambda: False)

    assert health._broker_ok() is True


def test_broker_ok_despacha_para_rabbit(monkeypatch):
    monkeypatch.setattr(facade, "usando_kafka", lambda: False)
    monkeypatch.setattr(health, "_kafka_ok", lambda: False)
    monkeypatch.setattr(health, "_rabbit_ok", lambda: True)

    assert health._broker_ok() is True


def test_pronto_tudo_ok(monkeypatch):
    monkeypatch.setattr(health, "_postgres_ok", lambda: True)
    monkeypatch.setattr(health, "_redis_ok", lambda: True)
    monkeypatch.setattr(health, "_broker_ok", lambda: True)

    resposta = health.pronto(None)

    assert resposta.status_code == 200
    assert b'"status": "ok"' in resposta.content


def test_pronto_com_falha(monkeypatch):
    monkeypatch.setattr(health, "_postgres_ok", lambda: True)
    monkeypatch.setattr(health, "_redis_ok", lambda: False)
    monkeypatch.setattr(health, "_broker_ok", lambda: True)

    resposta = health.pronto(None)

    assert resposta.status_code == 503
    assert b'"redis": "unavailable"' in resposta.content
