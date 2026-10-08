"""Efeito do notificacao-worker: notificar o pagamento (Aula 11).

`efeito_pagamento_processado` é a função pura injetada na `EspecEvento` do
consumidor Kafka - mesma maquina de estados do `PedidoCriado` (validação,
idempotência, retry/DLQ, commit manual), com o efeito trocado por este:

* **`RECUSADO` não notifica**: o cancelamento do pedido já foi aplicado pela
  API na mesma transação do pagamento; o worker só confirma o consumo;
* **`APROVADO` registra a notificação** com `get_or_create` no `OneToOne` de
  `Pagamento` - a reentrega do evento vira consulta, nunca duplicata;
* **`NotificacaoEnviada` sai depois do registro**, pelo mesmo broker
  (Kafka). Se a publicação for recusada, o efeito levanta erro: o consumidor
  república o `PagamentoProcessado` com backoff e o `get_or_create` garante
  que a notificação não é recriada no caminho de volta.

A idempotência é, portanto, em duas camadas: a do consumidor (Redis, por
`idempotency_key`) e a do banco (`OneToOne Pagamento -> Notificacao`).
"""

from __future__ import annotations

from typing import Any, Dict

from repositories.models import Notificacao, Pagamento, Pedido, StatusPagamento
from services.mensageria import facade
from services.mensageria.envelope import montar_notificacao_enviada


def efeito_pagamento_processado(envelope: Dict[str, Any]) -> Pedido:
    """Registra a notificação (se aprovado) e publica `NotificacaoEnviada`.

    Devolve o pedido, no mesmo contrato de `consumidor._executar_efeito`
    (é o que o registro de idempotência e o log do consumidor esperam).
    Pedido/pagamento inexistentes no banco viram exceção comum: o consumidor
    aplica retry e, esgotadas as tentativas, a DLQ guarda a mensagem.
    """
    dados = envelope["dados"]
    status = dados["status"]
    pedido_id = dados["pedido"]["id"]
    pagamento_id = dados["pagamento"]["id"]

    pedido = Pedido.objects.get(pk=pedido_id)

    if status == StatusPagamento.RECUSADO:
        # nada a notificar: a API já gravou o cancelamento do pedido
        return pedido

    pagamento = Pagamento.objects.get(pk=pagamento_id)
    notificacao, _criada = Notificacao.objects.get_or_create(
        pagamento=pagamento,
        defaults={
            "pedido": pedido,
            "mensagem": f"Pagamento aprovado para o pedido {pedido_id}.",
        },
    )

    publicado = facade.publicar_evento(
        montar_notificacao_enviada(notificacao, pagamento, pedido)
    )
    if not publicado:
        # registro durou; o retry do consumidor repassa o PagamentoProcessado
        # e o get_or_create vira consulta - a notificação sai sem duplicar
        raise RuntimeError("broker recusou a publicação de NotificacaoEnviada")

    return pedido
