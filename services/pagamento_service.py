"""Serviço de pagamento simulado de pedidos (Aula 11).

Regras concentradas aqui:

* **um pagamento por pedido**, garantido pelo banco (`OneToOneField`): a
  segunda tentativa levanta `PagamentoJaProcessado` (a view responde 409) e
  nada é criado nem republicado;
* **`RECUSADO` cancela o pedido na mesma transação** - ou o pagamento e o
  cancelamento existem, ou nenhum dos dois. Cancelar é uma mudança de estado
  do pedido, então o cache do `GET /pedidos/{id}/` (Aula 11) é invalidado no
  retorno, dentro da mesma transação;
* **`APROVADO` não muda o status do pedido**: o fluxo normal continua (o
  pedido-worker confirma), e a notificação é o próximo passo do evento.

Nada aqui publica: o envelope é montado e o broker é chamado pela view,
depois do commit - mesmo desenho do `POST /api/v1/pedidos/`.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction

from repositories.models import Pagamento, Pedido, StatusPagamento, StatusPedido
from services import cache as cache_service


class PagamentoJaProcessado(Exception):
    """O pedido já tem pagamento; carrega o existente para a resposta 409."""

    def __init__(self, pagamento: Pagamento) -> None:
        super().__init__(
            f"pagamento {pagamento.pk} já existe para o pedido {pagamento.pedido_id}"
        )
        self.pagamento = pagamento


class PagamentoService:
    """Persiste o pagamento e aplica o desfecho do pedido."""

    def processar(self, pedido: Pedido, status: str) -> Pagamento:
        """Cria o pagamento do pedido; devolve o registro persistido.

        `status` já veio validado do serializer (`APROVADO`/`RECUSADO`).
        Levanta `PagamentoJaProcessado` quando o pedido já foi pago - inclusive
        na corrida entre duas requisições simultâneas: quem perde no banco vê a
        `IntegrityError` e recebe o pagamento de quem venceu.
        """
        existente = Pagamento.objects.filter(pedido=pedido).first()
        if existente is not None:
            raise PagamentoJaProcessado(existente)

        try:
            with transaction.atomic():
                pagamento = Pagamento.objects.create(pedido=pedido, status=status)
                if status == StatusPagamento.RECUSADO:
                    # mesma transação: sem pagamento gravado se o cancelamento
                    # falhar, e vice-versa
                    pedido.status = StatusPedido.CANCELADO
                    pedido.save(update_fields=["status"])
                    # o status do pedido mudou: derruba o cache do `GET /pedidos/
                    # {id}/` (Aula 11). Dentro da transação do pagamento, de
                    # propósito: se der rollback, nada foi invalidado à toa.
                    cache_service.invalidar_pedido(pedido.pk)
        except IntegrityError:
            existente = Pagamento.objects.filter(pedido=pedido).first()
            if existente is not None:
                raise PagamentoJaProcessado(existente) from None
            raise

        return pagamento
