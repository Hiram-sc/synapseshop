"""Produtor do evento `PedidoCriado`.

Publica **depois do commit** da transação que criou o pedido (a view registra
o chamador com `transaction.on_commit`): publicar antes significaria que um
rollback deixaria na fila um evento apontando para um pedido que não existe.

Sem outbox nesta etapa (fora do escopo da spec): se o broker recusar a
publicação, a resposta da API sinaliza `evento_publicado: false` com o pedido
já persistido - o dado não se perde, e a reposição do evento é decisão
operacional. A publicação usa `mandatory=True` (mensagem não é silenciosamente
descartada quando não há rota) e `confirm_delivery` (só devolve `True` com a
confirmação do broker).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from services.mensageria import config, topologia
from services.mensageria.envelope import serializar

logger = logging.getLogger(__name__)


def publicar_pedido_criado(envelope: Dict[str, Any]) -> bool:
    """Publica o envelope no exchange principal.

    Devolve `True` somente com a confirmação do broker. Qualquer falha é
    registrada no log e vira `False`: o produtor nunca derruba uma requisição
    cujo pedido já foi persistido.
    """
    import json
    import time

    import pika

    inicio = time.monotonic()
    corpo = serializar(envelope)
    idempotency_key = envelope.get("idempotency_key", "?")
    event_id = envelope.get("event_id", "?")

    def log(mensagem: str, *, resultado: str, **campos) -> None:
        # linha JSON no mesmo formato do consumidor; `nome_evento` em vez de
        # `evento` para não colidir com o keyword do próprio logger
        registro = {
            "nome_evento": config.ROUTING_KEY_PEDIDO_CRIADO,
            "mensagem": mensagem,
            "event_id": event_id,
            "idempotency_key": idempotency_key,
            "duracao_ms": int((time.monotonic() - inicio) * 1000),
            "resultado": resultado,
        }
        registro.update(campos)
        logger.info("%s", json.dumps(registro, ensure_ascii=False, default=str))

    try:
        conexao = pika.BlockingConnection(config.parametros_conexao())
    except Exception as erro:  # noqa: BLE001 - qualquer falha de conexão
        log(
            "produtor sem conexão com o broker",
            resultado="sem_conexao",
            motivo=str(erro),
        )
        return False

    try:
        canal = conexao.channel()
        topologia.declarar_topologia(canal)
        canal.confirm_delivery()

        confirmado = canal.basic_publish(
            exchange=config.EXCHANGE_EVENTOS,
            routing_key=config.ROUTING_KEY_PEDIDO_CRIADO,
            body=corpo,
            properties=pika.BasicProperties(
                content_type="application/json",
                content_encoding="utf-8",
                delivery_mode=2,  # persistente: sobrevive a restart do broker
                message_id=event_id,
                correlation_id=str(envelope.get("correlation_id", "")),
                type=config.ROUTING_KEY_PEDIDO_CRIADO,
            ),
            mandatory=True,
        )
        if confirmado is False:
            raise RuntimeError("broker não confirmou a publicação")
        log("evento publicado", resultado="publicado")
        return True
    except Exception as erro:  # noqa: BLE001 - vira `evento_publicado: false`
        log(
            "publicação recusada pelo broker",
            resultado="publicacao_falhou",
            motivo=str(erro),
        )
        return False
    finally:
        try:
            if conexao.is_open:
                conexao.close()
        except Exception:  # noqa: BLE001 - fechar não pode mascarar o resultado
            pass
