"""Produtor Kafka dos eventos do SynapseShop (Aula 10, generalizado na 11).

Mesmo contrato do produtor RabbitMQ da Aula 9 (`publicar_pedido_criado` ->
`True`/`False`), trocando o handshake de confirmação:

* **`key = idempotency_key`**: é a chave de partição — todos os eventos de uma
  mesma chave de negócio caem na mesma partição, preservando a ordem por
  pedido e permitindo escalar o worker por partição sem quebrar ordenação;
* **confirmação por `flush()` + delivery callback**: `produce()` é
  assíncrono; a função só devolve `True` quando a mensagem foi entregue e
  reconhecida (`acks=all`), e `False` em qualquer falha (broker fora, tópico
  inexistente, mensagem rejeitada);
* **`x-retry-count: 0` no cabeçalho**: cabeçalhos carregam o contrato de
  reentrega, idem ao `x-dead-letter` da Aula 9;
* **mesma política de log estruturado** com `duracao_ms` e o resultado, para o
  fluxo ser reconstruível por `docker compose logs`.

O tópico é resolvido pelo `event_type` no mapa de `config` (o mesmo que a
topologia e o consumidor usam), garantido de forma idempotente antes de cada
publicação (`topologia_kafka.criar_topicos`, memoizado por processo) e a
única instância de `Producer` é compartilhada por processo (uma conexão,
thread-safe).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from confluent_kafka import Producer

from services.mensageria import config, topologia_kafka
from services.mensageria.envelope import serializar

logger = logging.getLogger(__name__)

#: teto do `flush()` por publicação; folgado para um single-message
_PRAZO_FLUSH_S = 15.0

_mutex = threading.Lock()
_produtor_instancia: Optional[Producer] = None


def obter_produtor() -> Producer:
    """Devolve o `Producer` único do processo (cria sob demanda)."""
    global _produtor_instancia
    if _produtor_instancia is None:
        with _mutex:
            if _produtor_instancia is None:
                _produtor_instancia = Producer(
                    {
                        "bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS,
                        "client.id": f"{config.KAFKA_CLIENT_ID}-produtor",
                        "security.protocol": config.KAFKA_SECURITY_PROTOCOL,
                        "acks": config.KAFKA_ACKS,
                        "retries": config.KAFKA_RETRIES,
                        "linger.ms": config.KAFKA_LINGER_MS,
                    }
                )
    return _produtor_instancia


def _produzir(
    produtor: Producer,
    topico: str,
    valor: bytes,
    chave: str,
    cabecalhos: List[tuple],
) -> Optional[Exception]:
    """Produce + flush com entrega verificada.

    Devolve `None` na confirmação e o erro do delivery em caso de falha.
    """
    entregue: List[Optional[Exception]] = []

    def _cb(erro, message) -> None:  # noqa: ARG001 - message do callback
        entregue.append(erro)

    try:
        produtor.produce(
            topic=topico,
            value=valor,
            key=chave.encode("utf-8"),
            headers=cabecalhos or None,
            callback=_cb,
        )
    except Exception as erro:  # noqa: BLE001 - BufferError/KafkaException etc.
        return erro
    pendentes = produtor.flush(_PRAZO_FLUSH_S)
    if pendentes > 0:
        return RuntimeError(
            f"flush esgotou ({_PRAZO_FLUSH_S}s) com {pendentes} mensagem(ns) pendente(s)"
        )
    erros = [erro for erro in entregue if erro is not None]
    return erros[0] if erros else None


def publicar_evento(envelope: Dict[str, Any]) -> bool:
    """Publica o envelope no tópico do seu `event_type`, com a chave de partição.

    Chave = `idempotency_key`, como na Aula 9/10. Devolve `True` somente com
    a confirmação do broker. Qualquer falha é registrada no log e vira
    `False`: o produtor nunca derruba uma requisição cujo registro já foi
    persistido. Evento sem tópico no mapa também vira `False` com log —
    nunca cai em tópico errado.
    """
    topico = config.KAFKA_TOPICO_POR_EVENTO.get(envelope.get("event_type"))
    if topico is None:
        logger.error(
            "event_type sem tópico configurado: %r",
            envelope.get("event_type"),
        )
        return False

    inicio = time.monotonic()
    idempotency_key = envelope.get("idempotency_key", "?")
    event_id = envelope.get("event_id", "?")
    correlation_id = str(envelope.get("correlation_id", ""))

    def log(mensagem: str, *, resultado: str, **campos: Any) -> None:
        registro = {
            "nome_evento": topico,
            "mensagem": mensagem,
            "event_id": event_id,
            "idempotency_key": idempotency_key,
            "duracao_ms": int((time.monotonic() - inicio) * 1000),
            "resultado": resultado,
        }
        registro.update(campos)
        logger.info("%s", json.dumps(registro, ensure_ascii=False, default=str))

    try:
        topologia_kafka.criar_topicos()
        produtor = obter_produtor()
        erro = _produzir(
            produtor,
            topico,
            serializar(envelope),
            idempotency_key,
            [
                (config.HEADER_RETRY, b"0"),
                ("motivo", "publicacao-inicial".encode("utf-8")),
                ("event-id", event_id.encode("utf-8")),
                ("correlation-id", correlation_id.encode("utf-8")),
            ],
        )
        if erro is not None:
            raise RuntimeError(f"kafka recusou a publicação: {erro}")
        log("evento publicado", resultado="publicado")
        return True
    except Exception as erro:  # noqa: BLE001 - vira `evento_publicado: false`
        log(
            "publicação recusada pelo broker",
            resultado="publicacao_falhou",
            motivo=str(erro),
        )
        return False


def publicar_pedido_criado(envelope: Dict[str, Any]) -> bool:
    """Alias do caminho da Aula 9/10: publica `PedidoCriado` (e só ele).

    Mantém o nome que a view e os testes já chamam; a resolução do tópico é
    idêntica à de `publicar_evento` para este `event_type`.
    """
    return publicar_evento(envelope)