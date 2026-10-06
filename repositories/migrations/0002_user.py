"""Cria a tabela de usuários da API (`repositories_user`).

Motivo (Aula 7): a autenticação JWT precisa de um usuário com identidade,
senha com hash e um papel (`role`). A tabela não existia - o projeto ainda
não tinha `django.contrib.auth` instalado - então a migration **cria**, sem
alterar nem remover dados de `repositories_item` e `repositories_category`.

Decisões que a migration carrega:

* `role` entra com `default='user'`, o menor privilégio possível: se algum dia
  uma linha for criada sem role explícita (ou por um `INSERT` manual), ela
  nasce como usuário comum, nunca como administrador;
* `is_active` (herdado de `AbstractUser`) é o que permite desativar uma conta
  sem apagá-la; o login recusa contas inativas;
* a senha é gravada **sempre** como hash PBKDF2, porque `AbstractUser` passa
  por `set_password()` - nunca em texto puro.

Reversibilidade: o `downgrade` derruba a tabela `repositories_user` e as
relações com `auth_group`/`auth_permission`. Nenhuma tabela do catálogo é
tocada, então o rollback de `0001` para cá é seguro.
"""

import django.contrib.auth.models
import django.contrib.auth.validators
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # `auth` fornece as tabelas de grupos/permissões referenciadas abaixo
        ("auth", "0012_alter_user_first_name_max_length"),
        ("repositories", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="User",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("password", models.CharField(max_length=128, verbose_name="password")),
                (
                    "last_login",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="last login"
                    ),
                ),
                (
                    "is_superuser",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Designates that this user has all permissions "
                            "without explicitly assigning them."
                        ),
                        verbose_name="superuser status",
                    ),
                ),
                (
                    "username",
                    models.CharField(
                        error_messages={
                            "unique": "A user with that username already exists."
                        },
                        help_text=(
                            "Required. 150 characters or fewer. Letters, "
                            "digits and @/./+/-/_ only."
                        ),
                        max_length=150,
                        unique=True,
                        validators=[
                            django.contrib.auth.validators.UnicodeUsernameValidator()
                        ],
                        verbose_name="username",
                    ),
                ),
                (
                    "first_name",
                    models.CharField(
                        blank=True, max_length=150, verbose_name="first name"
                    ),
                ),
                (
                    "last_name",
                    models.CharField(
                        blank=True, max_length=150, verbose_name="last name"
                    ),
                ),
                (
                    "email",
                    models.EmailField(
                        blank=True, max_length=254, verbose_name="email address"
                    ),
                ),
                (
                    "is_staff",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Designates whether the user can log into this admin site."
                        ),
                        verbose_name="staff status",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text=(
                            "Designates whether this user should be treated as "
                            "active. Unselect this instead of deleting accounts."
                        ),
                        verbose_name="active",
                    ),
                ),
                (
                    "date_joined",
                    models.DateTimeField(
                        default=django.utils.timezone.now, verbose_name="date joined"
                    ),
                ),
                # a role é a autorização do domínio: `admin` escreve no catálogo,
                # `user` apenas lê. O padrão é `user` (menor privilégio).
                (
                    "role",
                    models.CharField(
                        choices=[("admin", "Administrador"), ("user", "Usuário")],
                        default="user",
                        max_length=20,
                    ),
                ),
            ],
            options={
                "verbose_name": "usuário",
                "verbose_name_plural": "usuários",
                "db_table": "repositories_user",
            },
            # `UserManager` traz `create_user`/`create_superuser`, que já
            # aplicam o hasher de senha
            managers=[
                ("objects", django.contrib.auth.models.UserManager()),
            ],
        ),
        migrations.AddField(
            model_name="user",
            name="groups",
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    "The groups this user belongs to. A user will get all "
                    "permissions granted to each of their groups."
                ),
                related_name="user_set",
                related_query_name="user",
                to="auth.group",
                verbose_name="groups",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="user_permissions",
            field=models.ManyToManyField(
                blank=True,
                help_text="Specific permissions for this user.",
                related_name="user_set",
                related_query_name="user",
                to="auth.permission",
                verbose_name="user permissions",
            ),
        ),
    ]
