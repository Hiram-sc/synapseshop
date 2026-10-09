"""Testes unitários do serviço de autenticação (`services.auth_service`)."""

from __future__ import annotations

import pytest
from rest_framework_simplejwt.tokens import AccessToken

from services.auth_service import (
    AuthService,
    InactiveUser,
    InvalidCredentials,
)


class UsuarioFake:
    def __init__(
        self, username="user", senha="senha", is_active=True, role="user", pk=1
    ):
        self.id = pk
        self.pk = pk
        self.username = username
        self.is_active = is_active
        self.role = role
        self._senha = senha

    def check_password(self, raw):
        return raw == self._senha


class RepoUsuariosFake:
    def __init__(self, user=None):
        self.user = user
        self.logins = []

    def get_by_username(self, username):
        if self.user is not None and self.user.username == username:
            return self.user
        return None

    def record_login(self, user):
        self.logins.append(user)


@pytest.mark.unit
def test_authenticate_usuario_inexistente():
    repo = RepoUsuariosFake(user=None)
    with pytest.raises(InvalidCredentials):
        AuthService(users=repo).authenticate("ninguem", "senha")


@pytest.mark.unit
def test_authenticate_senha_errada():
    repo = RepoUsuariosFake(UsuarioFake())
    with pytest.raises(InvalidCredentials):
        AuthService(users=repo).authenticate("user", "errada")


@pytest.mark.unit
def test_authenticate_conta_inativa():
    repo = RepoUsuariosFake(UsuarioFake(is_active=False))
    with pytest.raises(InactiveUser):
        AuthService(users=repo).authenticate("user", "senha")


@pytest.mark.unit
def test_authenticate_ok():
    usuario = UsuarioFake()
    repo = RepoUsuariosFake(usuario)
    assert AuthService(users=repo).authenticate("user", "senha") is usuario


@pytest.mark.unit
def test_login_registra_acesso_e_emite_token():
    usuario = UsuarioFake(role="admin")
    repo = RepoUsuariosFake(usuario)
    autenticado = AuthService(users=repo).login("user", "senha")

    assert autenticado.user is usuario
    assert repo.logins == [usuario]
    token = AccessToken(autenticado.access_token)
    assert token["role"] == "admin"
    assert token["username"] == "user"


@pytest.mark.unit
def test_issue_token_carrega_identidade():
    usuario = UsuarioFake(username="ana", role="admin", pk=42)
    token = AccessToken(AuthService(users=RepoUsuariosFake()).issue_token(usuario))
    assert str(token["user_id"]) == "42"
    assert token["username"] == "ana"
