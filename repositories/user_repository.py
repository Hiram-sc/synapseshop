"""Repositório de usuários.

Isola o acesso à tabela de usuários: a camada de serviço (e as views) nunca
escrevem queries. Segue o mesmo desenho do repositório do microsserviço de
inventário (`inventory/app/storage.py`): a rota não conhece o ORM, o
repositório conhece.

A senha nunca trafega em texto puro: entra apenas em `create_user(s)`, que
chama `set_password`, e o Django grava apenas o hash com o hasher configurado
(PBKDF2 por padrão).
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from django.db import transaction
from django.utils import timezone

from repositories.models import Role, User


class UsernameAlreadyExists(Exception):
    """Levantada quando o `username` informado já está em uso.

    A unicidade é garantida pelo banco (`UNIQUE`); esta exceção existe para
    que o serviço e a API não precisem conhecer `IntegrityError`.
    """

    def __init__(self, username: str) -> None:
        super().__init__(f"Já existe um usuário com o username {username!r}.")
        self.username = username


class UserRepository:
    """Repositório transacional dos usuários da aplicação."""

    def __init__(self) -> None:
        self._model = User

    def get_by_username(self, username: str) -> Optional[User]:
        """Busca um usuário pelo username (índice único do banco)."""
        return self._model.objects.filter(username=username).first()

    def get_by_id(self, user_id: int) -> Optional[User]:
        """Busca um usuário pela chave primária."""
        return self._model.objects.filter(pk=user_id).first()

    def list_by_role(self, role: str) -> List[User]:
        """Lista os usuários de um papel, na ordem de username."""
        return list(self._model.objects.filter(role=role).order_by("username"))

    def create_user(self, username: str, password: str, role: str) -> User:
        """Cria um usuário e confirma a transação."""
        return self.create_users(
            [{"username": username, "password": password, "role": role}]
        )[0]

    def create_users(self, users: Sequence[dict[str, Any]]) -> List[User]:
        """Cria vários usuários dentro de uma única transação.

        Usado pelo comando de provisionamento: se um dos usuários falhar
        (username repetido, por exemplo), nenhum dos anteriores é mantido.
        """
        with transaction.atomic():
            created: List[User] = []
            for data in users:
                username = data["username"]
                if self._model.objects.filter(username=username).exists():
                    raise UsernameAlreadyExists(username)
                user = self._model(
                    username=username, role=data.get("role", Role.USER)
                )
                user.set_password(data["password"])
                user.save()
                created.append(user)
            return created

    def record_login(self, user: User) -> None:
        """Registra o instante do último acesso, de forma atômica."""
        now = timezone.now()
        with transaction.atomic():
            self._model.objects.filter(pk=user.pk).update(last_login=now)
            user.last_login = now
