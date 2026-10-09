"""Testes unitários dos utilitários de cache (`services.cache`)."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core.cache import cache

from services import cache as c


# ---------------------------------------------------------------------------
# normalização de parâmetros
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(None, ""), ("  Café ", "café"), (123, "123"), (0, "0")],
)
def test_texto(valor, esperado):
    assert c._texto(valor) == esperado


@pytest.mark.unit
@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(None, None), ("", None), ("True", "true"), (" FALSE ", "false")],
)
def test_logico(valor, esperado):
    assert c._logico(valor) == esperado


@pytest.mark.unit
@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("5", 5), (3, 3), ("abc", "abc"), (None, "None")],
)
def test_numero(valor, esperado):
    assert c._numero(valor) == esperado


# ---------------------------------------------------------------------------
# digest e chaves
# ---------------------------------------------------------------------------
def _digest(**extras):
    base = dict(
        page=1, limit=20, ordering=None, search=None, is_active=None, category=None
    )
    base.update(extras)
    return c.digest_listagem(**base)


@pytest.mark.unit
def test_digest_e_estavel_e_curto():
    digest = _digest()
    assert digest == _digest()
    assert len(digest) == 16
    int(digest, 16)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("a", "b"),
    [
        ({"search": "Café"}, {"search": " café "}),
        ({"ordering": None}, {"ordering": ""}),
        ({"page": "1"}, {"page": 1}),
        ({"is_active": None}, {"is_active": ""}),
    ],
)
def test_digest_normaliza_parametros_equivalentes(a, b):
    assert _digest(**a) == _digest(**b)


@pytest.mark.unit
def test_digest_distingue_parametros_diferentes():
    assert _digest(page=1) != _digest(page=2)


@pytest.mark.unit
def test_chaves_bem_formadas():
    assert c.chave_detalhe("5") == "catalogo:v1:item:detalhe:5"
    assert c.chave_pedido(10) == "pedidos:v1:pedido:10"
    assert c.chave_listagem("abc", 3) == "catalogo:v1:itens:lista:g3:abc"


@pytest.mark.unit
def test_chave_listagem_usa_geracao_atual():
    assert c.chave_listagem("abc") == "catalogo:v1:itens:lista:g0:abc"


@pytest.mark.unit
def test_geracao_atual_zero_quando_ausente():
    assert c.geracao_atual() == 0


@pytest.mark.unit
def test_geracao_atual_le_o_valor_gravado():
    cache.set(c.CHAVE_GERACAO_LISTA, 4)
    assert c.geracao_atual() == 4


@pytest.mark.unit
def test_geracao_atual_ignora_valor_invalido():
    cache.set(c.CHAVE_GERACAO_LISTA, "x")
    assert c.geracao_atual() == 0


# ---------------------------------------------------------------------------
# métricas
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_metricas_padrao():
    m = c.MetricasCache()
    assert m.hit_rate == 0.0
    assert m.tempo_medio_cache_ms == 0.0
    assert m.tempo_medio_total_ms == 0.0


@pytest.mark.unit
def test_metricas_agregam_estados_e_tempos():
    m = c.MetricasCache()
    m.registrar(c.HIT, 1.0, 2.0)
    m.registrar(c.MISS, 1.0, 3.0)
    m.registrar(c.BYPASS, 0.0, 4.0, erro=True)
    assert (m.hits, m.misses, m.bypass, m.erros) == (1, 1, 1, 1)
    assert m.lookups == 2
    assert m.hit_rate == 0.5
    assert m.tempo_medio_cache_ms == pytest.approx(1.0)
    assert m.tempo_medio_total_ms == pytest.approx(3.0)


@pytest.mark.unit
def test_metricas_bypass_sem_erro():
    m = c.MetricasCache()
    m.registrar(c.BYPASS, 0.0, 0.0)
    assert m.erros == 0


@pytest.mark.unit
def test_metricas_isoladas_por_endpoint_e_zeraveis():
    c._metricas_de("lista").registrar(c.HIT, 1.0, 1.0)
    c._metricas_de("detalhe").registrar(c.MISS, 1.0, 1.0)
    assert set(c.metricas()) == {"lista", "detalhe"}
    c.zerar_metricas()
    assert c.metricas() == {}


@pytest.mark.unit
def test_metricas_sao_copias():
    c._metricas_de("lista").registrar(c.HIT, 1.0, 1.0)
    copia = c.metricas()
    copia["lista"].hits = 99
    assert c.metricas()["lista"].hits == 1


# ---------------------------------------------------------------------------
# cache-aside: fail-open e estados (sem depender do Redis para as falhas)
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_obter_listagem_bypass_quando_desligado(monkeypatch):
    monkeypatch.setattr(settings, "CACHE_ENABLED", False)
    payload, estado = c.obter_listagem("d", lambda: {"count": 1, "results": []})
    assert estado == c.BYPASS
    assert payload["count"] == 1


@pytest.mark.unit
def test_obter_listagem_hit_nao_consulta_a_fonte(monkeypatch):
    monkeypatch.setattr(cache, "get", lambda chave: {"count": 1, "results": []})
    payload, estado = c.obter_listagem(
        "d", lambda: pytest.fail("não deveria consultar a fonte")
    )
    assert estado == c.HIT


@pytest.mark.unit
def test_obter_listagem_fail_open_quando_set_falha(monkeypatch):
    monkeypatch.setattr(cache, "get", lambda chave: None)
    monkeypatch.setattr(cache, "set", lambda *a, **k: None)
    payload, estado = c.obter_listagem("d", lambda: {"count": 2, "results": []})
    assert estado == c.BYPASS
    assert payload["count"] == 2
    assert c.metricas()["lista"].erros == 1


@pytest.mark.unit
def test_obter_detalhe_inexistente_nao_grava(monkeypatch):
    monkeypatch.setattr(cache, "get", lambda chave: None)
    monkeypatch.setattr(
        cache, "set", lambda *a, **k: pytest.fail("não deveria gravar")
    )
    payload, estado = c.obter_detalhe(1, lambda: None)
    assert payload is None
    assert estado == c.MISS


@pytest.mark.unit
def test_obter_pedido_fail_open(monkeypatch):
    monkeypatch.setattr(cache, "get", lambda chave: None)
    monkeypatch.setattr(cache, "set", lambda *a, **k: None)
    payload, estado = c.obter_pedido(1, lambda: {"id": 1})
    assert estado == c.BYPASS
    assert c.metricas()["pedido"].erros == 1


# ---------------------------------------------------------------------------
# invalidação e limpeza (Redis de teste, DB isolado)
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_invalidar_listagem_avanca_a_geracao():
    assert c.invalidar_listagem() == 1
    assert c.invalidar_listagem() == 2


@pytest.mark.unit
def test_invalidar_detalhe_e_pedido():
    cache.set(c.chave_detalhe(1), {"x": 1})
    cache.set(c.chave_pedido(1), {"x": 1})
    assert c.invalidar_detalhe(1) is True
    assert c.invalidar_detalhe(1) is False
    assert c.invalidar_pedido(1) is True


@pytest.mark.unit
def test_invalidar_detalhes_conta_as_chaves_removidas():
    cache.set(c.chave_detalhe(1), {"x": 1})
    cache.set(c.chave_detalhe(2), {"x": 2})
    assert c.invalidar_detalhes([1, 2, 3]) == 2


@pytest.mark.unit
def test_limpar_namespace_remove_o_catalogo():
    cache.set(c.chave_detalhe(1), {"x": 1})
    cache.set(c.CHAVE_GERACAO_LISTA, 2)
    assert c.limpar_namespace() >= 1


@pytest.mark.unit
def test_cliente_redis_disponivel():
    assert c.cliente_redis() is not None
