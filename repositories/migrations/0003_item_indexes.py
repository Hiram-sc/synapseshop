"""Cria os índices compostos da listagem do catálogo.

Motivo (Aula 7): a listagem de itens passou a aceitar os filtros
`?category=` e `?is_active=` **junto com** a ordenação por nome, que é a
ordenação padrão do modelo. Os dois índices abaixo existem para esse par de
operações; nada mais foi indexado.

| Índice                     | Consulta que sustenta                                    |
| -------------------------- | -------------------------------------------------------- |
| `ix_item_categoria_nome`   | `WHERE category_id = X ORDER BY name` (vitrine por categoria) |
| `ix_item_ativo_nome`       | `WHERE is_active = true ORDER BY name` (somente itens ativos) |

O índice composto com `category` também dispensa um índice separado na coluna
`category_id`, porque ele é a coluna mais à esquerda.

**O que não foi indexado, de propósito:** `name` não tem índice próprio porque
o filtro `?search=` usa `icontains`, que um índice B-tree não atende (precisaria
de `pg_trgm`, que é otimização de performance e entra junto com a aula de cache,
não agora); `price` e `created_at` só são ordenados, e ordenar por eles é uso
ocasional - índice especulativo é custo sem benefício.

Reversibilidade: o `downgrade` remove os índices, na ordem inversa do `upgrade`,
e não toca em nenhum dado.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("repositories", "0002_user"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="item",
            index=models.Index(
                fields=["category", "name"], name="ix_item_categoria_nome"
            ),
        ),
        migrations.AddIndex(
            model_name="item",
            index=models.Index(
                fields=["is_active", "name"], name="ix_item_ativo_nome"
            ),
        ),
    ]
