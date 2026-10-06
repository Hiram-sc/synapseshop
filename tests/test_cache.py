"""Testes do cache-aside com Redis (Aula 8).

O Redis usado aqui é o de verdade, o mesmo do `docker-compose.yml`: a aula
mexe justamente em cliente de rede, TTL e invalidação, e um dublê em memória
não exercitaria nada disso. Os testes conversam com a API por HTTP (cliente de
teste do DRF) e conferem o efeito no Redis - o header `X-Cache` para o
comportamento, `cache.ttl()`/chaves para o estado.
"""

from __future__ import annotations

import copy

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext

from repositories.models import Category, Item
from services import cache as cache_service
from services import events
from tests import ApiTestCaseBase

LISTAGEM = "/api/v1/items/"


def detalhe(item_id: int) -> str:
    return f"/api/v1/items/{item_id}/"


def digest_de(url: str) -> str:
    """Recalcula o `digest` da listagem a partir de uma URL de teste.

    Duplicate a montagem da chave em vez de expor a query string da view: o
    teste continua descrevendo o contrato ("mesmos parâmetros, mesma chave") em vez de
    depender de um detalhe de implementação do digest.
    """
    parametros = {
        "page": url.split("page=")[1].split("&")[0] if "page=" in url else 1,
        "limit": 20,
        "ordering": None,
        "search": None,
        "is_active": None,
        "category": None,
    }
    return cache_service.digest_listagem(**parametros)


class MixinListagem:
    """Atalhos para as consultas de listagem mais usadas nos testes."""

    def consultar(self, url: str = LISTAGEM):
        return self.client.get(url)


