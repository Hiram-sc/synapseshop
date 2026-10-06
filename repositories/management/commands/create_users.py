"""Provisiona os usuários iniciais da API.

A API **não** expõe criação de contas: um endpoint público de cadastro seria
uma porta aberta, e um endpoint de criação restrito a `admin` só funcionaria
depois que já existisse o primeiro admin. O provisionamento fica, portanto,
nesta camada de infraestrutura, executada por quem tem acesso ao ambiente.

    python manage.py create_users --admin-password <senha> --user-password <senha>

Cria um administrador (`admin`) e um usuário comum (`user`), ambos dentro de
uma única transação: se alguma criação falhar, nenhuma é mantida. O comando é
idempotente - rodar de novo não duplica contas, apenas informa o que já existia.
"""

from __future__ import annotations

from typing import Any, Dict, List

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from repositories.models import Role
from repositories.user_repository import UsernameAlreadyExists
from services.user_service import InvalidRole, UserService


class Command(BaseCommand):
    help = "Cria (ou reaproveita) os usuários admin e user da aplicação."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--admin-password",
            required=True,
            help="Senha do usuário admin. Não há valor padrão de propósito.",
        )
        parser.add_argument(
            "--user-password",
            required=True,
            help="Senha do usuário user. Não há valor padrão de propósito.",
        )
        parser.add_argument(
            "--admin-username", default="admin", help="Username do admin."
        )
        parser.add_argument(
            "--user-username", default="user", help="Username do usuário comum."
        )

    def handle(self, *args: Any, **options: Dict[str, Any]) -> None:
        service = UserService()
        a_criar: List[dict] = [
            {
                "username": options["admin_username"],
                "password": options["admin_password"],
                "role": Role.ADMIN,
            },
            {
                "username": options["user_username"],
                "password": options["user_password"],
                "role": Role.USER,
            },
        ]

        try:
            criados, ja_existentes = service.create_users(a_criar)
        except (InvalidRole, UsernameAlreadyExists) as erro:
            # a transação do serviço já foi revertida aqui
            raise CommandError(str(erro)) from erro
        except IntegrityError as erro:
            raise CommandError(
                "Falha de integridade ao criar os usuários; nada foi gravado."
            ) from erro

        for user in criados:
            self.stdout.write(
                self.style.SUCCESS(f"usuário '{user.username}' criado (role={user.role})")
            )
        for user in ja_existentes:
            self.stdout.write(
                f"usuário '{user.username}' já existia (role={user.role}); mantido"
            )
