"""Testes de integração dos comandos de management e dos workers."""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

import pika
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from repositories.models import EventoProcessado
from services.mensageria import consumidor_kafka as ck

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# create_users
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_create_users_cria_e_e_idempotente():
    from django.contrib.auth import get_user_model

    out = StringIO()
    call_command(
        "create_users",
        admin_password="admin-secret",
        user_password="user-secret",
        stdout=out,
    )

    assert get_user_model().objects.filter(username="admin").exists()
    assert get_user_model().objects.filter(username="user").exists()
    assert get_user_model().objects.count() == 2

    out2 = StringIO()
    call_command(
        "create_users",
        admin_password="admin-secret",
        user_password="user-secret",
        stdout=out2,
    )
    assert get_user_model().objects.count() == 2
    assert "já existia" in out2.getvalue()


# ---------------------------------------------------------------------------
# limpar_eventos_processados
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_limpar_eventos_processados():
    from datetime import timedelta

    from django.utils import timezone

    evento = EventoProcessado.objects.create(
        event_id="evt-1", event_type="PedidoCriado", idempotency_key="k1"
    )
    EventoProcessado.objects.filter(pk=evento.pk).update(
        processado_em=timezone.now() - timedelta(days=10)
    )

    out = StringIO()
    call_command("limpar_eventos_processados", dias=1, stdout=out)

    assert EventoProcessado.objects.count() == 0
    assert "removido" in out.getvalue()


@pytest.mark.django_db
def test_limpar_eventos_processados_dias_invalido():
    err = StringIO()
    call_command("limpar_eventos_processados", dias=0, stderr=err)

    assert "maior que zero" in err.getvalue()


# ---------------------------------------------------------------------------
# pedido_worker
# ---------------------------------------------------------------------------
def test_pedido_worker_kafka(monkeypatch):
    from repositories.management.commands import pedido_worker

    monkeypatch.setattr(pedido_worker.facade, "usando_kafka", lambda: True)
    chamado = {}
    monkeypatch.setattr(
        ck, "rodar_consumidor", lambda **kwargs: chamado.update(kwargs)
    )

    call_command("pedido_worker")

    assert "deve_parar" in chamado


def test_pedido_worker_rabbitmq(monkeypatch):
    from repositories.management.commands import pedido_worker

    monkeypatch.setattr(pedido_worker.facade, "usando_kafka", lambda: False)
    monkeypatch.setattr(pedido_worker.Command, "_conectar", lambda self: None)

    call_command("pedido_worker")


def test_pedido_worker_ao_receber(monkeypatch):
    from repositories.management.commands import pedido_worker
    from services.mensageria import consumidor

    processados = []
    monkeypatch.setattr(consumidor, "processar", lambda *a: processados.append(a))

    pedido_worker.Command()._ao_receber("canal", "method", "props", b"corpo")

    assert len(processados) == 1


def test_pedido_worker_ao_encerrar():
    from repositories.management.commands import pedido_worker

    comandante = pedido_worker.Command()
    conexao = SimpleNamespace(is_open=True, fechada=False)
    conexao.close = lambda: setattr(conexao, "fechada", True)
    comandante._conexao = conexao

    comandante._ao_encerrar(15, None)

    assert comandante._parar is True
    assert conexao.fechada is True


def test_pedido_worker_conectar_sucesso(monkeypatch):
    from repositories.management.commands import pedido_worker

    fake = object()
    monkeypatch.setattr(pika, "BlockingConnection", lambda _p: fake)

    assert pedido_worker.Command()._conectar() is fake


def test_pedido_worker_conectar_falha_ate_parar(monkeypatch):
    from repositories.management.commands import pedido_worker

    def _explodir(_p):
        raise RuntimeError("broker fora")

    monkeypatch.setattr(pika, "BlockingConnection", _explodir)
    comandante = pedido_worker.Command()

    def _sleep(_segundos):
        comandante._parar = True

    monkeypatch.setattr(pedido_worker.time, "sleep", _sleep)

    assert comandante._conectar() is None


def test_pedido_worker_consumir(monkeypatch):
    from repositories.management.commands import pedido_worker
    from services.mensageria import topologia

    chamadas = []

    class _Canal:
        def confirm_delivery(self):
            chamadas.append("confirm")

        def basic_qos(self, prefetch_count):
            chamadas.append(("qos", prefetch_count))

        def basic_consume(self, **kwargs):
            chamadas.append(("consume", kwargs))

        def start_consuming(self):
            raise KeyboardInterrupt

    conexao = SimpleNamespace(channel=lambda: _Canal())
    monkeypatch.setattr(
        topologia, "declarar_topologia", lambda _c: chamadas.append("topologia")
    )

    with pytest.raises(KeyboardInterrupt):
        pedido_worker.Command()._consumir(conexao)

    assert ("qos", 1) in chamadas
    assert "topologia" in chamadas


def test_pedido_worker_rodar_rabbitmq(monkeypatch):
    from repositories.management.commands import pedido_worker

    comandante = pedido_worker.Command()
    conexao = SimpleNamespace(is_open=True, fechada=False)
    conexao.close = lambda: setattr(conexao, "fechada", True)
    monkeypatch.setattr(comandante, "_conectar", lambda: conexao)

    def _consumir(_conexao):
        comandante._parar = True

    monkeypatch.setattr(comandante, "_consumir", _consumir)

    comandante._rodar_rabbitmq()

    assert conexao.fechada is True


# ---------------------------------------------------------------------------
# notificacao_worker
# ---------------------------------------------------------------------------
def test_notificacao_worker_kafka(monkeypatch):
    from repositories.management.commands import notificacao_worker as nw

    monkeypatch.setattr(nw.facade, "usando_kafka", lambda: True)
    chamado = {}
    monkeypatch.setattr(nw, "rodar_consumidor", lambda **kwargs: chamado.update(kwargs))

    call_command("notificacao_worker")

    assert "espec" in chamado


def test_notificacao_worker_exige_kafka(monkeypatch):
    from repositories.management.commands import notificacao_worker as nw

    monkeypatch.setattr(nw.facade, "usando_kafka", lambda: False)

    with pytest.raises(CommandError):
        call_command("notificacao_worker")


def test_notificacao_worker_ao_encerrar():
    from repositories.management.commands import notificacao_worker as nw

    comandante = nw.Command()
    comandante._ao_encerrar(15, None)

    assert comandante._parar is True


def test_espec_pagamento_processado():
    from repositories.management.commands import notificacao_worker as nw
    from services.mensageria.envelope import PAGAMENTO_PROCESSADO

    espec = nw.espec_pagamento_processado()

    assert espec.event_type == PAGAMENTO_PROCESSADO
