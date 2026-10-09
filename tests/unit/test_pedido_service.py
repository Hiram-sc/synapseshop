"""Testes unitários das regras de pedido (`services.pedido_service`)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import IntegrityError

from services.pedido_service import (
    PedidoInvalido,
    PedidoService,
    total_formatado,
)


def _item(item_id, nome="Teclado", preco="10.00", ativo=True):
    return SimpleNamespace(
        id=item_id, name=nome, price=Decimal(preco), is_active=ativo
    )


class RepoFake:
    """Repositório de pedidos em memória - sem tocar o banco."""

    def __init__(self, catalogo=None, existente=None):
        self.catalogo = catalogo or {}
        self.existente = existente
        self.criados = []

    def get_by_idempotency_key(self, usuario, chave):
        return self.existente

    def get_itens_catalogo(self, ids):
        return {i: self.catalogo[i] for i in ids if i in self.catalogo}

    def criar_com_itens(self, **kwargs):
        self.criados.append(kwargs)
        return SimpleNamespace(pk=99, **kwargs)


class RepoCorrida(RepoFake):
    """Simula a corrida: o `create` estoura `IntegrityError`."""

    def __init__(self, catalogo, existente_apos):
        super().__init__(catalogo=catalogo)
        self.existente_apos = existente_apos
        self._chamadas = 0

    def get_by_idempotency_key(self, usuario, chave):
        self._chamadas += 1
        return None if self._chamadas == 1 else self.existente_apos

    def criar_com_itens(self, **kwargs):
        raise IntegrityError("UNIQUE (usuario, idempotency_key)")


# ---------------------------------------------------------------------------
# validação
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_validar_itens_monta_as_linhas_com_preco_congelado():
    repo = RepoFake(catalogo={1: _item(1, "Teclado", "349.90")})
    linhas = PedidoService(pedidos=repo)._validar_itens(
        [{"item_id": 1, "quantidade": 2}]
    )
    assert len(linhas) == 1
    assert linhas[0].preco_unitario == Decimal("349.90")
    assert linhas[0].nome_item == "Teclado"
    assert linhas[0].subtotal == Decimal("699.80")


@pytest.mark.unit
def test_validar_itens_rejeita_item_repetido():
    repo = RepoFake(catalogo={1: _item(1)})
    with pytest.raises(PedidoInvalido, match="repetido"):
        PedidoService(pedidos=repo)._validar_itens(
            [{"item_id": 1, "quantidade": 1}, {"item_id": 1, "quantidade": 1}]
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("itens", "fragmento"),
    [
        ([{"item_id": 1, "quantidade": 0}], "no mínimo 1"),
        ([{"item_id": 2, "quantidade": 1}], "não existe"),
        ([{"item_id": 1, "quantidade": 1}], "está inativo"),
    ],
)
def test_validar_itens_rejeita_item_invalido(itens, fragmento):
    catalogo = {1: _item(1, ativo=False)}
    repo = RepoFake(catalogo=catalogo)
    with pytest.raises(PedidoInvalido, match=fragmento):
        PedidoService(pedidos=repo)._validar_itens(itens)


@pytest.mark.unit
def test_validar_itens_agrega_varios_erros():
    repo = RepoFake(catalogo={2: _item(2, ativo=False)})
    with pytest.raises(PedidoInvalido) as excinfo:
        PedidoService(pedidos=repo)._validar_itens(
            [{"item_id": 1, "quantidade": 1}, {"item_id": 2, "quantidade": 1}]
        )
    assert len(excinfo.value.erros) == 2


# ---------------------------------------------------------------------------
# criação
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_criar_devolve_existente_sem_duplicar():
    existente = SimpleNamespace(pk=1)
    repo = RepoFake(existente=existente)
    pedido, criado = PedidoService(pedidos=repo).criar(
        usuario=SimpleNamespace(pk=1), idempotency_key="k", itens=[]
    )
    assert pedido is existente
    assert criado is False
    assert repo.criados == []


@pytest.mark.unit
def test_criar_calcula_o_total_no_servidor():
    repo = RepoFake(
        catalogo={1: _item(1, preco="349.90"), 2: _item(2, preco="79.90")}
    )
    pedido, criado = PedidoService(pedidos=repo).criar(
        usuario=SimpleNamespace(pk=1),
        idempotency_key="k",
        itens=[
            {"item_id": 2, "quantidade": 2},
            {"item_id": 1, "quantidade": 1},
        ],
    )
    assert criado is True
    assert pedido.pk == 99
    assert repo.criados[0]["total"] == Decimal("509.70")
    assert len(repo.criados[0]["linhas"]) == 2


@pytest.mark.unit
def test_criar_perde_a_corrida_e_devolve_o_vencedor():
    vencedor = SimpleNamespace(pk=7)
    repo = RepoCorrida(catalogo={1: _item(1)}, existente_apos=vencedor)
    pedido, criado = PedidoService(pedidos=repo).criar(
        usuario=SimpleNamespace(pk=1),
        idempotency_key="k",
        itens=[{"item_id": 1, "quantidade": 1}],
    )
    assert pedido is vencedor
    assert criado is False


@pytest.mark.unit
def test_criar_propaga_integrity_error_sem_vencedor():
    repo = RepoCorrida(catalogo={1: _item(1)}, existente_apos=None)
    with pytest.raises(IntegrityError):
        PedidoService(pedidos=repo).criar(
            usuario=SimpleNamespace(pk=1),
            idempotency_key="k",
            itens=[{"item_id": 1, "quantidade": 1}],
        )


# ---------------------------------------------------------------------------
# correção pontual (D5): total_formatado
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize(
    ("total", "esperado"),
    [
        (Decimal("699.80"), "699.80"),
        (Decimal("0.00"), "0.00"),
        (Decimal("1E+2"), "100"),
    ],
)
def test_total_formatado(total, esperado):
    assert total_formatado(SimpleNamespace(total=total)) == esperado
