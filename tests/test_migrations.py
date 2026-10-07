"""Testes das migrations (Aula 7, ETAPA 11).

Uma migration que só funciona "para frente" é uma armadilha: quando der
problema em produção, o primeiro instinto é voltar uma versão, e aí ela se
descobre. Este módulo sobe o schema de `repositories`, desfaz e refaz, dentro
do banco de teste.

Como o teste roda em `TransactionTestCase` (o runner do Django cria um banco
novo para ele), aplicar e reverter migrations aqui não afeta o banco de
desenvolvimento nem o da aplicação em si.
"""

from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from repositories.models import Category, Item, Role, User
from repositories.user_repository import UserRepository


class TestMigrationsReversiveis(TransactionTestCase):
    """Ciclo apply -> rollback -> reapply das migrations de `repositories`."""

    migrate_from = [("repositories", "0001_initial")]
    # estado final do projeto: da Aula 9 em diante, a última migration é a que
    # cria Pedido/PedidoItem/EventoProcessado (`0004_pedido`). O ciclo só é
    # provado se o reapply voltar exatamente ao schema corrente.
    migrate_to = [("repositories", "0004_pedido")]

    def _aplicar(self, alvos) -> None:
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(alvos)
        executor.loader.build_graph()

    def _tabelas(self) -> set[str]:
        with connection.cursor() as cursor:
            return set(connection.introspection.table_names(cursor))

    def _colunas_de(self, tabela: str) -> set[str]:
        with connection.cursor() as cursor:
            descricao = connection.introspection.get_table_description(cursor, tabela)
        return {coluna.name for coluna in descricao}

    def test_01_ciclo_completo_apply_rollback_reapply(self) -> None:
        # o banco de teste já começa no estado final da aula
        estado_final = self._tabelas()

        # 1) rollback até a migration inicial (sem usuário, sem índice)
        self._aplicar(self.migrate_from)
        self.assertNotIn(User._meta.db_table, self._tabelas())
        self.assertIn(Category._meta.db_table, self._tabelas())

        # 2) reapply até o estado final da aula
        self._aplicar(self.migrate_to)
        self.assertIn(User._meta.db_table, self._tabelas())
        self.assertIn("role", self._colunas_de(User._meta.db_table))
        self.assertIn("admin", Role.values)
        self.assertIn("user", Role.values)

        # 3) e o schema voltou a ser exatamente o que era
        self.assertEqual(self._tabelas(), estado_final)

    def _indices_compostos(self) -> set[tuple]:
        """Índices B-tree de `Item` que começam por uma coluna filtrável."""
        with connection.cursor() as cursor:
            mapa = connection.introspection.get_constraints(
                cursor, Item._meta.db_table
            )
        return {
            tuple(info["columns"])
            for info in mapa.values()
            if info["index"]
        }

    def test_02_rollback_remove_os_indices_de_item(self) -> None:
        self._aplicar(self.migrate_to)
        antes = self._indices_compostos()
        self.assertIn(("category_id", "name"), antes)
        self.assertIn(("is_active", "name"), antes)

        self._aplicar(self.migrate_from)
        depois = self._indices_compostos()

        self.assertNotIn(("category_id", "name"), depois)
        self.assertNotIn(("is_active", "name"), depois)
        # o índice simples da FK, criado automaticamente junto com a migration
        # inicial, continua lá - e passa a ser redundante frente ao composto
        self.assertIn(("category_id",), depois)

    def test_03_reapply_preserva_os_dados_do_catalogo(self) -> None:
        """Reverter o schema da Aula 7 não pode apagar itens já cadastrados."""
        self._aplicar(self.migrate_to)
        categoria = Category.objects.create(name="Eletrônicos")
        item = Item.objects.create(name="Teclado", price="199.90", category=categoria)

        self._aplicar(self.migrate_from)
        self._aplicar(self.migrate_to)

        item.refresh_from_db()
        self.assertEqual(item.name, "Teclado")
        self.assertEqual(item.category.name, "Eletrônicos")

    def test_04_usuario_criado_por_codigo_e_aceito_pelo_schema_final(self) -> None:
        self._aplicar(self.migrate_from)
        self._aplicar(self.migrate_to)

        # pelo mesmo caminho da aplicação (o manager do Django usa a ordem
        # username/email/password, então o repositório é o atalho do projeto)
        user = UserRepository().create_user("ana", "senha-123", Role.USER)

        self.assertEqual(user.role, Role.USER)
        self.assertTrue(user.is_active)
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password("senha-123"))

    def tearDown(self) -> None:
        # deixa o banco de teste sempre no estado final esperado
        self._aplicar(self.migrate_to)
        super().tearDown()