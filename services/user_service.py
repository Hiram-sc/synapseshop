"""Serviço de usuários.

Responsável pelo provisionamento de contas (usado pelo comando de bootstrap
e pelos testes). O provisionamento é atômico: um lote de usuários só é
confirmado por inteiro.
"""

from __future__ import annotations

from typing import List, Optional

from repositories.models import Role, User
from repositories.user_repository import UserRepository


class InvalidRole(Exception):
    """Papel informado não existe em `Role`."""

    def __init__(self, role: str) -> None:
        super().__init__(
            f"Role inválida: {role!r}. Use {', '.join(Role.values)}."
        )
        self.role = role


class UserService:
    """Regras de negócio do provisionamento de usuários."""

    def __init__(self, users: Optional[UserRepository] = None) -> None:
        self._users = users or UserRepository()

    def get_by_username(self, username: str) -> Optional[User]:
        """Devolve o usuário do username, ou `None` se não existir."""
        return self._users.get_by_username(username)

    def create_users(
        self, users: List[dict[str, str]]
    ) -> tuple[List[User], List[User]]:
        """Cria (ou reaproveita) um lote de usuários dentro de uma transação.

        Usuários que já existem são devolvidos como `criados` = `False`, o que
        torna o comando de provisionamento repetível sem duplicar contas.
        """
        self._validate_roles([u.get("role", Role.USER) for u in users])
        a_criar, ja_existentes = self._split_existing(users)
        criados = self._users.create_users(a_criar) if a_criar else []
        return criados, ja_existentes

    def _validate_roles(self, roles: List[str]) -> None:
        for role in roles:
            if role not in Role.values:
                raise InvalidRole(role)

    def _split_existing(
        self, users: List[dict[str, str]]
    ) -> tuple[List[dict[str, str]], List[User]]:
        """Separa o que precisa ser inserido do que já está no banco."""
        a_criar: List[dict[str, str]] = []
        ja_existentes: List[User] = []
        for data in users:
            existente = self._users.get_by_username(data["username"])
            if existente is None:
                a_criar.append(data)
            else:
                ja_existentes.append(existente)
        return a_criar, ja_existentes
