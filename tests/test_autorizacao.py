"""Testes de autorização por role (Aula 7, ETAPA 11).

A separação que importa aqui é:

* **401 Unauthorized** - não sei quem é você (sem token, token inválido);
* **403 Forbidden** - sei quem é você, mas essa operação não é sua.

Nenhum caso pode trocar de status: um 403 em vez de 401 entrega informação
errada ao cliente, e um 401 em vez de 403 faz o cliente tentar relogar sem
motivo.
"""

from __future__ import annotations

from repositories.models import Role
from tests import ApiTestCaseBase

ITENS = "/api/v1/items/"
CATEGORIAS = "/api/v1/categories/"


class TestLeituraPublica(ApiTestCaseBase):
    """`GET` continua público: a vitrine não pede login."""

    def test_01_lista_de_itens_anonima(self) -> None:
        resposta = self.client.get(ITENS)
        self.assertEqual(resposta.status_code, 200)

    def test_02_detalhe_de_item_anonimo(self) -> None:
        resposta = self.client.get(f"{ITENS}{self.item_ativo.pk}/")
        self.assertEqual(resposta.status_code, 200)

    def test_03_lista_de_categorias_anonima(self) -> None:
        resposta = self.client.get(CATEGORIAS)
        self.assertEqual(resposta.status_code, 200)

    def test_04_leitura_com_token_de_usuario_tambem_funciona(self) -> None:
        resposta = self.client.get(ITENS, **self.auth_de(self.usuario))
        self.assertEqual(resposta.status_code, 200)

    def test_05_itens_inativos_aparecem_na_leitura_publica(self) -> None:
        """A vitrine lista tudo; quem filtra por `is_active` é o cliente."""
        nomes = [item["name"] for item in self.client.get(ITENS).data["results"]]
        self.assertIn("Teclado antigo", nomes)


class TestEscritaExigeAdmin(ApiTestCaseBase):
    """`POST`/`PUT`/`PATCH`/`DELETE` exigem `role admin`."""

    def _novo_item(self) -> dict:
        return {
            "name": "Mouse",
            "price": "129.90",
            "category": self.categoria.pk,
        }

    def test_01_user_recebe_403_em_post(self) -> None:
        resposta = self.client.post(
            ITENS, self._novo_item(), format="json", **self.auth_de(self.usuario)
        )
        self.assertEqual(resposta.status_code, 403)
        # a mensagem é a padrão do DRF: não diz qual papel faltou
        self.assertEqual(resposta.data["detail"].code, "permission_denied")

    def test_02_user_recebe_403_em_patch(self) -> None:
        resposta = self.client.patch(
            f"{ITENS}{self.item_ativo.pk}/",
            {"price": "1.00"},
            format="json",
            **self.auth_de(self.usuario),
        )
        self.assertEqual(resposta.status_code, 403)

    def test_03_user_recebe_403_em_delete(self) -> None:
        resposta = self.client.delete(
            f"{ITENS}{self.item_ativo.pk}/", **self.auth_de(self.usuario)
        )
        self.assertEqual(resposta.status_code, 403)

    def test_04_user_recebe_403_em_categorias(self) -> None:
        resposta = self.client.post(
            CATEGORIAS, {"name": "Games"}, format="json",
            **self.auth_de(self.usuario),
        )
        self.assertEqual(resposta.status_code, 403)

    def test_05_admin_cria_item(self) -> None:
        resposta = self.client.post(
            ITENS, self._novo_item(), format="json", **self.auth_de(self.admin)
        )
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(resposta.data["name"], "Mouse")

    def test_06_admin_atualiza_item(self) -> None:
        resposta = self.client.patch(
            f"{ITENS}{self.item_ativo.pk}/",
            {"price": "299.90"},
            format="json",
            **self.auth_de(self.admin),
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data["price"], "299.90")

    def test_07_admin_remove_item(self) -> None:
        resposta = self.client.delete(
            f"{ITENS}{self.item_ativo.pk}/", **self.auth_de(self.admin)
        )
        self.assertEqual(resposta.status_code, 204)

    def test_08_admin_cria_categoria(self) -> None:
        resposta = self.client.post(
            CATEGORIAS, {"name": "Games"}, format="json",
            **self.auth_de(self.admin),
        )
        self.assertEqual(resposta.status_code, 201)

    def test_09_sem_token_recebe_401_e_nao_403(self) -> None:
        """Sem identidade a resposta é 401, mesmo sendo uma escrita."""
        resposta = self.client.post(ITENS, self._novo_item(), format="json")
        self.assertEqual(resposta.status_code, 401)

    def test_10_token_invalido_recebe_401_e_nao_403(self) -> None:
        resposta = self.client.post(
            ITENS, self._novo_item(), format="json",
            HTTP_AUTHORIZATION="Bearer invalido",
        )
        self.assertEqual(resposta.status_code, 401)


class TestRoleDoBanco(ApiTestCaseBase):
    """A autorização lê a role atual do banco, não a do token."""

    def test_01_role_alterada_no_banco_vale_no_proximo_acesso(self) -> None:
        """Promover o usuário invalida o acesso de admin sem esperar expirar."""
        token = self.token_de(self.usuario)
        self.usuario.role = Role.ADMIN
        self.usuario.save(update_fields=["role"])

        resposta = self.client.post(
            ITENS,
            {"name": "Mouse", "price": "129.90", "category": self.categoria.pk},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resposta.status_code, 201)

    def test_02_rebaixamento_e_imediato(self) -> None:
        token = self.token_de(self.admin)
        self.admin.role = Role.USER
        self.admin.save(update_fields=["role"])

        resposta = self.client.post(
            ITENS,
            {"name": "Mouse", "price": "129.90", "category": self.categoria.pk},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resposta.status_code, 403)