class CacheDaListagemTests(MixinListagem, ApiTestCaseBase):
    def test_primeira_consulta_e_miss_e_a_segunda_e_hit(self):
        primeira = self.consultar()
        segunda = self.consultar()

        self.assertEqual(primeira.status_code, 200)
        self.assertEqual(primeira["X-Cache"], cache_service.MISS)
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(segunda["X-Cache"], cache_service.HIT)
        self.assertEqual(primeira.data, segunda.data)

    def test_cache_hit_nao_consulta_o_banco(self):
        """Evidência objetiva: no acerto, a requisição não toca no PostgreSQL.

        `X-Cache: HIT` sozinho mostra só que o Redis devolveu o payload. O que
        prova a economia de banco é a contagem de queries: no caminho de acerto
        nem `get_queryset` nem `preparar_pagina_cacheada` chegam ao ORM, já que
        a paginação reaproveita o `count` que veio do cache. As requisições são
        anônimas de propósito - com token, o `JWTAuthentication` buscaria o
        usuário no banco e a contagem deixaria de medir só o cache.
        """
        self.assertEqual(self.consultar()["X-Cache"], cache_service.MISS)

        with self.assertNumQueries(0):
            resposta = self.consultar()

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["X-Cache"], cache_service.HIT)

    def test_miss_consulta_o_banco_e_hit_nao_consulta(self):
        """O ciclo da spec: MISS -> banco -> cache -> resposta e, na segunda
        leitura, HIT -> cache -> resposta, sem nova consulta ao banco."""
        with CaptureQueriesContext(connection) as consultas:
            miss = self.consultar()

        self.assertEqual(miss["X-Cache"], cache_service.MISS)
        # sem isto, o `assertNumQueries(0)` abaixo não provaria nada: bastaria
        # um cache que nunca consultasse o banco em lugar nenhum
        self.assertGreater(len(consultas), 0)

        with self.assertNumQueries(0):
            hit = self.consultar()

        self.assertEqual(hit["X-Cache"], cache_service.HIT)
        # a resposta servida do cache é a mesma que a vinda do banco
        self.assertEqual(miss.data, hit.data)

    def test_payload_guardado_tem_count_e_results(self):
        self.consultar()

        guardado = cache.get(
            cache_service.chave_listagem(digest_de(LISTAGEM), geracao=0)
        )
        self.assertEqual(guardado["count"], Item.objects.count())
        self.assertEqual(len(guardado["results"]), Item.objects.count())
        # `next`/`previous` não são cacheados de propósito: são URL absoluta
        self.assertNotIn("next", guardado)
        self.assertNotIn("previous", guardado)

    def test_links_sao_remontados_a_cada_requisicao(self):
        primeiro = self.client.get(LISTAGEM + "?limit=1", HTTP_HOST="um.local")
        segundo = self.client.get(LISTAGEM + "?limit=1", HTTP_HOST="dois.local")

        self.assertEqual(primeiro["X-Cache"], cache_service.MISS)
        # mesmo payload no cache, mas o link aponta para quem pediu
        self.assertEqual(segundo["X-Cache"], cache_service.HIT)
        self.assertIn("um.local", primeiro.data["next"])
        self.assertIn("dois.local", segundo.data["next"])

    def test_ttl_da_listagem(self):
        self.consultar()

        chave = cache_service.chave_listagem(digest_de(LISTAGEM), geracao=0)
        self.assertLessEqual(cache.ttl(chave), settings.CACHE_TTL_LISTA)
        self.assertGreater(cache.ttl(chave), settings.CACHE_TTL_LISTA - 10)

    def test_filtros_distintos_geram_chaves_distintas(self):
        variacoes = [
            LISTAGEM,
            LISTAGEM + "?limit=2",
            LISTAGEM + "?is_active=true",
            LISTAGEM + "?ordering=-price",
            LISTAGEM + "?search=teclado",
            LISTAGEM + f"?category={self.categoria.id}",
        ]

        for url in variacoes:
            with self.subTest(url=url):
                # a segunda chamada de cada variação é HIT: cada uma tem chave
                # própria, e uma não está servindo a resposta da outra
                self.assertEqual(self.consultar(url)["X-Cache"], cache_service.MISS)
                self.assertEqual(self.consultar(url)["X-Cache"], cache_service.HIT)

    def test_filtros_continuam_corretos_apos_o_cache_aquecer(self):
        url = LISTAGEM + "?is_active=true&limit=2"
        self.assertEqual(self.consultar(url)["X-Cache"], cache_service.MISS)
        aquecida = self.consultar(url)
        self.assertEqual(aquecida["X-Cache"], cache_service.HIT)
        self.assertTrue(all(item["is_active"] for item in aquecida.data["results"]))

    def test_pagina_invalida_continua_404(self):
        for url in (LISTAGEM + "?page=abc", LISTAGEM + "?page=0", LISTAGEM + "?page=99"):
            with self.subTest(url=url):
                resposta = self.consultar(url)
                self.assertEqual(resposta.status_code, 404)
                # nem chegou a ser montada a resposta do cache: a página inválida
                # é rejeitada antes, pela paginação
                self.assertNotIn("X-Cache", resposta)

    def test_limite_acima_do_teto_usa_o_teto(self):
        # ?limit=500 e ?limit=100 são a mesma consulta: precisam da mesma chave
        self.consultar(LISTAGEM + "?limit=500")
        segunda = self.consultar(LISTAGEM + "?limit=100")
        self.assertEqual(segunda["X-Cache"], cache_service.HIT)
        self.assertEqual(len(segunda.data["results"]), Item.objects.count())


class CacheDoDetalheTests(ApiTestCaseBase):
    def test_primeira_consulta_e_miss_e_a_segunda_e_hit(self):
        url = detalhe(self.item_ativo.id)

        self.assertEqual(self.client.get(url)["X-Cache"], cache_service.MISS)
        self.assertEqual(self.client.get(url)["X-Cache"], cache_service.HIT)

    def test_cache_hit_nao_consulta_o_banco(self):
        """No detalhe, o acerto também resolve sem tocar no PostgreSQL.

        O `retrieve` monta a chave a partir do `id` da URL e devolve o payload
        do Redis antes de qualquer acesso ao ORM. A requisição é anônima para
        não contar o `SELECT` do usuário que o `JWTAuthentication` faria.
        """
        url = detalhe(self.item_ativo.id)
        self.assertEqual(self.client.get(url)["X-Cache"], cache_service.MISS)

        with self.assertNumQueries(0):
            resposta = self.client.get(url)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["X-Cache"], cache_service.HIT)
        self.assertEqual(resposta.data["id"], self.item_ativo.id)

    def test_ttl_do_detalhe(self):
        self.client.get(detalhe(self.item_ativo.id))

        chave = cache_service.chave_detalhe(self.item_ativo.id)
        self.assertLessEqual(cache.ttl(chave), settings.CACHE_TTL_DETALHE)
        self.assertGreater(cache.ttl(chave), settings.CACHE_TTL_DETALHE - 10)

    def test_item_inexistente_responde_404_sem_guardar_chave(self):
        inexistente = Item.objects.order_by("-id").first().id + 1000
        resposta = self.client.get(detalhe(inexistente))

        self.assertEqual(resposta.status_code, 404)
        self.assertIsNone(cache.get(cache_service.chave_detalhe(inexistente)))


