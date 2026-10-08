"""Criação dos tópicos Kafka da Aula 10 (idempotente).

Topologia Kafka:

```text
produtor --(pedidos.pedidocriado, 3 partições)--> chave de partição = idempotency_key
                                                      |
                                       tentativas esgotadas / contrato inválido
                                                      v
                                   pedidos.pedidocriado.dlq (1 partição)
```

* **`pedidos.pedidocriado`**: 3 partições (`KAFKA_TOPIC_PARTITIONS`) para
  paralelismo do worker; `retention.ms` de 7 dias (`KAFKA_RETENTION_MS_MAIN`);
* **`pedidos.pedidocriado.dlq`**: 1 partição (`KAFKA_DLQ_PARTITIONS`),
  retenção de 28 dias (`KAFKA_RETENTION_MS_DLQ`) para sobrar tempo de
  inspeção/correção de mensagens mortas.

A criação é idempotente e segura para corrida: primeira chamada no processo
cria o que faltar com `AdminClient`; se dois processos baterem ao mesmo tempo
na mesma criação, o `TOPIC_ALREADY_EXISTS` do *future* é tratado como sucesso.
Tópico já existente com partições divergentes é logado como aviso (o Kafka não
diminui partições; ajuste é operacional), mas não aborta a publicação.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Tuple

from confluent_kafka import KafkaError
from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka.error import KafkaException

from services.mensageria import config

logger = logging.getLogger(__name__)

_mutex = threading.Lock()
_topicos_criados = False

#: tópicos (nome, num_particoes, retention_ms) declarados por esta aula
_ALVOS: Dict[str, Tuple[int, int]] = {
    config.KAFKA_TOPIC_PEDIDO_CRIADO: (
        config.KAFKA_TOPIC_PARTITIONS,
        config.KAFKA_RETENTION_MS_MAIN,
    ),
    config.KAFKA_TOPIC_DLQ: (
        config.KAFKA_DLQ_PARTITIONS,
        config.KAFKA_RETENTION_MS_DLQ,
    ),
}


def _admin() -> AdminClient:
    return AdminClient(
        {
            "bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS,
            "security.protocol": config.KAFKA_SECURITY_PROTOCOL,
            "client.id": f"{config.KAFKA_CLIENT_ID}-topologia",
        }
    )


def _divergencias(existente) -> List[str]:
    """Avisos quando o tópico já existe com parâmetros diferentes do desejado.

    O metadata não traz `retention.ms`, então a conferência é a que é possível
    de forma barata: número de partições. Retenção é verificada na validação
    com `describe_configs` (script de smoke test), não a cada boot.
    """
    avisos: List[str] = []
    if len(existente.partitions) != config.KAFKA_TOPIC_PARTITIONS:
        avisos.append(
            f"pedidos.pedidocriado tem {len(existente.partitions)} partições, "
            f"desejado {config.KAFKA_TOPIC_PARTITIONS}"
        )
    return avisos


def criar_topicos(*, forcar: bool = False) -> None:
    """Garante a existência dos tópicos; idempotente dentro do processo."""
    global _topicos_criados

    if _topicos_criados and not forcar:
        return

    with _mutex:
        if _topicos_criados and not forcar:
            return

        admin = _admin()
        metadados = admin.list_topics(timeout=30)

        for nome, (particoes, _retencao) in _ALVOS.items():
            existente = metadados.topics.get(nome)
            if existente is None:
                continue
            for aviso in _divergencias(existente):
                logger.warning("Tópico existente com parâmetros divergentes: %s", aviso)

        novos = [
            NewTopic(
                nome,
                num_partitions=particoes,
                replication_factor=1,
                config={"retention.ms": str(retencao)},
            )
            for nome, (particoes, retencao) in _ALVOS.items()
            if metadados.topics.get(nome) is None
        ]

        if novos:
            futuros = admin.create_topics(novos)
            for nome, futuro in futuros.items():
                try:
                    futuro.result(timeout=30)
                except KafkaException as erro:
                    # dois processos criando o mesmo tópico ao mesmo tempo:
                    # o perdedor recebe TOPIC_ALREADY_EXISTS, que é sucesso
                    if erro.args[0].code() == KafkaError.TOPIC_ALREADY_EXISTS:
                        logger.info("Tópico %s já existia no momento da criação.", nome)
                        continue
                    mensagem = f"falha ao criar o tópico {nome}: {erro}"
                    logger.error(mensagem)
                    raise

        # propagação imediata de metadata ainda não é garantida: espera,
        # limitada, até o broker enxergar os tópicos recém-criados
        for _tentativa in range(30):
            metadados_pos = admin.list_topics(timeout=10)
            if all(metadados_pos.topics.get(nome) is not None for nome in _ALVOS):
                break
            time.sleep(0.3)
        else:
            ausentes = [
                nome for nome in _ALVOS if metadados_pos.topics.get(nome) is None
            ]
            logger.warning(
                "Broker ainda não listou os tópicos (%s); consumer/produtor "
                "podem precisar reconectar.",
                ", ".join(ausentes),
            )

        _topicos_criados = True