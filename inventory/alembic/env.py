"""Ambiente de migração do Alembic do microsserviço de inventário.

Importa a base declarativa e os modelos da aplicação para que o
`--autogenerate` consiga compará-los com o schema real do PostgreSQL. A URL
de conexão não fica no `alembic.ini`: é montada a partir das variáveis de
ambiente `POSTGRES_*`, as mesmas usadas pela aplicação.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# permite executar o Alembic a partir da raiz do microsserviço
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base, DatabaseSettings  # noqa: E402
from app.models import InventoryItemModel  # noqa: F401,E402  (registra a tabela)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# base declarativa que o Alembic usa para comparar modelos e banco
target_metadata = Base.metadata


def _eh_do_microsservico(
    obj, nome: str, tipo: str, refletido, comparado
) -> bool:
    """Filtro do `--autogenerate` que restringe o escopo deste microsserviço.

    O banco `synapseshop` é compartilhado com a API Django. Sem este filtro, o
    Alembic enxerga `repositories_item`, `repositories_category`,
    `django_migrations` e `django_content_type` como tabelas removidas e gera
    um `drop_table` que destruiria dados de outro serviço.
    """
    if tipo == "table":
        return nome in Base.metadata.tables
    if tipo in ("index", "unique_constraint"):
        return obj.table.name in Base.metadata.tables
    return True


def run_migrations_offline() -> None:
    """Gera o SQL das migrações sem abrir conexão com o banco."""
    context.configure(
        url=DatabaseSettings.from_env().url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_eh_do_microsservico,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Executa as migrações contra o PostgreSQL configurado no ambiente."""
    # migrações rodam uma única vez, então o pool do SQLAlchemy é dispensável
    connectable = create_engine(
        DatabaseSettings.from_env().url, poolclass=pool.NullPool
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=_eh_do_microsservico,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
