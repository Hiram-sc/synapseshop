"""Serviço de autenticação.

Concentra o fluxo de login: busca o usuário, valida a senha com o hasher do
Django, confere se a conta está ativa e emite o token JWT. Nenhuma regra de
negócio vive na view e nenhum acesso ao banco acontece fora do repositório.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rest_framework_simplejwt.tokens import AccessToken

from repositories.models import User
from repositories.user_repository import UserRepository


class InvalidCredentials(Exception):
    """Credenciais inválidas (usuário inexistente ou senha errada).

    A mesma exceção cobre os dois casos de propósito: responder
    "usuário não existe" para uns e "senha errada" para outros entrega uma
    enumerated-attack oracle ao atacante.
    """


class InactiveUser(Exception):
    """Usuário existe e a senha confere, mas a conta está desativada."""


@dataclass(frozen=True)
class AuthenticatedUser:
    """Resultado do login: o token e o usuário já validado."""

    user: User
    access_token: str


class AuthService:
    """Autentica credenciais e emite tokens de acesso."""

    def __init__(self, users: Optional[UserRepository] = None) -> None:
        self._users = users or UserRepository()

    def authenticate(self, username: str, password: str) -> User:
        """Valida as credenciais e devolve o usuário autenticado.

        Levanta `InvalidCredentials` ou `InactiveUser`; o login nunca expõe
        qual dos dois casos ocorreu ao cliente.
        """
        user = self._users.get_by_username(username)
        if user is None or not user.check_password(password):
            raise InvalidCredentials()
        if not user.is_active:
            raise InactiveUser()
        return user

    def login(self, username: str, password: str) -> AuthenticatedUser:
        """Autentica o usuário e emite o token de acesso."""
        user = self.authenticate(username, password)
        self._users.record_login(user)
        return AuthenticatedUser(user=user, access_token=self.issue_token(user))

    def issue_token(self, user: User) -> str:
        """Gera um JWT assinado contendo a identidade e a role do usuário."""
        token = AccessToken.for_user(user)
        # a role viaja no token para o cliente poder adaptá-la, mas a
        # autorização continua lendo o valor atual do banco (ver
        # `api/permissions.py`), que muda sem precisar esperar o token expirar
        token["role"] = user.role
        token["username"] = user.username
        return str(token)
