"""Testes unitários do contrato dos eventos (`services/mensageria/envelope`)."""

from __future__ import annotations

import copy
import json
import re
from decimal import Decimal
from types import SimpleNamespace

import pytest

from services.mensageria.envelope import (
    CAMPOS_OBRIGATORIOS,
    EVENTO_TYPE,
    EVENTO_VERSION,
    NOTIFICACAO_ENVIADA,
    PAGAMENTO_PROCESSADO,
    EnvelopeInvalido,
    decimal_para_str,
    desserializar,
    montar_notificacao_enviada,
    montar_pagamento_processado,
    montar_pedido_criado,
    serializar,
    validar_envelope,
)

INSTANTE = "2026-01-02T03:04:05+00:00"


# ---------------------------------------------------------------------------
# serialização
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (Decimal("99.90"), "99.90"),
        (Decimal("0.00"), "0.00"),
        (Decimal("10.5"), "10.5"),
        (Decimal("1E+2"), "100"),
    ],
)
def test_decimal_para_str(valor, esperado):
    assert decimal_para_str(valor) == esperado


@pytest.mark.unit
def test_serializar_e_json_canonico_utf8():
    corpo = serializar({"b": 1, "a": "café"})
    assert corpo == '{"a":"café","b":1}'.encode("utf-8")


@pytest.mark.unit
def test_desserializar_roundtrip():
    envelope = {"event_id": "e1", "dados": {"pedido": {"id": 1}}}
    assert desserializar(serializar(envelope)) == envelope


@pytest.mark.unit
@pytest.mark.parametrize(
    "corpo",
    [
        b"nao e json",
        b'"apenas texto"',
        b"[1, 2, 3]",
        b"\xff\xfe",
    ],
)
def test_desserializar_corpo_invalido(corpo):
    with pytest.raises(EnvelopeInvalido):
        desserializar(corpo)


# ---------------------------------------------------------------------------
# montagem dos envelopes
# ---------------------------------------------------------------------------
def _linha(item_id=1, nome="Teclado", quantidade=2, preco="349.90"):
    return SimpleNamespace(
        item_id=item_id,
        nome_item=nome,
        quantidade=quantidade,
        preco_unitario=Decimal(preco),
    )


def _pedido(itens=None, **campos):
    dados = {
        "pk": 10,
        "idempotency_key": "req-1",
        "usuario_id": 5,
        "status": "pendente",
        "created_at": SimpleNamespace(isoformat=lambda: INSTANTE),
        "total": Decimal("699.80"),
    }
    dados.update(campos)
    linhas = itens if itens is not None else [_linha()]
    dados["itens"] = SimpleNamespace(all=lambda: linhas)
    return SimpleNamespace(**dados)


@pytest.mark.unit
def test_montar_pedido_criado(relogio_fixo):
    envelope = montar_pedido_criado(_pedido())
    assert envelope["event_type"] == EVENTO_TYPE
    assert envelope["version"] == EVENTO_VERSION
    assert envelope["occurred_at"] == relogio_fixo.isoformat()
    assert envelope["correlation_id"] == "req-1"
    assert envelope["idempotency_key"] == "req-1"
    pedido = envelope["dados"]["pedido"]
    assert pedido["id"] == 10
    assert pedido["total"] == "699.80"
    assert pedido["itens"][0]["subtotal"] == "699.80"


@pytest.mark.unit
def test_montar_pagamento_processado(relogio_fixo):
    pagamento = SimpleNamespace(
        pk=3, status="APROVADO", created_at=SimpleNamespace(isoformat=lambda: INSTANTE)
    )
    envelope = montar_pagamento_processado(pagamento, _pedido())
    assert envelope["event_type"] == PAGAMENTO_PROCESSADO
    assert envelope["idempotency_key"] == "pagamento:10"
    assert envelope["dados"]["status"] == "APROVADO"
    assert envelope["dados"]["pagamento"]["id"] == 3


@pytest.mark.unit
def test_montar_notificacao_enviada(relogio_fixo):
    notificacao = SimpleNamespace(
        pk=7, mensagem="ok", created_at=SimpleNamespace(isoformat=lambda: INSTANTE)
    )
    pagamento = SimpleNamespace(pk=3, status="APROVADO")
    envelope = montar_notificacao_enviada(notificacao, pagamento, _pedido())
    assert envelope["event_type"] == NOTIFICACAO_ENVIADA
    assert envelope["idempotency_key"] == "notificacao:3"
    assert envelope["dados"]["notificacao"]["id"] == 7


# ---------------------------------------------------------------------------
# validação do contrato
# ---------------------------------------------------------------------------
def _base_pedido_criado():
    return {
        "event_id": "e1",
        "event_type": EVENTO_TYPE,
        "version": EVENTO_VERSION,
        "occurred_at": INSTANTE,
        "correlation_id": "c1",
        "idempotency_key": "k1",
        "dados": {
            "pedido": {
                "id": 1,
                "total": "10.00",
                "itens": [
                    {"nome": "x", "preco_unitario": "10.00", "quantidade": 1}
                ],
            }
        },
    }


def _base_pagamento():
    return {
        "event_id": "e1",
        "event_type": PAGAMENTO_PROCESSADO,
        "version": EVENTO_VERSION,
        "occurred_at": INSTANTE,
        "correlation_id": "c1",
        "idempotency_key": "pagamento:1",
        "dados": {"status": "APROVADO", "pedido": {"id": 1}, "pagamento": {"id": 2}},
    }


