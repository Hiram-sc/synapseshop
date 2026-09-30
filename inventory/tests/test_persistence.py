"""Testes de persistência do microsserviço de inventário.

Rodam contra um PostgreSQL de verdade, e não contra um dublê em memória: o
que está sendo verificado é justamente se os dados chegam ao banco e se as
restrições declaradas no modelo são respeitadas. A suíte cria o banco
`synapseshop_test`, aplica a migration do Alembic nele e o remove ao final,
sem tocar nos dados de desenvolvimento.

Execução, de dentro da rede do Docker Compose:

    docker compose run --rm inventory python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import unittest

# o banco de teste precisa ser apontado antes de qualquer importação do app
BANCO_DE_TESTE = "synapseshop_test"
BANCO_DE_ADMINISTRACAO = os.environ.get("POSTGRES_DB", "synapseshop")
os.environ["POSTGRES_DB"] = BANCO_DE_TESTE

import psycopg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.database import DatabaseSettings, SessionLocal, engine  # noqa: E402
from app.models import InventoryItemModel  # noqa: E402
from app.schemas import InventoryItemCreate, InventoryItemUpdate  # noqa: E402
from app.storage import DuplicateSkuError, InventoryStore  # noqa: E402


def _url_psycopg(banco: str) -> str:
    """URL para o driver psycopg, que não aceita o sufixo `+psycopg`."""
    return (
        f"postgresql://{engine.url.username}:{engine.url.password}"
        f"@{engine.url.host}:{engine.url.port}/{banco}"
    )


def _criar_banco_de_teste() -> None:
    """Recria o banco de teste a partir do zero."""
    with psycopg.connect(_url_psycopg(BANCO_DE_ADMINISTRACAO), autocommit=True) as c:
        with c.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{BANCO_DE_TESTE}"')
            cursor.execute(f'CREATE DATABASE "{BANCO_DE_TESTE}"')


def _remover_banco_de_teste() -> None:
    """Descarta o banco de teste."""
    with psycopg.connect(_url_psycopg(BANCO_DE_ADMINISTRACAO), autocommit=True) as c:
        with c.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{BANCO_DE_TESTE}"')


def setUpModule() -> None:
    _criar_banco_de_teste()
    # a migration do próprio projeto monta o schema do banco de teste
    command.upgrade(Config("alembic.ini"), "head")


def tearDownModule() -> None:
    engine.dispose()
    _remover_banco_de_teste()


class TestPersistencia(unittest.TestCase):
    """Cobre o caminho de sucesso e as regras de integridade do banco."""

    def setUp(self) -> None:
        self._sessao = SessionLocal()
        self.store = InventoryStore(self._sessao)

    def tearDown(self) -> None:
        self._sessao.rollback()
        self._sessao.close()
        # um teste não pode herdar linhas do anterior
        with SessionLocal() as sessao_limpeza:
            sessao_limpeza.execute(delete(InventoryItemModel))
            sessao_limpeza.commit()

    # ---------- caminho de sucesso ----------

    def test_01_cria_item_e_persiste_no_banco(self) -> None:
        item = self.store.create_item(
            InventoryItemCreate(sku="SKU-001", name="Teclado mecânico", quantity=10)
        )

        self.assertIsNotNone(item.id)
        self.assertEqual(item.sku, "SKU-001")
        self.assertEqual(item.quantity, 10)
        self.assertIsNotNone(item.created_at)
        self.assertIsNotNone(item.updated_at)

        # relê de outra sessão: prova que a escrita foi commitada no banco
        with SessionLocal() as sessao_independente:
            salvo = sessao_independente.get(InventoryItemModel, item.id)
        self.assertIsNotNone(salvo)
        self.assertEqual(salvo.name, "Teclado mecânico")

    def test_02_busca_item_por_id(self) -> None:
        criado = self.store.create_item(
            InventoryItemCreate(sku="SKU-ID", name="Mouse", quantity=3)
        )
        encontrado = self.store.get_item(criado.id)
        self.assertIsNotNone(encontrado)
        self.assertEqual(encontrado.sku, "SKU-ID")

    def test_03_busca_item_por_sku(self) -> None:
        self.store.create_item(
            InventoryItemCreate(sku="SKU-BUSCA", name="Monitor", quantity=7)
        )
        encontrado = self.store.get_item_by_sku("SKU-BUSCA")
        self.assertIsNotNone(encontrado)
        self.assertEqual(encontrado.name, "Monitor")
        self.assertIsNone(self.store.get_item_by_sku("SKU-INEXISTENTE"))

    def test_04_lista_itens_ordenados(self) -> None:
        self.store.create_item(InventoryItemCreate(sku="SKU-A", name="A", quantity=1))
        self.store.create_item(InventoryItemCreate(sku="SKU-B", name="B", quantity=2))

        itens = self.store.list_items()
        self.assertEqual(len(itens), 2)
        # ordenação padrão: mais recente primeiro
        self.assertEqual(itens[0].sku, "SKU-B")

    def test_05_atualiza_quantidade(self) -> None:
        criado = self.store.create_item(
            InventoryItemCreate(sku="SKU-ALT", name="Cadeira", quantity=1)
        )
        atualizado = self.store.update_item(criado.id, InventoryItemUpdate(quantity=42))

        self.assertIsNotNone(atualizado)
        self.assertEqual(atualizado.quantity, 42)
        with SessionLocal() as sessao_independente:
            salvo = sessao_independente.get(InventoryItemModel, criado.id)
        self.assertEqual(salvo.quantity, 42)

    def test_06_remove_item(self) -> None:
        criado = self.store.create_item(
            InventoryItemCreate(sku="SKU-DEL", name="Webcam", quantity=1)
        )
        self.assertTrue(self.store.delete_item(criado.id))
        self.assertIsNone(self.store.get_item(criado.id))

    # ---------- regras de integridade ----------

    def test_07_sku_duplicado_e_recusado_pelo_banco(self) -> None:
        self.store.create_item(
            InventoryItemCreate(sku="SKU-DUP", name="Original", quantity=1)
        )
        with self.assertRaises(DuplicateSkuError):
            self.store.create_item(
                InventoryItemCreate(sku="SKU-DUP", name="Repetido", quantity=2)
            )
        # o item original continua intacto
        self.assertEqual(len(self.store.list_items()), 1)

    def test_08_quantidade_negativa_e_recusada_pelo_banco(self) -> None:
        # o payload Pydantic já bloqueia; aqui o valor é forçado para provar
        # que a restrição do PostgreSQL existe de forma independente
        self._sessao.add(
            InventoryItemModel(sku="SKU-NEG", name="Inválido", quantity=-1)
        )
        with self.assertRaises(IntegrityError):
            self._sessao.commit()
        self._sessao.rollback()
        self.assertIsNone(self.store.get_item_by_sku("SKU-NEG"))

    def test_09_sku_nulo_e_recusado_pelo_banco(self) -> None:
        self._sessao.add(
            InventoryItemModel(sku=None, name="Sem SKU", quantity=1)  # type: ignore[arg-type]
        )
        with self.assertRaises(IntegrityError):
            self._sessao.commit()
        self._sessao.rollback()

    def test_10_transacao_nao_deixa_lixo_apos_falha(self) -> None:
        """Falha em uma escrita não pode deixar resíduo na tabela."""
        self.store.create_item(
            InventoryItemCreate(sku="SKU-TX", name="Original", quantity=1)
        )
        with self.assertRaises(DuplicateSkuError):
            self.store.create_item(
                InventoryItemCreate(sku="SKU-TX", name="Falha", quantity=9)
            )

        with SessionLocal() as sessao_independente:
            total = sessao_independente.execute(
                select(func.count()).select_from(InventoryItemModel)
            ).scalar()
        self.assertEqual(total, 1)

    def test_11_atualizar_para_sku_ja_existente_conflita(self) -> None:
        self.store.create_item(
            InventoryItemCreate(sku="SKU-LIVRE", name="Livre", quantity=1)
        )
        ocupado = self.store.create_item(
            InventoryItemCreate(sku="SKU-OCUPADO", name="Ocupado", quantity=1)
        )

        with self.assertRaises(DuplicateSkuError):
            self.store.update_item(ocupado.id, InventoryItemUpdate(sku="SKU-LIVRE"))

        # a sessão foi revertida: o item mantém o SKU que tinha
        self.assertEqual(self.store.get_item(ocupado.id).sku, "SKU-OCUPADO")

    def test_12_update_de_item_inexistente_retorna_none(self) -> None:
        self.assertIsNone(
            self.store.update_item(999999, InventoryItemUpdate(quantity=1))
        )

    def test_13_delete_de_item_inexistente_retorna_false(self) -> None:
        self.assertFalse(self.store.delete_item(999999))


if __name__ == "__main__":
    unittest.main()