class InvalidaçãoTests(ApiTestCaseBase):
    def test_criar_item_invalida_a_listagem(self):
        self.assertEqual(self.client.get(LISTAGEM)["X-Cache"], cache_service.MISS)

        criado = self.client.post(
            LISTAGEM,
            {
                "name": "Monitor 4K",
                "price": "1899.00",
                "category": self.categoria.id,
            },
            format="json",
            **self.auth_de(self.admin),
        )
        self.assertEqual(criado.status_code, 201, criado.data)

        depois = self.client.get(LISTAGEM)
        self.assertEqual(depois["X-Cache"], cache_service.MISS)
        self.assertEqual(depois.data["count"], Item.objects.count())

    def test_atualizar_item_invalida_o_detalhe(self):
        url = detalhe(self.item_ativo.id)
        self.assertEqual(self.client.get(url)["X-Cache"], cache_service.MISS)

        alterado = self.client.patch(
            url, {"price": "299.90"}, format="json", **self.auth_de(self.admin)
        )
        self.assertEqual(alterado.status_code, 200, alterado.data)

        depois = self.client.get(url)
        self.assertEqual(depois["X-Cache"], cache_service.MISS)
        self.assertEqual(depois.data["price"], "299.90")

    def test_remover_item_apaga_o_detalhe_cacheado(self):
        url = detalhe(self.livro.id)
        self.client.get(url)

        removido = self.client.delete(url, **self.auth_de(self.admin))
        self.assertEqual(removido.status_code, 204)

        self.assertIsNone(cache.get(cache_service.chave_detalhe(self.livro.id)))
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_geracao_avanca_a_cada_invalidação(self):
        inicio = cache_service.geracao_atual()

        self.client.post(
            LISTAGEM,
            {
                "name": "Teclado sem fio",
                "price": "199.90",
                "category": self.categoria.id,
            },
            format="json",
            **self.auth_de(self.admin),
        )

        self.assertEqual(cache_service.geracao_atual(), inicio + 1)

    def test_renomear_categoria_invalida_o_detalhe_dos_itens(self):
        url = detalhe(self.item_ativo.id)
        self.client.get(url)
        self.assertEqual(self.client.get(url)["X-Cache"], cache_service.HIT)

        self.client.patch(
            f"/api/v1/categories/{self.categoria.id}/",
            {"name": "Periféricos"},
            format="json",
            **self.auth_de(self.admin),
        )

        # o `category_name` do item mudou, então o detalhe antigo não pode servir
        depois = self.client.get(url)
        self.assertEqual(depois["X-Cache"], cache_service.MISS)
        self.assertEqual(depois.data["category_name"], "Periféricos")

    def test_apagar_categoria_invalida_o_detalhe_dos_itens_removidos(self):
        url = detalhe(self.item_ativo.id)
        self.client.get(url)

        removida = self.client.delete(
            f"/api/v1/categories/{self.categoria.id}/", **self.auth_de(self.admin)
        )
        self.assertEqual(removida.status_code, 204)

        self.assertIsNone(cache.get(cache_service.chave_detalhe(self.item_ativo.id)))
        self.assertEqual(self.client.get(url).status_code, 404)


