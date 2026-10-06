"""Testes de autenticação (Aula 7, ETAPA 11).

Cobre o login e a validação do JWT: credencial válida, usuário inexistente,
senha inválida, conta desativada, token válido, token inválido, token
expirado, token de usuário removido e requisição sem token.
"""

from __future__ import annotations

from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from repositories.models import Role
from tests import SENHA_ADMIN, SENHA_USER, ApiTestCaseBase

LOGIN = "/api/v1/auth/login/"
ME = "/api/v1/auth/me/"
ITENS = "/api/v1/items/"


class TestLogin(ApiTestCaseBase):
    """`POST /api/v1/auth/login/`."""

    def test_01_login_valido_devolve_token_e_usuario(self) -> None:
        resposta = self.client.post(
            LOGIN, {"username": "admin", "password": SENHA_ADMIN}, format="json"
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.data["access"])
        self.assertEqual(resposta.data["token_type"], "Bearer")
        self.assertEqual(resposta.data["user"]["username"], "admin")
        self.assertEqual(resposta.data["user"]["role"], Role.ADMIN)
        # a senha jamais volta na resposta, nem como hash
        self.assertNotIn("password", resposta.data["user"])

    def test_02_expires_in_reflete_a_configuracao(self) -> None:
        resposta = self.client.post(
            LOGIN, {"username": "admin", "password": SENHA_ADMIN}, format="json"
        )
        esperado = int(
            settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()
        )
        self.assertEqual(resposta.data["expires_in"], esperado)

    def test_03_token_carrega_identidade_e_role(self) -> None:
        token = self.fazer_login("user", SENHA_USER)
        payload = jwt.decode(
            token,
            settings.SIMPLE_JWT["SIGNING_KEY"],
            algorithms=[settings.SIMPLE_JWT["ALGORITHM"]],
        )

        self.assertEqual(str(payload["user_id"]), str(self.usuario.pk))
        self.assertEqual(payload["username"], "user")
        self.assertEqual(payload["role"], Role.USER)
        self.assertIn("exp", payload)
        self.assertIn("iat", payload)

    def test_04_usuario_inexistente_responde_401(self) -> None:
        resposta = self.client.post(
            LOGIN, {"username": "nao-existe", "password": SENHA_ADMIN},
            format="json",
        )

        self.assertEqual(resposta.status_code, 401)
        self.assertEqual(resposta.data["detail"], "Usuário ou senha inválidos.")

    def test_05_senha_invalida_responde_401(self) -> None:
        resposta = self.client.post(
            LOGIN, {"username": "admin", "password": "senha-errada"}, format="json"
        )

        self.assertEqual(resposta.status_code, 401)
        self.assertEqual(resposta.data["detail"], "Usuário ou senha inválidos.")

    def test_06_erro_de_login_nao_distingue_usuario_de_senha(self) -> None:
        """Respostas idênticas evitam revelar quais usernames existem."""
        inexistente = self.client.post(
            LOGIN, {"username": "nao-existe", "password": "x"}, format="json"
        )
        senha_errada = self.client.post(
            LOGIN, {"username": "admin", "password": "x"}, format="json"
        )

        self.assertEqual(inexistente.status_code, senha_errada.status_code)
        self.assertEqual(inexistente.data, senha_errada.data)

    def test_07_conta_desativada_responde_403(self) -> None:
        self.usuario.is_active = False
        self.usuario.save(update_fields=["is_active"])

        resposta = self.client.post(
            LOGIN, {"username": "user", "password": SENHA_USER}, format="json"
        )

        self.assertEqual(resposta.status_code, 403)
        self.assertIn("desativada", resposta.data["detail"])

    def test_08_payload_incompleto_responde_400(self) -> None:
        resposta = self.client.post(LOGIN, {"username": "admin"}, format="json")
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("password", resposta.data)

    def test_09_login_registra_o_ultimo_acesso(self) -> None:
        self.assertIsNone(self.usuario.last_login)

        self.client.post(
            LOGIN, {"username": "user", "password": SENHA_USER}, format="json"
        )

        self.usuario.refresh_from_db()
        self.assertIsNotNone(self.usuario.last_login)

    def test_10_senha_e_gravada_como_hash(self) -> None:
        self.usuario.refresh_from_db()

        self.assertNotEqual(self.usuario.password, SENHA_USER)
        self.assertTrue(self.usuario.password.startswith("pbkdf2_sha256$"))
        self.assertTrue(self.usuario.check_password(SENHA_USER))


