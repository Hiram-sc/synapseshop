"""Testes unitários da idempotência do consumo (`services.mensageria.idempotencia`)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from repositories.models import EventoProcessado, Pedido
from services.mensageria import idempotencia

pytestmark = pytest.mark.unit


def _explode(*_args, **_kwargs):
    raise RuntimeError("cache indisponível")


def _pedido():
    from django.contrib.auth import get_user_model

    usuario, _ = get_user_model().objects.get_or_create(username="uidem")
    return Pedido.objects.create(usuario=usuario, idempotency_key="k", total="1.00")


@pytest.mark.django_db
def test_ja_processado_via_redis():
    from django.core.cache import cache

    chave = idempotencia._chave_redis("PedidoCriado", "k1")
    cache.set(chave, "evt-1")

    assert idempotencia.ja_processado("PedidoCriado", "k1") is True


@pytest.mark.django_db
def test_ja_processado_via_banco():
    EventoProcessado.objects.create(
        event_id="evt-1", event_type="PedidoCriado", idempotency_key="k2"
    )

    assert idempotencia.ja_processado("PedidoCriado", "k2") is True


@pytest.mark.django_db
def test_ja_processado_falso():
    assert idempotencia.ja_processado("PedidoCriado", "inexistente") is False


@pytest.mark.django_db
def test_ja_processado_fail_open(monkeypatch):
    EventoProcessado.objects.create(
        event_id="evt-1", event_type="PedidoCriado", idempotency_key="k3"
    )
    monkeypatch.setattr(idempotencia.cache, "get", _explode)

    assert idempotencia.ja_processado("PedidoCriado", "k3") is True


@pytest.mark.django_db
def test_registrar_cria_e_grava_no_redis():
    from django.core.cache import cache

    pedido = _pedido()
    criado = idempotencia.registrar(
        event_type="PedidoCriado",
        idempotency_key="k4",
        event_id="evt-4",
        pedido=pedido,
    )

    assert criado is True
    assert EventoProcessado.objects.filter(idempotency_key="k4").count() == 1
    assert cache.get(idempotencia._chave_redis("PedidoCriado", "k4")) == "evt-4"


@pytest.mark.django_db
def test_registrar_duplicado_devolve_false():
    idempotencia.registrar("PedidoCriado", "k5", "evt-5")
    criado = idempotencia.registrar("PedidoCriado", "k5", "evt-5")

    assert criado is False
    assert EventoProcessado.objects.filter(idempotency_key="k5").count() == 1


@pytest.mark.django_db
def test_registrar_redis_indisponivel(monkeypatch):
    monkeypatch.setattr(idempotencia.cache, "set", _explode)

    assert idempotencia.registrar("PedidoCriado", "k6", "evt-6") is True


@pytest.mark.django_db
def test_limpar_registros_remove_antigos():
    antigo = EventoProcessado.objects.create(
        event_id="evt-antigo", event_type="PedidoCriado", idempotency_key="antigo"
    )
    EventoProcessado.objects.filter(pk=antigo.pk).update(
        processado_em=timezone.now() - timedelta(days=10)
    )
    EventoProcessado.objects.create(
        event_id="evt-novo", event_type="PedidoCriado", idempotency_key="novo"
    )

    removidos = idempotencia.limpar_registros(1)

    assert removidos == 1
    assert EventoProcessado.objects.count() == 1
