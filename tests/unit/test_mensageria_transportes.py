"""Testes unitários dos transportes de mensageria.

Cobrem configuração, topologia e produtores (RabbitMQ e Kafka) com fakes no
lugar do broker: nenhuma conexão real é aberta.
"""

from __future__ import annotations

from types import SimpleNamespace

import pika
import pytest
from confluent_kafka import KafkaError
from confluent_kafka.error import KafkaException

from services.mensageria import config, produtor, produtor_kafka, topologia
from services.mensageria import topologia_kafka

pytestmark = pytest.mark.unit


def _envelope(event_type="PedidoCriado"):
    return {
        "event_type": event_type,
        "event_id": "evt-1",
        "idempotency_key": "idem-1",
        "correlation_id": "corr-1",
        "version": "1.0",
        "occurred_at": "2026-01-02T03:04:05+00:00",
        "dados": {"pedido": {"id": 1, "total": "9.90", "itens": []}},
    }


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def test_url_amqp_monta_a_url(monkeypatch):
    monkeypatch.setattr(config, "RABBITMQ_USER", "u ser")
    monkeypatch.setattr(config, "RABBITMQ_PASSWORD", "p@ss")
    monkeypatch.setattr(config, "RABBITMQ_HOST", "broker")
    monkeypatch.setattr(config, "RABBITMQ_PORT", 5672)
    monkeypatch.setattr(config, "RABBITMQ_VHOST", "/")

    assert config.url_amqp() == "amqp://u+ser:p%40ss@broker:5672%2F"


def test_parametros_conexao(monkeypatch):
    monkeypatch.setattr(config, "RABBITMQ_HOST", "broker")
    parametros = config.parametros_conexao()

    assert parametros.host == "broker"
    assert parametros.connection_attempts == 1


# ---------------------------------------------------------------------------
# topologia rabbit
# ---------------------------------------------------------------------------
class _CanalFake:
    def __init__(self):
        self.chamadas = []

    def exchange_declare(self, **kwargs):
        self.chamadas.append(("exchange", kwargs))

    def queue_declare(self, **kwargs):
        self.chamadas.append(("queue", kwargs))

    def queue_bind(self, **kwargs):
        self.chamadas.append(("bind", kwargs))


def test_declarar_topologia_declara_tudo():
    canal = _CanalFake()
    topologia.declarar_topologia(canal)

    tipos = [nome for nome, _ in canal.chamadas]
    assert tipos.count("exchange") == 2
    assert tipos.count("queue") == 2
    assert tipos.count("bind") == 2


def test_argumentos_da_fila_apontam_para_a_dlq():
    assert topologia.ARGUMENTOS_FILA["x-dead-letter-exchange"] == config.EXCHANGE_DLX
    assert topologia.ARGUMENTOS_FILA["x-dead-letter-routing-key"] == config.QUEUE_DLQ


# ---------------------------------------------------------------------------
# produtor rabbit
# ---------------------------------------------------------------------------
class _CanalProdutor:
    def __init__(self, publish_result=True, raise_publish=False):
        self.publish_result = publish_result
        self.raise_publish = raise_publish
        self.publicados = []

    def confirm_delivery(self):
        return None

    def basic_publish(self, **kwargs):
        if self.raise_publish:
            raise RuntimeError("broker recusou")
        self.publicados.append(kwargs)
        return self.publish_result


class _ConexaoProdutor:
    def __init__(self, canal, is_open=True):
        self._canal = canal
        self.is_open = is_open
        self.fechada = False

    def channel(self):
        return self._canal

    def close(self):
        self.fechada = True


def _preparar_pika(monkeypatch, conexao_ou_erro):
    def _blocking_connection(_parametros):
        if isinstance(conexao_ou_erro, Exception):
            raise conexao_ou_erro
        return conexao_ou_erro

    monkeypatch.setattr(pika, "BlockingConnection", _blocking_connection)
    monkeypatch.setattr(pika, "BasicProperties", lambda **kwargs: kwargs)


def test_produtor_rabbit_publica(monkeypatch, transporte_real):
    canal = _CanalProdutor()
    conexao = _ConexaoProdutor(canal)
    _preparar_pika(monkeypatch, conexao)
    monkeypatch.setattr(topologia, "declarar_topologia", lambda _c: None)

    assert produtor.publicar_pedido_criado(_envelope()) is True
    assert len(canal.publicados) == 1
    assert conexao.fechada is True