class EventosTests(ApiTestCaseBase):
    def test_criar_item_publica_evento_com_o_id(self):
        recebidos = []
        self.assinar_evento(events.ITEM_CRIADO, recebidos.append)

        self.client.post(
            LISTAGEM,
            {"name": "Webcam", "price": "349.00", "category": self.categoria.id},
            format="json",
            **self.auth_de(self.admin),
        )

        self.assertEqual(len(recebidos), 1)
        item = Item.objects.get(name="Webcam")
        self.assertEqual(recebidos[0].dados["item_id"], item.id)

    def test_assinante_que_quebra_nao_derruba_a_escrita(self):
        def quebrado(evento):
            raise RuntimeError("falha proposital do assinante")

        self.assinar_evento(events.ITEM_CRIADO, quebrado)

        resposta = self.client.post(
            LISTAGEM,
            {"name": "Headset", "price": "449.00", "category": self.categoria.id},
            format="json",
            **self.auth_de(self.admin),
        )

        self.assertEqual(resposta.status_code, 201)
        self.assertTrue(Item.objects.filter(name="Headset").exists())


class CacheDesligadoTests(ApiTestCaseBase):
    def test_listagem_desligada_e_bypass_e_nao_grava_nada(self):
        with self.settings(CACHE_ENABLED=False):
            primeira = self.client.get(LISTAGEM)
            segunda = self.client.get(LISTAGEM)

        self.assertEqual(primeira.status_code, 200)
        self.assertEqual(primeira["X-Cache"], cache_service.BYPASS)
        self.assertEqual(segunda["X-Cache"], cache_service.BYPASS)
        self.assertIsNone(cache.get(cache_service.chave_listagem(digest_de(LISTAGEM), 0)))

    def test_detalhe_desligado_e_bypass(self):
        with self.settings(CACHE_ENABLED=False):
            resposta = self.client.get(detalhe(self.item_ativo.id))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["X-Cache"], cache_service.BYPASS)
        self.assertIsNone(cache.get(cache_service.chave_detalhe(self.item_ativo.id)))


class FailOpenTests(ApiTestCaseBase):
    """Com o Redis fora do ar, a API continua respondendo - e diz que não cacheou."""

    def sem_redis(self):
        """Aponta o cache default para uma porta onde ninguém escuta."""
        configuracao = copy.deepcopy(settings.CACHES)
        configuracao["default"]["LOCATION"] = "redis://localhost:6399/0"
        return self.settings(CACHES=configuracao)

    def test_listagem_responde_do_banco(self):
        with self.sem_redis():
            resposta = self.client.get(LISTAGEM)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["X-Cache"], cache_service.BYPASS)
        self.assertEqual(resposta.data["count"], Item.objects.count())

    def test_detalhe_responde_do_banco(self):
        with self.sem_redis():
            resposta = self.client.get(detalhe(self.item_ativo.id))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["X-Cache"], cache_service.BYPASS)
        self.assertEqual(resposta.data["id"], self.item_ativo.id)

    def test_escrita_nao_quebra(self):
        with self.sem_redis():
            resposta = self.client.post(
                LISTAGEM,
                {
                    "name": "Hub USB-C",
                    "price": "129.00",
                    "category": self.categoria.id,
                },
                format="json",
                **self.auth_de(self.admin),
            )

        self.assertEqual(resposta.status_code, 201)
        self.assertTrue(Item.objects.filter(name="Hub USB-C").exists())

    def test_erro_conta_como_erro_e_nao_como_miss(self):
        with self.sem_redis():
            self.client.get(LISTAGEM)

        metricas = cache_service.metricas()["lista"]
        self.assertEqual(metricas.erros, 1)
        self.assertEqual(metricas.misses, 0)
        self.assertEqual(metricas.bypass, 1)


class MetricasTests(ApiTestCaseBase):
    def test_hits_misses_e_hit_rate(self):
        self.client.get(LISTAGEM)  # MISS
        self.client.get(LISTAGEM)  # HIT
        self.client.get(LISTAGEM)  # HIT

        metricas = cache_service.metricas()["lista"]
        self.assertEqual(metricas.lookups, 3)
        self.assertEqual(metricas.misses, 1)
        self.assertEqual(metricas.hits, 2)
        self.assertAlmostEqual(metricas.hit_rate, 2 / 3)

    def test_metricas_sao_separadas_por_endpoint(self):
        self.client.get(LISTAGEM)
        self.client.get(detalhe(self.item_ativo.id))

        metricas = cache_service.metricas()
        self.assertEqual(metricas["lista"].misses, 1)
        self.assertEqual(metricas["detalhe"].misses, 1)
        self.assertEqual(metricas["lista"].hits, 0)


