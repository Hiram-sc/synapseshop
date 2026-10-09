"""Testes unitários do consumidor Kafka (`services.mensageria.consumidor_kafka`).

Cobre a máquina de estados por mensagem, a leitura de cabeçalhos, a montagem de
DLQ/republish e o laço de `rodar_consumidor` - tudo com consumidor/mensagem
fakes e o banco de teste.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from confluent_kafka import KafkaError

from repositories.models import EventoProcessado, Pedido, StatusPedido
from services.mensageria import config
from services.mensageria import consumidor_kafka as ck
from services.mensageria.envelope import EVENTO_TYPE, PAGAMENTO_PROCESSADO, serializar

pytestmark = pytest.mark.unit


def _envelope(pedido_id, idem="idem-1"):
    return {
        "event_id": "evt-1",
        "event_type": EVENTO_TYPE,
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


class _Mensagem:
    def __init__(self, valor=b"", erro=None, headers=None, key=b"chave"):
        self._valor = valor
        self._erro = erro
        self._headers = headers or []
        self._key = key

    def value(self):
        return self._valor

    def error(self):
        return self._erro

    def headers(self):
        return self._headers

    def key(self):
        return self._key

    def partition(self):
        return 0

    def offset(self):
        return 1


class _Consumidor:
    def __init__(self):
        self.commits = []
        self.subscrito = None
        self.fechado = False

    def subscribe(self, topicos):
        self.subscrito = topicos

    def commit(self, message=None):
        self.commits.append(message)

    def close(self):
        self.fechado = True


def _criar_pedido(idem="idem-1"):
    from django.contrib.auth import get_user_model

    usuario, _ = get_user_model().objects.get_or_create(username="uk")
    return Pedido.objects.create(
        usuario=usuario, idempotency_key=idem, total="9.90"
    )


@pytest.mark.django_db
def test_processar_mensagem_contrato_invalido(monkeypatch):
    consumidor = _Consumidor()
    monkeypatch.setattr(ck, "_enviar_dlq", lambda *a, **k: None)
    espec = ck.espec_pedido_criado()

    ck.processar_mensagem(
        consumidor, _Mensagem(valor=b"{bad"), object(), espec
    )

    assert len(consumidor.commits) == 1


@pytest.mark.django_db
def test_processar_mensagem_event_type_inesperado(monkeypatch):
    consumidor = _Consumidor()
    monkeypatch.setattr(ck, "_enviar_dlq", lambda *a, **k: None)
    espec = ck.espec_pedido_criado()
    espec_errado = ck.EspecEvento(
        event_type=PAGAMENTO_PROCESSADO,
        topico=espec.topico,
        topico_dlq=espec.topico_dlq,
        group_id=espec.group_id,
        cliente_id=espec.cliente_id,
        efeito=espec.efeito,
    )

    ck.processar_mensagem(
        consumidor, _Mensagem(valor=serializar(_envelope(1))), object(), espec_errado
    )

    assert len(consumidor.commits) == 1


@pytest.mark.django_db
def test_processar_mensagem_duplicada(monkeypatch):
    consumidor = _Consumidor()
    monkeypatch.setattr(ck.idempotencia, "ja_processado", lambda *a: True)

    ck.processar_mensagem(
        consumidor,
        _Mensagem(valor=serializar(_envelope(1))),
        object(),
        ck.espec_pedido_criado(),
    )

    assert len(consumidor.commits) == 1
    assert EventoProcessado.objects.count() == 0


@pytest.mark.django_db
def test_processar_mensagem_sucesso():
    pedido = _criar_pedido()
    consumidor = _Consumidor()

    ck.processar_mensagem(
        consumidor,
        _Mensagem(valor=serializar(_envelope(pedido.pk))),
        object(),
        ck.espec_pedido_criado(),
    )

    pedido.refresh_from_db()
    assert pedido.status == StatusPedido.CONFIRMADO
    assert EventoProcessado.objects.count() == 1
    assert len(consumidor.commits) == 1


@pytest.mark.django_db
def test_processar_mensagem_retry_commita(monkeypatch):
    consumidor = _Consumidor()
    monkeypatch.setattr(ck.time, "sleep", lambda _s: None)
    monkeypatch.setattr(ck, "_republicar", lambda *a, **k: None)

    ck.processar_mensagem(
        consumidor,
        _Mensagem(valor=serializar(_envelope(999))),
        object(),
        ck.espec_pedido_criado(),
    )

    assert len(consumidor.commits) == 1


@pytest.mark.django_db
def test_processar_mensagem_retry_com_falha_nao_commita(monkeypatch):
    consumidor = _Consumidor()
    monkeypatch.setattr(ck.time, "sleep", lambda _s: None)
    monkeypatch.setattr(ck, "_republicar", lambda *a, **k: RuntimeError("x"))

    ck.processar_mensagem(
        consumidor,
        _Mensagem(valor=serializar(_envelope(999))),
        object(),
        ck.espec_pedido_criado(),
    )

    assert consumidor.commits == []


@pytest.mark.django_db
def test_processar_mensagem_dlq_commita(monkeypatch):
    consumidor = _Consumidor()
    monkeypatch.setattr(ck.time, "sleep", lambda _s: None)
    monkeypatch.setattr(ck, "_enviar_dlq", lambda *a, **k: None)
    headers = [(config.HEADER_RETRY, str(config.MAX_TENTATIVAS - 1).encode())]

    ck.processar_mensagem(
        consumidor,
        _Mensagem(valor=serializar(_envelope(999)), headers=headers),
        object(),
        ck.espec_pedido_criado(),
    )

    assert len(consumidor.commits) == 1


@pytest.mark.django_db
def test_processar_mensagem_dlq_falha_nao_commita(monkeypatch):
    consumidor = _Consumidor()
    monkeypatch.setattr(ck.time, "sleep", lambda _s: None)
    monkeypatch.setattr(ck, "_enviar_dlq", lambda *a, **k: RuntimeError("dlq fora"))
    headers = [(config.HEADER_RETRY, str(config.MAX_TENTATIVAS - 1).encode())]

    ck.processar_mensagem(
        consumidor,
        _Mensagem(valor=serializar(_envelope(999)), headers=headers),
        object(),
        ck.espec_pedido_criado(),
    )

    assert consumidor.commits == []


def test_ler_retry_count():
    assert ck._ler_retry_count(None) == 0
    assert ck._ler_retry_count([]) == 0
    assert ck._ler_retry_count([(b"outro", b"1")]) == 0
    assert ck._ler_retry_count([(config.HEADER_RETRY, b"5")]) == 5
    assert ck._ler_retry_count([(config.HEADER_RETRY, "7")]) == 7
    assert ck._ler_retry_count([(config.HEADER_RETRY, b"abc")]) == 0


def test_enviar_dlq_filtra_cabecalhos(monkeypatch):
    capturado = {}

    def _produzir(produtor, topico, valor, chave, cabecalhos):
        capturado.update(
            topico=topico, valor=valor, chave=chave, cabecalhos=cabecalhos
        )
        return None

    monkeypatch.setattr(ck.produtor_kafka, "_produzir", _produzir)
    message = _Mensagem(
        valor=b"cru",
        headers=[
            (b"x-retry-count", b"1"),
            (b"motivo", b"antigo"),
            (b"event-id", b"evt-1"),
        ],
    )

    ck._enviar_dlq(
        object(),
        message,
        topico_dlq="dlq",
        chave="k",
        tentativa=2,
        motivo="falha",
    )

    nomes = [nome for nome, _ in capturado["cabecalhos"]]
    assert capturado["valor"] == b"cru"
    assert config.HEADER_RETRY in nomes
    assert "motivo" in nomes
    assert "event-id" in nomes


def test_republicar_publica_no_topico_principal(monkeypatch):
    capturado = {}

    def _produzir(produtor, topico, valor, chave, cabecalhos):
        capturado["topico"] = topico
        capturado["cabecalhos"] = cabecalhos
        return None

    monkeypatch.setattr(ck.produtor_kafka, "_produzir", _produzir)

    assert (
        ck._republicar(object(), _envelope(1), topico="principal", chave="k", tentativa=1)
        is None
    )
    assert capturado["topico"] == "principal"


def test_commit_seguro_engole_excecao():
    class _ConsumidorQuebrado:
        def commit(self, message=None):
            raise RuntimeError("offset")

    ck._commit_seguro(
        _ConsumidorQuebrado(), _Mensagem(), nome_evento="topico"
    )


def test_criar_consumidor_configura_commit_manual(monkeypatch):
    capturado = {}

    class _FakeConsumer:
        def __init__(self, configuracao):
            capturado.update(configuracao)

    monkeypatch.setattr(ck, "Consumer", _FakeConsumer)
    espec = ck.espec_pedido_criado()

    ck._criar_consumidor(espec)

    assert capturado["group.id"] == espec.group_id
    assert capturado["enable.auto.commit"] is False


def test_espec_pedido_criado():
    espec = ck.espec_pedido_criado()
    assert espec.event_type == EVENTO_TYPE
    assert espec.topico == config.KAFKA_TOPIC_PEDIDO_CRIADO
    assert espec.topico_dlq == config.KAFKA_TOPIC_DLQ


def test_rodar_consumidor_processa_o_laco(monkeypatch):
    estado = {"parar": False}
    consumidor = _Consumidor()

    erro_eof = SimpleNamespace(code=lambda: KafkaError._PARTITION_EOF)
    erro_outro = SimpleNamespace(code=lambda: KafkaError.UNKNOWN_TOPIC_OR_PART)
    mensagem_bom = _Mensagem(valor=b"ok")
    mensagem_explode = _Mensagem(valor=b"explode")

    lista = [
        _Mensagem(erro=erro_eof),
        _Mensagem(erro=erro_outro),
        mensagem_bom,
        mensagem_explode,
    ]

    def _poll(_timeout):
        if lista:
            return lista.pop(0)
        estado["parar"] = True
        return None

    consumidor.poll = _poll

    processadas = []

    def _processar(_consumer, message, _produtor, _espec):
        if message is mensagem_explode:
            raise RuntimeError("falha inesperada")
        processadas.append(message)

    monkeypatch.setattr(ck.topologia_kafka, "criar_topicos", lambda **_k: None)
    monkeypatch.setattr(ck.produtor_kafka, "obter_produtor", lambda: object())
    monkeypatch.setattr(ck, "_criar_consumidor", lambda _espec: consumidor)
    monkeypatch.setattr(ck, "processar_mensagem", _processar)
    monkeypatch.setattr(ck.time, "sleep", lambda _s: None)

    ck.rodar_consumidor(deve_parar=lambda: estado["parar"])

    assert consumidor.fechado is True
    assert consumidor.subscrito == [ck.espec_pedido_criado().topico]
    assert processadas == [mensagem_bom]