def _base_notificacao():
    return {
        "event_id": "e1",
        "event_type": NOTIFICACAO_ENVIADA,
        "version": EVENTO_VERSION,
        "occurred_at": INSTANTE,
        "correlation_id": "c1",
        "idempotency_key": "notificacao:2",
        "dados": {
            "notificacao": {"id": 3},
            "pagamento": {"id": 2},
            "pedido": {"id": 1},
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "fabrica", [_base_pedido_criado, _base_pagamento, _base_notificacao]
)
def test_validar_envelope_aceita_os_tres_eventos(fabrica):
    envelope = fabrica()
    assert validar_envelope(envelope) is envelope


@pytest.mark.unit
@pytest.mark.parametrize("campo", CAMPOS_OBRIGATORIOS)
def test_validar_envelope_campo_obrigatorio_ausente(campo):
    envelope = _base_pedido_criado()
    del envelope[campo]
    with pytest.raises(EnvelopeInvalido, match="campo obrigatório ausente"):
        validar_envelope(envelope)


@pytest.mark.unit
@pytest.mark.parametrize("campo", ["event_type", "version", "occurred_at"])
def test_validar_envelope_campo_de_texto_vazio(campo):
    envelope = _base_pedido_criado()
    envelope[campo] = "   "
    with pytest.raises(EnvelopeInvalido, match="ausente ou vazio"):
        validar_envelope(envelope)


@pytest.mark.unit
def test_validar_envelope_event_type_desconhecido():
    envelope = _base_pedido_criado()
    envelope["event_type"] = "EventoQueNaoExiste"
    with pytest.raises(EnvelopeInvalido, match="event_type desconhecido"):
        validar_envelope(envelope)


@pytest.mark.unit
def test_validar_envelope_version_nao_suportada():
    envelope = _base_pedido_criado()
    envelope["version"] = "9.9"
    with pytest.raises(EnvelopeInvalido, match="version não suportada"):
        validar_envelope(envelope)


@pytest.mark.unit
def test_validar_envelope_dados_precisa_ser_objeto():
    envelope = _base_pedido_criado()
    envelope["dados"] = "nao e dict"
    with pytest.raises(EnvelopeInvalido, match="dados deve ser um objeto"):
        validar_envelope(envelope)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutacao", "fragmento"),
    [
        (lambda d: d.__setitem__("pedido", "x"), "dados.pedido deve ser um objeto"),
        (lambda d: d["pedido"].pop("id"), "dados.pedido.id é obrigatório"),
        (lambda d: d["pedido"].pop("total"), "dados.pedido.total é obrigatório"),
        (lambda d: d["pedido"].__setitem__("total", 10.0), "total deve ser string"),
        (lambda d: d["pedido"].__setitem__("itens", []), "itens deve ser lista não vazia"),
        (lambda d: d["pedido"].__setitem__("itens", "x"), "itens deve ser lista não vazia"),
        (lambda d: d["pedido"]["itens"].__setitem__(0, "x"), "itens[0] deve ser objeto"),
        (lambda d: d["pedido"]["itens"][0].__setitem__("nome", 1), "nome deve ser string"),
        (
            lambda d: d["pedido"]["itens"][0].__setitem__("preco_unitario", 1),
            "preco_unitario deve ser string",
        ),
        (
            lambda d: d["pedido"]["itens"][0].__setitem__("quantidade", True),
            "quantidade deve ser inteiro",
        ),
        (
            lambda d: d["pedido"]["itens"][0].__setitem__("quantidade", "1"),
            "quantidade deve ser inteiro",
        ),
    ],
)
def test_validar_dados_pedido_criado(mutacao, fragmento):
    envelope = copy.deepcopy(_base_pedido_criado())
    mutacao(envelope["dados"])
    with pytest.raises(EnvelopeInvalido, match=re.escape(fragmento)):
        validar_envelope(envelope)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutacao", "fragmento"),
    [
        (
            lambda d: d.__setitem__("status", "PENDENTE"),
            "dados.status deve ser um de",
        ),
        (
            lambda d: d.__setitem__("pedido", {"id": "1"}),
            "dados.pedido.id deve ser um inteiro",
        ),
        (
            lambda d: d.__setitem__("pagamento", {}),
            "dados.pagamento.id deve ser um inteiro",
        ),
    ],
)
def test_validar_dados_pagamento_processado(mutacao, fragmento):
    envelope = copy.deepcopy(_base_pagamento())
    mutacao(envelope["dados"])
    with pytest.raises(EnvelopeInvalido, match=fragmento):
        validar_envelope(envelope)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutacao", "fragmento"),
    [
        (
            lambda d: d.__setitem__("notificacao", {}),
            "dados.notificacao.id deve ser um inteiro",
        ),
        (
            lambda d: d.__setitem__("pagamento", {}),
            "dados.pagamento.id deve ser um inteiro",
        ),
        (
            lambda d: d.__setitem__("pedido", {}),
            "dados.pedido.id deve ser um inteiro",
        ),
    ],
)
def test_validar_dados_notificacao_enviada(mutacao, fragmento):
    envelope = copy.deepcopy(_base_notificacao())
    mutacao(envelope["dados"])
    with pytest.raises(EnvelopeInvalido, match=fragmento):
        validar_envelope(envelope)


@pytest.mark.unit
def test_serializar_torna_o_envelope_consumivel_pelo_broker():
    envelope = validar_envelope(_base_pedido_criado())
    assert json.loads(serializar(envelope).decode("utf-8"))["event_type"] == EVENTO_TYPE
