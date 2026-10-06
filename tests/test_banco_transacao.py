"""Testes de banco e transação (Aula 7, ETAPA 11).

Foco em três garantias:

1. a senha nunca é gravada em texto puro;
2. o `create_users` é transacional: um erro no meio do lote não deixa
   usuário pela metade no banco;
3. o papel do repositório é ser uma camada fina e previsível.
"""

from __future__ import annotations

from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from repositories.models import Item, Role, User
from repositories.user_repository import UserRepository, UsernameAlreadyExists
from services.user_service import InvalidRole, UserService
from tests import SENHA_ADMIN, SENHA_USER


class TestIntegridadeDaSenha(TestCase):
    """A senha é sempre hash PBKDF2, nunca texto puro."""

    def test_01_create_user_grava_hash(self) -> None:
        users = UserRepository()
        users.create_user("joana", SENHA_USER, Role.USER)

        user = User.objects.get(username="joana")
        self.assertTrue(user.password.startswith("pbkdf2_sha256$"))
        self.assertTrue(user.check_password(SENHA_USER))
        self.assertNotEqual(user.password, SENHA_USER)

    def test_02_senhas_de_usuarios_diferentes_nao_sao_iguais_no_hash(self) -> None:
        """Mesmo com a mesma senha, o hash difere (salt aleatório)."""
        users = UserRepository()
        users.create_user("ana", SENHA_USER, Role.USER)
        users.create_user("bob", SENHA_USER, Role.USER)

        hashes = set(User.objects.values_list("password", flat=True))
        self.assertEqual(len(hashes), 2)


class TestAtomicidadeDoLote(TestCase):
    """Falha no meio do lote não pode deixar resíduo.

    A atomicidade mora no **repositório** (`UserRepository.create_users`),
    que insere o lote inteiro dentro de um `transaction.atomic`. O serviço,
    por cima, é idempotente: separa o que já existe antes de chegar lá, e é
    por isso que repetir o provisionamento não estoura exceção.
    """

    def setUp(self) -> None:
        self.users = UserRepository()
        self.service = UserService(users=self.users)
        self.lote = [
            {"username": "ana", "password": SENHA_USER, "role": Role.USER},
            {"username": "bob", "password": SENHA_USER, "role": Role.USER},
            {"username": "carla", "password": SENHA_USER, "role": Role.ADMIN},
        ]

    def test_01_lote_completo_cria_todos(self) -> None:
        criados, ja_existentes = self.service.create_users(self.lote)

        self.assertEqual([u.username for u in criados], ["ana", "bob", "carla"])
        self.assertEqual(ja_existentes, [])
        self.assertEqual(User.objects.count(), 3)

    def test_02_username_duplicado_desfaz_o_lote_inteiro(self) -> None:
        User.objects.create_user("bob", "outra-senha", Role.USER)

        with self.assertRaises(UsernameAlreadyExists):
            self.users.create_users(self.lote)

        # "ana" já tinha sido inserida quando "bob" falhou: o rollback tem de
        # tê-la removido, senão o lote fica pela metade
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(User.objects.filter(username="bob").exists())
        self.assertFalse(User.objects.filter(username="ana").exists())

    def test_03_servico_e_idempotente(self) -> None:
        """Repetir o provisionamento não duplica nem levanta exceção."""
        self.service.create_users(self.lote)
        criados, ja_existentes = self.service.create_users(self.lote)

        self.assertEqual(criados, [])
        self.assertEqual(
            sorted(u.username for u in ja_existentes), ["ana", "bob", "carla"]
        )
        self.assertEqual(User.objects.count(), 3)

    def test_04_role_invalida_e_rejeitada_antes_de_tocar_no_banco(self) -> None:
        lotes = [
            {"username": "ana", "password": SENHA_USER, "role": Role.USER},
            {"username": "bob", "password": SENHA_USER, "role": "superadmin"},
        ]

        with self.assertRaises(InvalidRole):
            self.service.create_users(lotes)

        self.assertEqual(User.objects.count(), 0)

    def test_04_rollback_explicito_desfaz_a_saida_do_usuario(self) -> None:
        """Comprovação direta do comportamento do `transaction.atomic`."""
        try:
            with transaction.atomic():
                User.objects.create_user("temporario", SENHA_USER, Role.USER)
                raise RuntimeError("falha simulada")
        except RuntimeError:
            pass

        self.assertFalse(User.objects.filter(username="temporario").exists())


class TestIntegridadeDoBanco(TestCase):
    """Invariantes do PostgreSQL que a aplicação depende."""

    def test_01_username_e_unico(self) -> None:
        User.objects.create_user("ana", SENHA_USER, Role.USER)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user("ana", SENHA_ADMIN, Role.ADMIN)

    def test_02_migracoes_aplicadas(self) -> None:
        """Garante que a tabela de usuários e os índices da aula existem."""
        with connection.cursor() as cursor:
            tabelas = connection.introspection.table_names(cursor)
            self.assertIn(User._meta.db_table, tabelas)
            self.assertIn(Item._meta.db_table, tabelas)

            indices_item = connection.introspection.get_constraints(
                cursor, Item._meta.db_table
            )
            por_colunas = {
                tuple(info["columns"])
                for info in indices_item.values()
                if info["index"]
            }
            self.assertIn(("category_id", "name"), por_colunas)
            self.assertIn(("is_active", "name"), por_colunas)