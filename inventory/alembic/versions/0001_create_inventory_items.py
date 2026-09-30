"""cria a tabela `inventory_items`

Revision ID: 0001
Revises:
Create Date: 2026-09-30

Revisada manualmente após o `--autogenerate`: o arquivo foi reescrito no padrão
do projeto (aspas duplas, docstrings em português, revisão `0001`) e o bloco
`upgrade` foi conferido campo a campo contra `app/models.py`. O `downgrade`
remove o índice antes da tabela, que é a ordem inversa do `upgrade`.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Cria a tabela de itens de estoque com restrições e índice."""
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sku", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity >= 0", name="ck_inventory_items_quantity_non_negative"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku", name="uq_inventory_items_sku"),
    )
    op.create_index(
        "ix_inventory_items_created_at", "inventory_items", ["created_at"], unique=False
    )


def downgrade() -> None:
    """Desfaz a criação da tabela de itens de estoque."""
    op.drop_index("ix_inventory_items_created_at", table_name="inventory_items")
    op.drop_table("inventory_items")
