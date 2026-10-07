"""Idempotência do consumo: Redis como caminho rápido, PostgreSQL como fonte.

Duas camadas, com papéis diferentes:

* **Redis com TTL** - pergunta barata, feita a cada mensagem. Se a chave já
  está aqui, o evento foi processado dentro da janela e a mensagem é
  confirmada sem tocar o banco. A expiração é automática e o TTL é
  configurável (`PEDIDO_IDEMPOTENCIA_TTL_S`).
* **PostgreSQL (`EventoProcessado`)** - registro durável, com `UNIQUE` em
  `(event_type, idempotency_key)`. É dele que vem a garantia contra corrida
  entre consumidores e contra o Redis ter perdido a chave (eviction, restart,
  TTL vencido).

Ordem obrigatória, definida pela spec:

```text
receber -> validar contrato -> verificar idempotência -> executar efeito
        -> persistir registro -> confirmar mensagem
```

A chave é registrada **depois** do efeito, nunca antes: se o processamento
falhar logo após gravar a chave, uma reentrega seria tratada como duplicata e
descartada sem que o efeito tivesse acontecido.

O Redis é cache de resultado, não autoridade: se ele estiver fora, a consulta
cai no PostgreSQL; se ele devolver `None` por fail-open, idem. O inverso não
aplica - a escrita no banco acontece sempre.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.core.cache import cache
from django.db import IntegrityError, transaction

from repositories.models import EventoProcessado, Pedido
from services.mensageria import config

logger = logging.getLogger(__name__)


def _chave_redis(event_type: str, idempotency_key: str) -> str:
    return f"{config.PREFIXO_IDEMPOTENCIA}:{event_type}:{idempotency_key}"


def ja_processado(event_type: str, idempotency_key: str) -> bool:
    """`True` se o evento já tem registro durável (via Redis ou banco)."""
    try:
        if cache.get(_chave_redis(event_type, idempotency_key)) is not None:
            return True
    except Exception:  # noqa: BLE001 - fail-open: decide o PostgreSQL
        logger.warning(
            "Caminho rápido de idempotência indisponível; consultando o banco."
        )

    return EventoProcessado.objects.filter(
        event_type=event_type, idempotency_key=idempotency_key
    ).exists()


def registrar(
    event_type: str,
    idempotency_key: str,
    event_id: str,
    pedido: Optional[Pedido] = None,
) -> bool:
    """Grava o registro durável e acende o caminho rápido no Redis.

    Devolve `True` quando esta instância criou o registro e `False` quando
    outro consumidor chegou primeiro (a `UNIQUE` do banco é a trava de
    corrida). Sempre chamado **depois** do efeito no pedido.
    """
    try:
        with transaction.atomic():
            EventoProcessado.objects.create(
                event_id=event_id,
                event_type=event_type,
                idempotency_key=idempotency_key,
                pedido=pedido,
            )
        criado = True
    except IntegrityError:
        # outra instância registrou a mesma chave primeiro: para ela, o
        # efeito também já foi feito - o consumidor só confirma a mensagem
        logger.info(
            "Registro de idempotência já existia: event_type=%s idempotency_key=%s",
            event_type,
            idempotency_key,
        )
        criado = False

    if criado:
        try:
            cache.set(
                _chave_redis(event_type, idempotency_key),
                event_id,
                timeout=config.TTL_IDEMPOTENCIA_S,
            )
        except Exception as erro:  # noqa: BLE001 - o banco já é a garantia
            logger.warning(
                "Falha ao gravar a chave rápida de idempotência: %s", erro
            )

    return criado


def limpar_registros(antes_de_dias: int) -> int:
    """Apaga `EventoProcessado` mais velhos que `antes_de_dias` dias.

    O TTL do Redis não remove nada do PostgreSQL, então sem esta limpeza a
    tabela cresceria para sempre. Os registros antigos só saem depois da
    janela em que uma reentrega atrasada ainda poderia chegar.
    """
    from datetime import timedelta

    from django.utils import timezone

    limite = timezone.now() - timedelta(days=antes_de_dias)
    removidos, _ = EventoProcessado.objects.filter(
        processado_em__lt=limite
    ).delete()
    return removidos