class DigestTests(SimpleTestCase):
    """O `digest` é o que impede uma listagem de servir a resposta da outra."""

    def assinatura(self, **kwargs):
        padrao = {
            "page": 1,
            "limit": 20,
            "ordering": None,
            "search": None,
            "is_active": None,
            "category": None,
        }
        padrao.update(kwargs)
        return cache_service.digest_listagem(**padrao)

    def test_parametros_iguais_dao_a_mesma_chave(self):
        self.assertEqual(self.assinatura(search="teclado"), self.assinatura(search="TECLADO"))
        self.assertEqual(self.assinatura(search=""), self.assinatura())
        self.assertEqual(self.assinatura(is_active="true"), self.assinatura(is_active="True"))

    def test_parametros_diferentes_dao_chaves_diferentes(self):
        base = self.assinatura()
        for variavel in (
            {"page": 2},
            {"limit": 50},
            {"ordering": "-price"},
            {"search": "teclado"},
            {"is_active": "false"},
            {"category": "3"},
        ):
            with self.subTest(variavel=variavel):
                self.assertNotEqual(base, self.assinatura(**variavel))

    def test_ordenacao_padrao_entra_no_digest(self):
        self.assertEqual(self.assinatura(), self.assinatura(ordering="name"))
        self.assertNotEqual(self.assinatura(), self.assinatura(ordering="price"))

    def test_pagina_invalida_nao_quebra_a_assinatura(self):
        # `?page=abc` é erro da paginação (404), não da assinatura de chave
        self.assertIsInstance(self.assinatura(page="abc"), str)


class EventosDoCacheTests(SimpleTestCase):
    def test_publicar_entrega_o_evento(self):
        recebidos = []
        self.addCleanup(events.cancelar, events.ITEM_ATUALIZADO, recebidos.append)
        events.inscrever(events.ITEM_ATUALIZADO, recebidos.append)

        events.publicar(events.ITEM_ATUALIZADO, item_id=7)

        self.assertEqual(len(recebidos), 1)
        self.assertEqual(recebidos[0].nome, events.ITEM_ATUALIZADO)
        self.assertEqual(recebidos[0].dados, {"item_id": 7})

    def test_publicar_sem_assinantes_nao_falha(self):
        events.publicar(events.ITEM_REMOVIDO, item_id=1)


class LimpezaDoNamespaceTests(ApiTestCaseBase):
    def test_limpar_namespace_remove_as_chaves_do_catalogo(self):
        self.client.get(LISTAGEM)
        self.client.get(detalhe(self.item_ativo.id))
        # chave de outra origem (throttling) não faz parte do namespace
        cache.set("throttle_anon_127.0.0.1", ["x"], 60)

        removidas = cache_service.limpar_namespace()

        self.assertGreaterEqual(removidas, 2)
        self.assertIsNone(cache.get(cache_service.chave_listagem(digest_de(LISTAGEM), 0)))
        self.assertIsNone(cache.get(cache_service.chave_detalhe(self.item_ativo.id)))
        self.assertIsNotNone(cache.get("throttle_anon_127.0.0.1"))


class CategoriaSemItensTests(ApiTestCaseBase):
    def test_apagar_categoria_vazia_publica_evento_sem_ids(self):
        vazia = Category.objects.create(name="Brinquedos")

        recebidos = []
        self.assinar_evento(events.CATEGORIA_REMOVIDA, recebidos.append)

        resposta = self.client.delete(
            f"/api/v1/categories/{vazia.id}/", **self.auth_de(self.admin)
        )

        self.assertEqual(resposta.status_code, 204)
        self.assertEqual(recebidos[0].dados["item_ids"], [])
