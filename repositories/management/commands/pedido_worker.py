"""Worker consumidor do `PedidoCriado`.

    python manage.py pedido_worker

Serviço separado da API (o mesmo código, outra entrada): a API publica, o
worker consome. O broker é escolhido por `MENSAGERIA_BROKER`
(`services.mensageria.facade`):

* **`kafka`** (padrão da Aula 10): consumidor `confluent-kafka` com
  `enable.auto.commit=False`, retry por republicação com `x-retry-count`,
  backoff exponencial e DLQ em tópico separado. Vários worker no **mesmo
  grupo** repartem as 3 partições automaticamente (worker escalável);
* **qualquer outro valor** (ex.: `rabbitmq`): fluxo da Aula 9, preservado
  como alternativa — prefetch=1, ack manual, DLQ pelo `x-dead-letter`.

O loop de conexão do RabbitMQ obedece à regra da spec - **a conexão é
mantida enquanto saudável e só é recriada quando realmente cai**:

* queda da conexão -> espera fixa (5s) e uma nova tentativa;
* broker fora no momento de conectar -> espera crescente (5s, 10s, 20s...
  até 60s), com log a cada tentativa.

Não há `connection_attempts` agressivo no próprio pika nem retry imediato:
reconectar em rajada só satura o broker e esconde o problema.

`prefetch=1` limita a uma mensagem em aberto por vez: com ack manual, é o
que dá ao contador de tentativas um significado linear e evita que o worker
acumule mensagens que ele mesmo poderia não conseguir processar.
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Optional

from django.core.management.base import BaseCommand

from services.mensageria import config, facade

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Consome o evento PedidoCriado no broker ativo (MENSAGERIA_BROKER: "
        "kafka com idempotência/retry/DLQ, ou rabbitmq da Aula 9)."
    )

    def __init__(self) -> None:
        super().__init__()
        self._parar = False
        self._conexao = None

    # -- parada graciosa ---------------------------------------------------
    def _ao_encerrar(self, signum, frame) -> None:
        """SIGTERM/SIGINT: pede o fim do consumo sem matar a conexão no meio."""
        self._parar = True
        logger.info("Sinal %s recebido; encerrando o worker...", signum)
        conexao = self._conexao
        if conexao is not None and conexao.is_open:
            try:
                # derruba o loop de consumo; a mensagem em aberto volta para
                # a fila (não confirmada) e nada é perdido
                conexao.close()
            except Exception as erro:  # noqa: BLE001
                logger.warning("Não foi possível fechar a conexão: %s", erro)

    # -- consumo -----------------------------------------------------------
    def _ao_receber(self, channel, method, properties, body) -> None:
        from services.mensageria import consumidor

        consumidor.processar(channel, method, properties, body)

    def _consumir(self, conexao) -> None:
        """Declara topologia, liga o consumidor e bloqueia até a conexão cair."""
        from services.mensageria import topologia

        canal = conexao.channel()
        topologia.declarar_topologia(canal)
        # confirm_delivery vale também para as republicações do retry
        canal.confirm_delivery()
        canal.basic_qos(prefetch_count=1)
        canal.basic_consume(
            queue=config.QUEUE_PEDIDO_CRIADO,
            on_message_callback=self._ao_receber,
            auto_ack=False,  # ack manual: confirmar só após processar
        )
        logger.info(
            "Worker consumindo %s (prefetch=1, max_tentativas=%s, backoff_base=%ss)",
            config.QUEUE_PEDIDO_CRIADO,
            config.MAX_TENTATIVAS,
            config.BACKOFF_BASE_S,
        )
        # bloqueia enquanto a conexão estiver viva; retorna (ou levanta) na queda
        canal.start_consuming()

    def _conectar(self) -> Optional[object]:
        """Abre a conexão, esperando de forma crescente se o broker está fora."""
        import pika

        espera = config.ESPERA_RECONEXAO_S
        while not self._parar:
            try:
                conexao = pika.BlockingConnection(config.parametros_conexao())
                self._conexao = conexao
                return conexao
            except Exception as erro:  # noqa: BLE001 - broker ainda fora
                logger.warning(
                    "Sem conexão com o RabbitMQ; nova tentativa em %.0fs. motivo=%s",
                    espera,
                    erro,
                )
                time.sleep(espera)
                espera = min(espera * 2, config.ESPERA_RECONEXAO_MAX_S)
        return None

    def handle(self, *args, **options) -> None:
        signal.signal(signal.SIGTERM, self._ao_encerrar)
        signal.signal(signal.SIGINT, self._ao_encerrar)

        if facade.usando_kafka():
            self._rodar_kafka()
        else:
            self._rodar_rabbitmq()

    def _rodar_kafka(self) -> None:
        """Delega ao consumidor Kafka da Aula 10 (ver serviços.mensageria)."""
        from services.mensageria import consumidor_kafka

        logger.info(
            "Worker iniciando com Apache Kafka: topico=%s dlq=%s group=%s",
            config.KAFKA_TOPIC_PEDIDO_CRIADO,
            config.KAFKA_TOPIC_DLQ,
            config.KAFKA_GROUP_ID,
        )
        consumidor_kafka.rodar_consumidor(deve_parar=lambda: self._parar)
        logger.info("Worker encerrado.")

    def _rodar_rabbitmq(self) -> None:
        logger.info(
            "Worker iniciando: fila=%s exchange=%s dlq=%s",
            config.QUEUE_PEDIDO_CRIADO,
            config.EXCHANGE_EVENTOS,
            config.QUEUE_DLQ,
        )

        while not self._parar:
            conexao = self._conectar()
            if conexao is None:
                break

            try:
                self._consumir(conexao)
            except KeyboardInterrupt:
                self._parar = True
            except Exception as erro:  # noqa: BLE001 - queda da conexão
                if not self._parar:
                    logger.warning(
                        "Conexão com o RabbitMQ caiu; recriando em %.0fs. motivo=%s",
                        config.ESPERA_RECONEXAO_S,
                        erro,
                    )
            finally:
                self._conexao = None
                try:
                    if conexao.is_open:
                        conexao.close()
                except Exception:  # noqa: BLE001
                    pass

            if not self._parar:
                # espera fixa entre recriações: reconexão imediata seria o
                # loop agressivo que a spec proíbe
                time.sleep(config.ESPERA_RECONEXAO_S)

        logger.info("Worker encerrado.")
