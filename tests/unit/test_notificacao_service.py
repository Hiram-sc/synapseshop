"""Testes do efeito do notificacao-worker (`services.notificacao_service`)."""

from __future__ import annotations

import pytest

from repositories.models import Notificacao, Pagamento, Pedido, StatusPagamento
from services import notificacao_service
from services.mensageria import facade
from services.mensageria.envelope import (
    NOTIFICACAO_ENVIADA,
    montar_pagamento_processado,
)

pytestmark = pytest.mark.unit


def _pedido_e_pagamento(status=StatusPagamento.APROVADO):
    from django.contrib.auth import get_user_model

    usuario, _ = get_user_model().objects.get_or_create(username="unotif")
    pedido = Pedido.objects.create(
        usuario=usuario, idempotency_key="idem-notif", total="9.90"
    )
    pedido.refresh_from_db()
    pagamento = Pagamento.objects.create(pedido=pedido, status=status)
    return pedido, pagamento


@pytest.mark.django_db
def test_aprovado_registra_e_publica(monkeypatch):
    pedido, pagamento = _pedido_e_pagamento()
    publicados = []
    monkeypatch.setattr(
        facade, "publicar_evento", lambda env: publicados.append(env) or True
    )
    envelope = montar_pagamento_processado(pagamento, pedido)

    resultado = notificacao_service.efeito_pagamento_processado(envelope)

    assert resultado == pedido
    assert Notificacao.objects.filter(pagamento=pagamento).count() == 1
    assert publicados[0]["event_type"] == NOTIFICACAO_ENVIADA
    assert publicados[0]["idempotency_key"] == f"notificacao:{pagamento.pk}"


@pytest.mark.django_db
def test_reentrega_nao_duplica_notificacao(monkeypatch):
    pedido, pagamento = _pedido_e_pagamento()
    monkeypatch.setattr(facade, "publicar_evento", lambda env: True)
    envelope = montar_pagamento_processado(pagamento, pedido)

    notificacao_service.efeito_pagamento_processado(envelope)
    notificacao_service.efeito_pagamento_processado(envelope)

    assert Notificacao.objects.filter(pagamento=pagamento).count() == 1


@pytest.mark.django_db
def test_recusado_nao_notifica(monkeypatch):
    pedido, pagamento = _pedido_e_pagamento(StatusPagamento.RECUSADO)
    publicados = []
    monkeypatch.setattr(
        facade, "publicar_evento", lambda env: publicados.append(env) or True
    )
    envelope = montar_pagamento_processado(pagamento, pedido)

    resultado = notificacao_service.efeito_pagamento_processado(envelope)

    assert resultado == pedido
    assert Notificacao.objects.count() == 0
    assert publicados == []


@pytest.mark.django_db
def test_publicacao_recusada_levanta(monkeypatch):
    pedido, pagamento = _pedido_e_pagamento()
    monkeypatch.setattr(facade, "publicar_evento", lambda env: False)
    envelope = montar_pagamento_processado(pagamento, pedido)

    with pytest.raises(RuntimeError):
        notificacao_service.efeito_pagamento_processado(envelope)

    # o registro durou: o retry do consumidor encontrará o get_or_create
    assert Notificacao.objects.filter(pagamento=pagamento).count() == 1
