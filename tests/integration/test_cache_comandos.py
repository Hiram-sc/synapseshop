"""Testes de integração dos comandos de cache (`cache_stats`, `cache_benchmark`)."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.cache import cache
from django.core.management import call_command

from repositories.models import Category, Item
from services import cache as cache_service
from repositories.management.commands import cache_stats

pytestmark = pytest.mark.integration


def _popular_catalogo():
    categoria = Category.objects.create(name="Eletrônicos")
    item = Item.objects.create(name="Teclado", price="10.00", category=categoria)
    return item


def _popular_cache():
    cache_service.invalidar_listagem()
    cache.set(cache_service.chave_listagem("digest"), {"count": 0}, 60)
    cache.set(cache_service.chave_detalhe(1), {"id": 1}, 60)


# ---------------------------------------------------------------------------
# cache_stats - funções puras
# ---------------------------------------------------------------------------
def test_tipo_da_chave_classifica():
    prefixo = f"prefixo:1:"
    assert (
        cache_stats._tipo_da_chave(f"{prefixo}{cache_service.CHAVE_GERACAO_LISTA}")
        == "geracao"
    )
    assert (
        cache_stats._tipo_da_chave(f"{prefixo}{cache_service.DETALHE}:1") == "detalhe"
    )
    assert (
        cache_stats._tipo_da_chave(f"{prefixo}{cache_service.LISTA}:g0:abc")
        == "listagem"
    )
    assert cache_stats._tipo_da_chave("qualquer:outra") == "outros"


def test_decodificar_e_padrao():
    assert cache_stats.Command._decodificar(b"chave") == "chave"
    assert cache_stats.Command._decodificar("chave") == "chave"
    assert cache_service.NAMESPACE in cache_stats._padrao_das_chaves()


# ---------------------------------------------------------------------------
# cache_stats - comando
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_cache_stats_json():
    _popular_cache()
    out = StringIO()

    call_command("cache_stats", json=True, stdout=out)

    relatorio = json.loads(out.getvalue())
    assert relatorio["configuracao"]["ativo"] is True
    assert relatorio["redis"]["chaves_do_catalogo"] >= 1
    assert relatorio["redis"]["por_tipo"]["geracao"] >= 1


@pytest.mark.django_db
def test_cache_stats_texto():
    _popular_cache()
    cache_service._metricas_de("lista").registrar(cache_service.HIT, 1.0, 2.0)
    out = StringIO()

    call_command("cache_stats", stdout=out)

    assert "Configuração" in out.getvalue()
    assert "HIT" in out.getvalue()


@pytest.mark.django_db
def test_cache_stats_limpar_e_zerar():
    _popular_cache()
    out = StringIO()

    call_command("cache_stats", limpar=True, zerar=True, stdout=out)

    assert "removida" in out.getvalue()
    assert "zerados" in out.getvalue()


@pytest.mark.django_db
def test_cache_stats_redis_offline(monkeypatch):
    monkeypatch.setattr(cache_service, "cliente_redis", lambda: None)
    comando = cache_stats.Command()

    relatorio = comando.montar_relatorio()

    assert relatorio["configuracao"]["redis_online"] is False
    assert relatorio["redis"]["chaves_do_catalogo"] == 0

    out = StringIO()
    comando.stdout = out
    comando.imprimir(relatorio)
    assert "SEM CONEXÃO" in out.getvalue()


# ---------------------------------------------------------------------------
# cache_benchmark
# ---------------------------------------------------------------------------
def test_percentil():
    from repositories.management.commands.cache_benchmark import _percentil

    assert _percentil([1.0, 2.0, 3.0, 4.0], 95) in (3.0, 4.0)


def test_contexto_nulo():
    from repositories.management.commands.cache_benchmark import _nulo

    with _nulo() as valor:
        assert valor is None


@pytest.mark.django_db
def test_cache_benchmark_catalogo_vazio():
    out = StringIO()

    call_command("cache_benchmark", stdout=out)

    assert "catálogo está vazio" in out.getvalue()


@pytest.mark.django_db
def test_cache_benchmark_json():
    _popular_catalogo()
    out = StringIO()

    call_command("cache_benchmark", n=2, limit=2, json=True, stdout=out)

    resultado = json.loads(out.getvalue())
    assert "listagem_hit" in resultado
    assert resultado["listagem_hit"]["x_cache"] == {"HIT": 2}
    assert "passos" in resultado["ciclo_de_invalidação"]


@pytest.mark.django_db
def test_cache_benchmark_texto():
    _popular_catalogo()
    out = StringIO()

    call_command("cache_benchmark", n=2, limit=2, stdout=out)

    assert "Benchmark do cache" in out.getvalue()
    assert "Ganho do cache" in out.getvalue()
