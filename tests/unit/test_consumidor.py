"""Testes unitários do consumidor RabbitMQ (`services.mensageria.consumidor`).

A máquina de estados de uma mensagem - contrato, idempotência, efeito, retry,
DLQ - é exercitada com um canal fake e o banco de teste real.
"""

from __future__ import annotations

from types import SimpleNamespace

import pika
import pytest

from repositories.models import EventoProcessado, Pedido, StatusPedido
from services.mensageria import config, consumidor
from services.mensageria.envelope import serializar

pytestmark = pytest.mark.unit


def _envelope(pedido_id, idem="idem-1", event_type="PedidoCriado"):
    return {
        "event_id": "evt-1",
        "event_type": event_type,
        "version": "1.0",
        "occurred_at": "2026-01-02T03:04:05+00:00",
        "correlation_id": "corr-1",
        "idempotency_key": idem,
        "dados": {
            "pedido": {
                "id": pedido_id,
                "total": "9.90",
                "itens": [
                    {"nome": "x", "preco_unitario": "1.00", "quantidade": 1}
                ],
            }
        },
    }


class _Canal:
    def __init__(self, ack_erro=False, nack_erro=False):
        self.acks = []
        self.nacks = []
        self.ack_erro = ack_erro
        self.nack_erro = nack_erro

    def basic_ack(self, delivery_tag):
        if self.ack_erro:
            raise RuntimeError("canal fechado")
        self.acks.append(delivery_tag)

    def basic_nack(self, delivery_tag, requeue):
        if self.nack_erro:
            raise RuntimeError("canal fechado")
        self.nacks.append((delivery_tag, requeue))


def _criar_pedido(usuario_id=1, idem="idem-1"):
    from django.contrib.auth import get_user_model

    usuario, _ = get_user_model().objects.get_or_create(
        username=f"u{usuario_id}"
    )
    return Pedido.objects.create(
        usuario=usuario, idempotency_key=idem, total="9.90"
    )


@pytest.mark.django_db
def test_processar_contrato_invalido_vai_para_dlq():
    canal = _Canal()
    consumidor.processar(
        canal, SimpleNamespace(delivery_tag="t1"), SimpleNamespace(headers={}), b"{bad"
    )

    assert canal.nacks == [("t1", False)]
    assert canal.acks == []


@pytest.mark.django_db
def test_processar_duplicada_apenas_confirma(monkeypatch):
    canal = _Canal()
    monkeypatch.setattr(consumidor.idempotencia, "ja_processado", lambda *a: True)

    consumidor.processar(
        canal,
        SimpleNamespace(delivery_tag="t1"),
        SimpleNamespace(headers={}),
        serializar(_envelope(1)),
    )

    assert canal.acks == ["t1"]
    assert EventoProcessado.objects.count() == 0


@pytest.mark.django_db
def test_processar_sucesso_confirma_e_registra(monkeypatch):
    pedido = _criar_pedido()
    canal = _Canal()

    consumidor.processar(
        canal,
        SimpleNamespace(delivery_tag="t1"),
        SimpleNamespace(headers={}),
        serializar(_envelope(pedido.pk)),
    )

    pedido.refresh_from_db()
    assert pedido.status == StatusPedido.CONFIRMADO
    assert pedido.processado_em is not None
    assert EventoProcessado.objects.count() == 1
    assert canal.acks == ["t1"]


@pytest.mark.django_db
def test_processar_erro_republica_e_confirma(monkeypatch):
    canal = _Canal()
    monkeypatch.setattr(consumidor.time, "sleep", lambda _s: None)
    monkeypatch.setattr(consumidor, "_republicar", lambda *a, **k: True)

    consumidor.processar(
        canal,
        SimpleNamespace(delivery_tag="t1"),
        SimpleNamespace(headers={}),
        serializar(_envelope(999)),
    )

    assert canal.acks == ["t1"]
    assert canal.nacks == []


@pytest.mark.django_db
def test_processar_erro_com_republicacao_falha_reenfileira(monkeypatch):
    canal = _Canal()
    monkeypatch.setattr(consumidor.time, "sleep", lambda _s: None)
    monkeypatch.setattr(consumidor, "_republicar", lambda *a, **k: False)

    consumidor.processar(
        canal,
        SimpleNamespace(delivery_tag="t1"),
        SimpleNamespace(headers={}),
        serializar(_envelope(999)),
    )

    assert canal.nacks == [("t1", True)]


@pytest.mark.django_db
def test_processar_tentativas_esgotadas_vai_para_dlq(monkeypatch):
    canal = _Canal()
    monkeypatch.setattr(consumidor.time, "sleep", lambda _s: None)

    consumidor.processar(
        canal,
        SimpleNamespace(delivery_tag="t1"),
        SimpleNamespace(headers={config.HEADER_RETRY: config.MAX_TENTATIVAS - 1}),
        serializar(_envelope(999)),
    )

    assert canal.nacks == [("t1", False)]


@pytest.mark.django_db
def test_processar_falha_forcada(monkeypatch):
    canal = _Canal()
    monkeypatch.setattr(consumidor.time, "sleep", lambda _s: None)
    monkeypatch.setattr(consumidor, "_republicar", lambda *a, **k: True)
    monkeypatch.setattr(config, "FALHA_IDEM_KEYS", "idem-*")

    consumidor.processar(
        canal,
        SimpleNamespace(delivery_tag="t1"),
        SimpleNamespace(headers={}),
        serializar(_envelope(999)),
    )

    assert canal.acks == ["t1"]


def test_tentativa_atual_sem_cabecalho():
    assert consumidor._tentativa_atual(SimpleNamespace(headers=None)) == 1
    assert consumidor._tentativa_atual(SimpleNamespace(headers={})) == 1
    assert (
        consumidor._tentativa_atual(
            SimpleNamespace(headers={config.HEADER_RETRY: "2"})
        )
        == 3
    )
    assert (
        consumidor._tentativa_atual(
            SimpleNamespace(headers={config.HEADER_RETRY: "abc"})
        )
        == 1
    )


def test_falha_forcada_sem_padroes_nao_levanta(monkeypatch):
    monkeypatch.setattr(config, "FALHA_IDEM_KEYS", "")
    consumidor._falha_forcada("qualquer")


def test_falha_forcada_com_padrao(monkeypatch):
    monkeypatch.setattr(config, "FALHA_IDEM_KEYS", "boom-*, outro")
    with pytest.raises(consumidor.FalhaForcada):
        consumidor._falha_forcada("boom-123")


@pytest.mark.django_db
def test_executar_efeito_pedido_inexistente():
    with pytest.raises(consumidor.FalhaProcessamento):
        consumidor._executar_efeito(_envelope(424242))


def test_ack_e_nack_seguros_engolem_excecao():
    consumidor._ack_seguro(_Canal(ack_erro=True), "t1")
    consumidor._nack_seguro(_Canal(nack_erro=True), "t1", requeue=True)


def test_republicar_sucesso(monkeypatch):
    monkeypatch.setattr(pika, "BasicProperties", lambda **k: k)

    class _CanalPub:
        def basic_publish(self, **kwargs):
            return True

    assert (
        consumidor._republicar(
            _CanalPub(), _envelope(1), 1, SimpleNamespace(headers={})
        )
        is True
    )


def test_republicar_falha(monkeypatch):
    monkeypatch.setattr(pika, "BasicProperties", lambda **k: k)

    class _CanalPub:
        def basic_publish(self, **kwargs):
            raise RuntimeError("caiu")

    assert (
        consumidor._republicar(
            _CanalPub(), _envelope(1), 1, SimpleNamespace(headers={})
        )
        is False
    )