def test_produtor_rabbit_sem_conexao(monkeypatch, transporte_real):
    _preparar_pika(monkeypatch, RuntimeError("fora do ar"))

    assert produtor.publicar_pedido_criado(_envelope()) is False


def test_produtor_rabbit_nao_confirmado(monkeypatch, transporte_real):
    canal = _CanalProdutor(publish_result=False)
    conexao = _ConexaoProdutor(canal)
    _preparar_pika(monkeypatch, conexao)
    monkeypatch.setattr(topologia, "declarar_topologia", lambda _c: None)

    assert produtor.publicar_pedido_criado(_envelope()) is False


def test_produtor_rabbit_publicacao_levanta(monkeypatch, transporte_real):
    canal = _CanalProdutor(raise_publish=True)
    conexao = _ConexaoProdutor(canal)
    _preparar_pika(monkeypatch, conexao)
    monkeypatch.setattr(topologia, "declarar_topologia", lambda _c: None)

    assert produtor.publicar_pedido_criado(_envelope()) is False


# ---------------------------------------------------------------------------
# produtor kafka
# ---------------------------------------------------------------------------
class _ProdutorKafkaFake:
    def __init__(
        self, flush_pendentes=0, delivery_error=None, produce_raises=False
    ):
        self.flush_pendentes = flush_pendentes
        self.delivery_error = delivery_error
        self.produce_raises = produce_raises
        self._callback = None
        self.produzidos = []

    def produce(self, **kwargs):
        if self.produce_raises:
            raise BufferError("buffer cheio")
        self._callback = kwargs.get("callback")
        self.produzidos.append(kwargs)

    def flush(self, _timeout):
        if self._callback is not None:
            self._callback(self.delivery_error, None)
        return self.flush_pendentes


def test_obter_produtor_memoiza(monkeypatch):
    criados = []

    class _FakeProducer:
        def __init__(self, _config):
            criados.append(_config)

    monkeypatch.setattr(produtor_kafka, "_produtor_instancia", None)
    monkeypatch.setattr(produtor_kafka, "Producer", _FakeProducer)

    primeiro = produtor_kafka.obter_produtor()
    segundo = produtor_kafka.obter_produtor()

    assert primeiro is segundo
    assert len(criados) == 1


def test_produzir_sucesso():
    produtor_fake = _ProdutorKafkaFake()
    assert (
        produtor_kafka._produzir(produtor_fake, "topico", b"corpo", "chave", [])
        is None
    )


def test_produzir_flush_pendente():
    produtor_fake = _ProdutorKafkaFake(flush_pendentes=2)
    erro = produtor_kafka._produzir(
        produtor_fake, "topico", b"corpo", "chave", []
    )
    assert isinstance(erro, RuntimeError)


def test_produzir_erro_de_entrega():
    erro = RuntimeError("entrega falhou")
    produtor_fake = _ProdutorKafkaFake(delivery_error=erro)
    assert (
        produtor_kafka._produzir(produtor_fake, "topico", b"corpo", "chave", [])
        is erro
    )


def test_produzir_erro_no_produce():
    produtor_fake = _ProdutorKafkaFake(produce_raises=True)
    erro = produtor_kafka._produzir(
        produtor_fake, "topico", b"corpo", "chave", []
    )
    assert isinstance(erro, BufferError)


def test_publicar_evento_sem_topico(transporte_real):
    assert produtor_kafka.publicar_evento({"event_type": "Inexistente"}) is False


def test_publicar_evento_kafka_sucesso(monkeypatch, transporte_real):
    monkeypatch.setattr(topologia_kafka, "criar_topicos", lambda **_k: None)
    monkeypatch.setattr(produtor_kafka, "obter_produtor", lambda: object())
    monkeypatch.setattr(
        produtor_kafka, "_produzir", lambda *a, **k: None
    )

    assert produtor_kafka.publicar_evento(_envelope()) is True


def test_publicar_evento_kafka_falha(monkeypatch, transporte_real):
    monkeypatch.setattr(topologia_kafka, "criar_topicos", lambda **_k: None)
    monkeypatch.setattr(produtor_kafka, "obter_produtor", lambda: object())
    monkeypatch.setattr(
        produtor_kafka, "_produzir", lambda *a, **k: RuntimeError("recusado")
    )

    assert produtor_kafka.publicar_evento(_envelope()) is False


