"""Testes da fachada de mensageria (`services.mensageria.facade`).

A fachada é o único ponto que decide o broker: estes testes cobrem as três
rotas - kafka, rabbitmq com `PedidoCriado` e rabbitmq sem rota para o evento.
"""

from __future__ import annotations

import pytest

from services.mensageria import config, facade

pytestmark = pytest.mark.unit


def _pedido_criado() -> dict:
    return {
        "event_type": "PedidoCriado",
        "event_id": "evt-1",
        "idempotency_key": "idem-1",
        "correlation_id": "corr-1",
    }


def test_broker_ativo_e_usando_kafka(monkeypatch):
    monkeypatch.setattr(config, "MENSAGERIA_BROKER", "kafka")
    assert facade.broker_ativo() == "kafka"
    assert facade.usando_kafka() is True

    monkeypatch.setattr(config, "MENSAGERIA_BROKER", "rabbitmq")
    assert facade.broker_ativo() == "rabbitmq"
    assert facade.usando_kafka() is False


def test_publicar_evento_no_kafka(monkeypatch, broker_isolado):
    monkeypatch.setattr(config, "MENSAGERIA_BROKER", "kafka")

    assert facade.publicar_evento(_pedido_criado()) is True

    assert broker_isolado.publicacoes == [("kafka", _pedido_criado())]


def test_publicar_evento_no_rabbitmq_com_rota(monkeypatch, broker_isolado):
    monkeypatch.setattr(config, "MENSAGERIA_BROKER", "rabbitmq")

    assert facade.publicar_evento(_pedido_criado()) is True

    assert broker_isolado.publicacoes == [("rabbitmq", _pedido_criado())]


def test_publicar_evento_sem_rota_no_rabbitmq(monkeypatch, broker_isolado):
    monkeypatch.setattr(config, "MENSAGERIA_BROKER", "rabbitmq")
    envelope = {"event_type": "PagamentoProcessado", "event_id": "evt-2"}

    assert facade.publicar_evento(envelope) is False

    # nada foi publicado e a falha do broker fake nunca chegou a ser consultada
    assert broker_isolado.publicacoes == []


def test_publicar_evento_sem_event_type_no_rabbitmq(monkeypatch, broker_isolado):
    monkeypatch.setattr(config, "MENSAGERIA_BROKER", "rabbitmq")

    assert facade.publicar_evento({}) is False
    assert broker_isolado.publicacoes == []


def test_publicar_pedido_criado_delega(monkeypatch, broker_isolado):
    monkeypatch.setattr(config, "MENSAGERIA_BROKER", "kafka")

    assert facade.publicar_pedido_criado(_pedido_criado()) is True

    assert broker_isolado.publicacoes == [("kafka", _pedido_criado())]
