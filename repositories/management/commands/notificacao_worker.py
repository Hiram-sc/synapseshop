"""Worker consumidor do `PagamentoProcessado` (Aula 11).

    python manage.py notificacao_worker

Serviço separado da API (o mesmo código, outra entrada): a API publica o
pagamento, este worker consome e fecha o fluxo **Pedido -> Pagamento ->
Notificação**, registrando a notificação e publicando o evento terminal
`NotificacaoEnviada`.

Só existe no **Kafka** (`MENSAGERIA_BROKER=kafka`, padrão): os eventos da
Aula 11 não têm rota no RabbitMQ da Aula 9, então com outro broker o comando
recusa iniciar em vez de ficar num loop silencioso de nada-a-consumir.

O consumo é o mesmo maquinário do pedido-worker (`EspecEvento` + loop do
`consumidor_kafka`), com o que varia nesta especificação:

* tópico `pedidos.pagamentoprocessado` (e a sua DLQ);
* group próprio (`KAFKA_GROUP_ID_NOTIFICACAO`): histórico de offset
  independente do pedido-worker - cada worker com o seu grupo;
* efeito `services.notificacao_service.efeito_pagamento_processado`.
"""

from __future__ import annotations

import logging
import signal

from django.core.management.base import BaseCommand, CommandError

from services.mensageria import config, facade
from services.mensageria.consumidor_kafka import EspecEvento, rodar_consumidor
from services.mensageria.envelope import PAGAMENTO_PROCESSADO
from services.notificacao_service import efeito_pagamento_processado

logger = logging.getLogger(__name__)


def espec_pagamento_processado() -> EspecEvento:
    """Especificação do consumo do `PagamentoProcessado` neste worker."""
    return EspecEvento(
        event_type=PAGAMENTO_PROCESSADO,
        topico=config.KAFKA_TOPIC_PAGAMENTO_PROCESSADO,
        topico_dlq=config.KAFKA_TOPIC_PAGAMENTO_DLQ,
        group_id=config.KAFKA_GROUP_ID_NOTIFICACAO,
        cliente_id=f"{config.KAFKA_CLIENT_ID}-notificacao",
        efeito=efeito_pagamento_processado,
    )


class Command(BaseCommand):
    help = (
        "Consome o evento PagamentoProcessado no Apache Kafka e registra a "
        "notificação (só com MENSAGERIA_BROKER=kafka)."
    )

    def __init__(self) -> None:
        super().__init__()
        self._parar = False

    def _ao_encerrar(self, signum, frame) -> None:
        """SIGTERM/SIGINT: pede o fim do consumo; o loop fecha em ≤1s."""
        self._parar = True
        logger.info("Sinal %s recebido; encerrando o worker...", signum)

    def handle(self, *args, **options) -> None:
        signal.signal(signal.SIGTERM, self._ao_encerrar)
        signal.signal(signal.SIGINT, self._ao_encerrar)

        if not facade.usando_kafka():
            raise CommandError(
                "notificacao-worker exige MENSAGERIA_BROKER=kafka: os eventos "
                "PagamentoProcessado/NotificacaoEnviada não existem no "
                "RabbitMQ da Aula 9."
            )

        espec = espec_pagamento_processado()
        logger.info(
            "Notificacao-worker iniciando com Apache Kafka: topico=%s dlq=%s "
            "group=%s",
            espec.topico,
            espec.topico_dlq,
            espec.group_id,
        )
        rodar_consumidor(deve_parar=lambda: self._parar, espec=espec)
        logger.info("Notificacao-worker encerrado.")
