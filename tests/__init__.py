"""Base dos testes da API Django (SynapseShop, Aula 8).

Regras comuns a todos os módulos de teste:

* o banco é o PostgreSQL de verdade, em um banco separado (`test_<POSTGRES_DB>`)
  criado e destruído pelo runner do Django - nenhum teste toca no banco de
  desenvolvimento;
* o cache é limpo entre os testes. No Redis, `cache.clear()` é um `FLUSHDB`:
  apaga tanto o namespace do catálogo quanto os contadores de throttling, que é
  exatamente o estado inicial que cada teste precisa;
* as métricas do cache (hits, misses, bypasses) são zeradas junto, para que um
  teste não dependa das requisições do anterior;
* os usuários `admin` e `user` são criados pelo repositório, e não por
  atalhos do Django, para que o caminho testado seja o mesmo da aplicação.
"""

from __future__ import annotations

from django.core.cache import cache
from rest_framework.test import APITestCase

from repositories.models import Category, Item, Role
from repositories.user_repository import UserRepository
from services import cache as cache_service
from services import events
from services.auth_service import AuthService

SENHA_ADMIN = "senha-admin-de-teste"
SENHA_USER = "senha-user-de-teste"


class ApiTestCaseBase(APITestCase):
    """Classe base com usuários, catálogo e limpeza de cache prontos."""

    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        cache_service.zerar_metricas()
        self.addCleanup(cache.clear)
        self.addCleanup(cache_service.zerar_metricas)

        self.users = UserRepository()
        self.auth = AuthService(users=self.users)
        self.admin = self.users.create_user("admin", SENHA_ADMIN, Role.ADMIN)
        self.usuario = self.users.create_user("user", SENHA_USER, Role.USER)

        self.categoria = Category.objects.create(name="Eletrônicos")
        self.outra_categoria = Category.objects.create(name="Livros")
        self.item_ativo = Item.objects.create(
            name="Teclado mecânico", price="349.90", category=self.categoria
        )
        self.item_inativo = Item.objects.create(
            name="Teclado antigo",
            price="99.90",
            category=self.categoria,
            is_active=False,
        )
        self.livro = Item.objects.create(
            name="Livro de Python", price="79.90", category=self.outra_categoria
        )

    # ---------- atalhos de requisição ----------

    def token_de(self, user) -> str:
        """Emite um token válido para o usuário, sem passar pelo endpoint."""
        return self.auth.issue_token(user)

    def auth_de(self, user) -> dict:
        """Header `Authorization` com o token do usuário."""
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token_de(user)}"}

    def fazer_login(self, username: str, password: str) -> str:
        """Executa `POST /api/v1/auth/login/` e devolve o token de acesso."""
        resposta = self.client.post(
            "/api/v1/auth/login/",
            {"username": username, "password": password},
            format="json",
        )
        self.assertEqual(resposta.status_code, 200, resposta.data)
        return resposta.data["access"]

    def assinar_evento(self, nome: str, callback) -> None:
        """Inscreve um assinante temporário e o cancela no fim do teste.

        O cancelamento é por assinatura, e não um "limpa tudo": as regras de
        invalidação do cache foram registradas no boot do processo e precisam
        continuar ativas para os testes seguintes.
        """
        events.inscrever(nome, callback)
        self.addCleanup(events.cancelar, nome, callback)