class TestValidacaoDoToken(ApiTestCaseBase):
    """Validação do JWT em rota protegida."""

    def test_01_token_valido_abre_rota_protegida(self) -> None:
        resposta = self.client.get(ME, **self.auth_de(self.usuario))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data["username"], "user")
        self.assertEqual(resposta.data["role"], Role.USER)

    def test_02_sem_token_responde_401(self) -> None:
        resposta = self.client.get(ME)
        self.assertEqual(resposta.status_code, 401)

    def test_03_header_authorization_vazio_responde_401(self) -> None:
        resposta = self.client.get(ME, HTTP_AUTHORIZATION="")
        self.assertEqual(resposta.status_code, 401)

    def test_04_scheme_diferente_de_bearer_responde_401(self) -> None:
        token = self.token_de(self.usuario)
        resposta = self.client.get(ME, HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(resposta.status_code, 401)

    def test_05_token_quebrado_responde_401(self) -> None:
        resposta = self.client.get(ME, HTTP_AUTHORIZATION="Bearer nao-e-um-jwt")
        self.assertEqual(resposta.status_code, 401)

    def test_06_token_assinado_com_outra_chave_responde_401(self) -> None:
        """A assinatura é verificada: forjar o payload não basta."""
        forjado = jwt.encode(
            {
                "user_id": self.admin.pk,
                "username": "admin",
                "role": Role.ADMIN,
                "exp": timezone.now() + timedelta(minutes=10),
            },
            "chave-que-nao-e-a-do-projeto",
            algorithm="HS256",
        )

        resposta = self.client.get(ME, HTTP_AUTHORIZATION=f"Bearer {forjado}")
        self.assertEqual(resposta.status_code, 401)

    def test_07_token_expirado_responde_401(self) -> None:
        expirado = AccessToken()
        expirado["user_id"] = self.usuario.pk
        expirado["role"] = self.usuario.role
        expirado["exp"] = timezone.now() - timedelta(minutes=1)

        resposta = self.client.get(ME, HTTP_AUTHORIZATION=f"Bearer {expirado}")
        self.assertEqual(resposta.status_code, 401)

    def test_08_token_de_usuario_removido_responde_401(self) -> None:
        """O token é conferido contra o banco a cada requisição."""
        token = self.token_de(self.usuario)
        self.usuario.delete()

        resposta = self.client.get(ME, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resposta.status_code, 401)

    def test_09_token_de_usuario_desativado_responde_401(self) -> None:
        token = self.token_de(self.usuario)
        self.usuario.is_active = False
        self.usuario.save(update_fields=["is_active"])

        resposta = self.client.get(ME, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resposta.status_code, 401)

    def test_10_token_sem_identificacao_de_usuario_responde_401(self) -> None:
        anonimo = AccessToken()
        anonimo["exp"] = timezone.now() + timedelta(minutes=10)

        resposta = self.client.get(ME, HTTP_AUTHORIZATION=f"Bearer {anonimo}")
        self.assertEqual(resposta.status_code, 401)

    def test_11_token_valido_continua_valido_apos_o_login(self) -> None:
        """`POST /auth/login/` não exige token: é a porta de entrada."""
        token = self.fazer_login("admin", SENHA_ADMIN)
        resposta = self.client.get(ME, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resposta.status_code, 200)