def test_publicar_pedido_criado_e_alias(monkeypatch, transporte_real):
    monkeypatch.setattr(topologia_kafka, "criar_topicos", lambda **_k: None)
    monkeypatch.setattr(produtor_kafka, "obter_produtor", lambda: object())
    monkeypatch.setattr(produtor_kafka, "_produzir", lambda *a, **k: None)

    assert produtor_kafka.publicar_pedido_criado(_envelope()) is True


# ---------------------------------------------------------------------------
# topologia kafka
# ---------------------------------------------------------------------------
class _ErroKafka:
    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code


class _Futuro:
    def __init__(self, erro=None):
        self._erro = erro

    def result(self, timeout=None):
        if self._erro is not None:
            raise self._erro


class _AdminFake:
    def __init__(self, antes, depois, criar=None):
        self._antes = antes
        self._depois = depois
        self._criar = criar
        self.chamadas = 0
        self.topicos_criados = None

    def list_topics(self, timeout=None):
        self.chamadas += 1
        topicos = self._antes if self.chamadas == 1 else self._depois
        return SimpleNamespace(topics=topicos)

    def create_topics(self, novos):
        self.topicos_criados = novos
        return self._criar or {}


def _todos_os_topicos():
    return {nome: SimpleNamespace(partitions=[0, 1, 2]) for nome in topologia_kafka._ALVOS}


def test_divergencias_de_particoes():
    existente = SimpleNamespace(partitions=[0])
    nome = config.KAFKA_TOPIC_PEDIDO_CRIADO

    avisos = topologia_kafka._divergencias(nome, existente)

    assert len(avisos) == 1
    assert "desejado 3" in avisos[0]


def test_criar_topicos_cria_os_que_faltam(monkeypatch):
    monkeypatch.setattr(topologia_kafka, "_topicos_criados", False)
    admin = _AdminFake(antes={}, depois=_todos_os_topicos())
    monkeypatch.setattr(topologia_kafka, "_admin", lambda: admin)

    topologia_kafka.criar_topicos()

    assert admin.topicos_criados is not None
    assert len(admin.topicos_criados) == len(topologia_kafka._ALVOS)


def test_criar_topicos_avisa_divergencia(monkeypatch):
    monkeypatch.setattr(topologia_kafka, "_topicos_criados", False)
    antes = {
        config.KAFKA_TOPIC_PEDIDO_CRIADO: SimpleNamespace(partitions=[0]),
    }
    depois = _todos_os_topicos()
    admin = _AdminFake(antes=antes, depois=depois)
    monkeypatch.setattr(topologia_kafka, "_admin", lambda: admin)

    topologia_kafka.criar_topicos()


def test_criar_topicos_ja_existe_na_corrida(monkeypatch):
    monkeypatch.setattr(topologia_kafka, "_topicos_criados", False)
    erro = KafkaException(_ErroKafka(KafkaError.TOPIC_ALREADY_EXISTS))
    criar = {
        nome: _Futuro(erro=erro) for nome in topologia_kafka._ALVOS
    }
    admin = _AdminFake(antes={}, depois=_todos_os_topicos(), criar=criar)
    monkeypatch.setattr(topologia_kafka, "_admin", lambda: admin)

    topologia_kafka.criar_topicos()


def test_criar_topicos_erro_propaga(monkeypatch):
    monkeypatch.setattr(topologia_kafka, "_topicos_criados", False)
    erro = KafkaException(_ErroKafka(KafkaError.UNKNOWN_TOPIC_OR_PART))
    criar = {config.KAFKA_TOPIC_PEDIDO_CRIADO: _Futuro(erro=erro)}
    admin = _AdminFake(antes={}, depois=_todos_os_topicos(), criar=criar)
    monkeypatch.setattr(topologia_kafka, "_admin", lambda: admin)

    with pytest.raises(KafkaException):
        topologia_kafka.criar_topicos()


def test_criar_topicos_memoiza(monkeypatch):
    monkeypatch.setattr(topologia_kafka, "_topicos_criados", True)

    def _explodir():
        raise AssertionError("não devia consultar o admin")

    monkeypatch.setattr(topologia_kafka, "_admin", _explodir)

    topologia_kafka.criar_topicos()
