"""Testes de paginação e filtros (Aula 7, ETAPA 11).

Cobre primeira página, página seguinte, limite de registros e os filtros do
domínio (`search`, `is_active`, `category`, `ordering`), inclusive combinados.
"""

from __future__ import annotations

from decimal import Decimal

from repositories.models import Item
from tests import ApiTestCaseBase

ITENS = "/api/v1/items/"
CATEGORIAS = "/api/v1/categories/"


class TestPaginacao(ApiTestCaseBase):
    """`?page=` e `?limit=` nas listas."""

    def setUp(self) -> None:
        super().setUp()
        for numero in range(7):
            Item.objects.create(
                name=f"Item {numero:02d}",
                price=Decimal("10.00") + numero,
                category=self.categoria,
            )

    def test_01_primeira_pagina(self) -> None:
        resposta = self.client.get(f"{ITENS}?page=1&limit=3")

        self.assertEqual(resposta.status_code, 200)
        # 3 itens da base + 7 criados
        self.assertEqual(resposta.data["count"], 10)
        self.assertEqual(len(resposta.data["results"]), 3)
        self.assertIsNone(resposta.data["previous"])
        self.assertIsNotNone(resposta.data["next"])

    def test_02_pagina_seguinte(self) -> None:
        primeira = self.client.get(f"{ITENS}?page=1&limit=3").data
        segunda = self.client.get(f"{ITENS}?page=2&limit=3").data

        nomes = {item["name"] for item in primeira["results"]}
        nomes |= {item["name"] for item in segunda["results"]}
        self.assertEqual(len(nomes), 6)
        self.assertIsNotNone(segunda["previous"])

    def test_03_ultima_pagina(self) -> None:
        resposta = self.client.get(f"{ITENS}?page=4&limit=3")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.data["results"]), 1)
        self.assertIsNone(resposta.data["next"])

    def test_04_limite_padrao_e_de_20_registros(self) -> None:
        for numero in range(20):
            Item.objects.create(
                name=f"Extra {numero:02d}",
                price="1.00",
                category=self.categoria,
            )

        resposta = self.client.get(ITENS)

        # 3 itens da base + 7 criados em setUp + 20 extras
        self.assertEqual(resposta.data["count"], 30)
        self.assertEqual(len(resposta.data["results"]), 20)

    def test_05_limit_acima_do_teto_e_truncado(self) -> None:
        resposta = self.client.get(f"{ITENS}?limit=1000")
        # o teto é max_page_size=100; a base tem menos que isso
        self.assertEqual(resposta.status_code, 200)
        self.assertLessEqual(len(resposta.data["results"]), 100)

    def test_06_pagina_inexistente_responde_404(self) -> None:
        resposta = self.client.get(f"{ITENS}?page=999&limit=3")
        self.assertEqual(resposta.status_code, 404)

    def test_07_categorias_tambem_sao_paginadas(self) -> None:
        resposta = self.client.get(f"{CATEGORIAS}?limit=1")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data["count"], 2)
        self.assertEqual(len(resposta.data["results"]), 1)


class TestFiltros(ApiTestCaseBase):
    """Filtros e ordenação do catálogo."""

    def test_01_filtro_por_busca_textual(self) -> None:
        resposta = self.client.get(f"{ITENS}?search=Python")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            [item["name"] for item in resposta.data["results"]],
            ["Livro de Python"],
        )

    def test_02_busca_sem_resultado(self) -> None:
        resposta = self.client.get(f"{ITENS}?search=inexistente")
        self.assertEqual(resposta.data["count"], 0)
        self.assertEqual(resposta.data["results"], [])

    def test_03_filtro_por_categoria(self) -> None:
        resposta = self.client.get(f"{ITENS}?category={self.categoria.pk}")
        self.assertEqual(resposta.data["count"], 2)

    def test_04_filtro_por_ativo_e_inativo(self) -> None:
        ativos = self.client.get(f"{ITENS}?is_active=true")
        inativos = self.client.get(f"{ITENS}?is_active=false")

        self.assertEqual(ativos.data["count"], 2)
        self.assertEqual(inativos.data["count"], 1)
        self.assertEqual(inativos.data["results"][0]["name"], "Teclado antigo")

    def test_05_combinacao_de_filtros(self) -> None:
        """`category` + `is_active` é a consulta que motivou o índice composto."""
        resposta = self.client.get(
            f"{ITENS}?category={self.categoria.pk}&is_active=true"
        )
        self.assertEqual(resposta.data["count"], 1)
        self.assertEqual(resposta.data["results"][0]["name"], "Teclado mecânico")

    def test_06_combinacao_de_filtro_com_paginacao(self) -> None:
        Item.objects.create(
            name="Teclado compacto", price="199.90", category=self.categoria
        )

        resposta = self.client.get(
            f"{ITENS}?search=Teclado&is_active=true&limit=1"
        )
        self.assertEqual(resposta.data["count"], 2)
        self.assertEqual(len(resposta.data["results"]), 1)

    def test_07_ordenacao_por_campo_permitido(self) -> None:
        resposta = self.client.get(f"{ITENS}?ordering=-price")
        # a resposta traz `price` como string; comparar como Decimal, senão
        # "9.90" seria maior que "349.90"
        precos = [Decimal(item["price"]) for item in resposta.data["results"]]
        self.assertEqual(precos, sorted(precos, reverse=True))
        self.assertEqual(precos[0], Decimal("349.90"))

    def test_08_ordenacao_padrao_por_nome(self) -> None:
        nomes = [item["name"] for item in self.client.get(ITENS).data["results"]]
        self.assertEqual(nomes, sorted(nomes))

    def test_09_ordenacao_invalida_e_ignorada(self) -> None:
        """Campo fora da lista branca é descartado pelo DRF, sem quebrar a API."""
        resposta = self.client.get(f"{ITENS}?ordering=senha")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.data["count"], 3)

    def test_10_filtro_com_valor_invalido_responde_400(self) -> None:
        """`category` é inteiro: texto não é um id, e o cliente precisa saber."""
        resposta = self.client.get(f"{ITENS}?category=abc")
        self.assertEqual(resposta.status_code, 400)

    def test_11_filtros_de_categoria(self) -> None:
        resposta = self.client.get(f"{CATEGORIAS}?search=Livros")
        self.assertEqual(resposta.data["count"], 1)
        self.assertEqual(resposta.data["results"][0]["name"], "Livros")
