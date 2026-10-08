"""Cache-aside do catálogo e dos pedidos com Redis.

Este módulo é a única porta de entrada para o cache da aplicação: nenhuma view
fala com o `django.core.cache` diretamente. Isso permite responder a três
perguntas que a aula pede, e que só têm resposta se o acesso estiver centralizado:

1. **Qual chave guarda o quê?** Nomes versionados (`catalogo:v1:...`,
   `pedidos:v1:...`) e um `digest` dos parâmetros no lugar de uma lista de
   chaves por combinação.
2. **O que acontece quando o Redis cai?** Nada: o cache é acessório. A camada
   engole a falha (fail-open), marca a métrica de erro e devolve
   `X-Cache: BYPASS`, com os dados vindos do PostgreSQL.
3. **Como se mede o ganho?** Contadores em memória por endpoint (`hits`,
   `misses`, `bypass`, `erros`) e o tempo gasto dentro do cache, lidos pelo
   comando `cache_stats`.

Três decisões de projeto explicam o desenho:

* **Cache-aside, não write-through.** Quem escreve não atualiza o cache; quem
  lê preenche (`obter_listagem`/`obter_detalhe`/`obter_pedido`). Assim existe
  um único caminho de escrita no banco e nenhum risco de o cache "esquecer"
  uma alteração.
* **Invalidação da listagem por geração.** A listagem combina paginação,
  busca, ordenação, filtro de ativo e filtro de categoria: são tantas variantes
  que apagar todas de uma vez (KEYS/SCAN) exigiria varrer o Redis a cada
  escrita. Em vez disso, todas as chaves da listagem carregam um número de
  geração (`g0`, `g1`, ...) e uma invalidação é um único `INCR`. As chaves da
  geração antiga ficam órfãs e morrem sozinhas pelo TTL.
* **Nada de cache de 404.** Um item inexistente não é gravado: o `404` continua
  dependendo do banco, e o TTL segue sendo apenas a rede de proteção para o caso
  de alguma invalidação se perder. O pedido segue a mesma regra - e quem decide
  o 404 do pedido é o dono: o HIT só é servido a admin ou ao próprio dono
  (a checagem é na view, a partir do payload cacheado).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from django.conf import settings
from django.core.cache import cache
from rest_framework.renderers import JSONRenderer

logger = logging.getLogger(__name__)

# --- estados expostos no header `X-Cache` ---------------------------------
HIT = "HIT"
MISS = "MISS"
BYPASS = "BYPASS"

# --- espaço de chaves ------------------------------------------------------
# O prefixo `v1` versiona o *formato* do payload: se um dia o JSON da listagem
# mudar de shape, basta subir para `v2` e as chaves antigas deixam de ser lidas
# (sem precisar apagá-las).
LISTA = "catalogo:v1:itens:lista"
DETALHE = "catalogo:v1:item:detalhe"
CHAVE_GERACAO_LISTA = "catalogo:v1:itens:lista:geracao"
#: tudo que a aula 8 escreve no Redis (listagem, detalhe e a geração)
NAMESPACE = "catalogo:v1:*"

# Chave do pedido (Aula 11). Namespace próprio porque o pedido não é catálogo:
# o payload é outro, o TTL é outro e a invalidação é por chave, sempre que um
# processo (API ou worker) altera o status do pedido.
PEDIDO = "pedidos:v1:pedido"
NAMESPACE_PEDIDO = "pedidos:v1:*"

# ordenação usada quando o cliente não manda `?ordering=`; precisa acompanhar o
# `Meta.ordering` de `repositories.models.Item`, porque é dela que o DRF tira o
# padrão do `OrderingFilter`
ORDENACAO_PADRAO = "name"

# intervalo (em segundos) entre dois logs da mesma falha de cache
INTERVALO_LOG_FALHA_S = 30.0

_renderizador = JSONRenderer()

_lock = threading.Lock()
_metricas: Dict[str, MetricasCache] = {}
_ultimo_log: Dict[str, float] = {}


# ---------------------------------------------------------------------------
# chaves
# ---------------------------------------------------------------------------
def _texto(valor: Optional[Any]) -> str:
    """Normaliza um parâmetro de texto para o cálculo do `digest`.

    `?search=` vazio e a ausência de `?search=` produzem a mesma consulta, então
    precisam cair na mesma chave. A busca do DRF é insensível a maiúsculas
    (`icontains`), por isso `?search=Café` e `?search=café` também podem
    compartilhar a chave.
    """
    if valor is None:
        return ""
    return str(valor).strip().lower()


def _logico(valor: Optional[Any]) -> Optional[str]:
    """Normaliza `?is_active=` para `"true"`/`"false"`/ausente.

    A normalização não precisa cobrir toda a gramática do `django-filter`; ela
    só evita que a mesma consulta apareça em chaves diferentes.
    """
    if valor is None or valor == "":
        return None
    return str(valor).strip().lower()


def _numero(valor: Any) -> Any:
    """Converte para `int` quando dá, e devolve o valor cru quando não dá.

    `?page=abc` não é um erro que a assinatura do `digest` precise detectar: quem
    responde 404 é a paginação. Se a conversão explodisse aqui, um parâmetro
    inválido viraria 500 em vez do 404 de sempre - uma regressão causada
    justamente pelo cache.
    """
    try:
        return int(valor)
    except (TypeError, ValueError):
        return str(valor)


def digest_listagem(
    *,
    page: Any,
    limit: Any,
    ordering: Optional[str],
    search: Optional[str],
    is_active: Optional[Any],
    category: Optional[Any],
) -> str:
    """Assina os parâmetros da listagem e devolve um resumo curto e estável.

    A chave final é `catalogo:v1:itens:lista:g{geracao}:{digest}`. O `digest` é
    o SHA-1 (16 caracteres) do JSON canônico dos parâmetros - com as chaves
    ordenadas e sem espaços -, o que garante que `?page=1&limit=20` e
    `?limit=20&page=1` caiam na mesma chave.
    """
    parametros = {
        "category": _texto(category),
        "is_active": _logico(is_active),
        "limit": _numero(limit),
        "ordering": _texto(ordering) or ORDENACAO_PADRAO,
        "page": _numero(page),
        "search": _texto(search),
    }
    bruto = json.dumps(parametros, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()[:16]


def chave_detalhe(item_id: Any) -> str:
    """Chave do detalhe de um item: `catalogo:v1:item:detalhe:{id}`."""
    return f"{DETALHE}:{int(item_id)}"


def chave_pedido(pedido_id: Any) -> str:
    """Chave do pedido: `pedidos:v1:pedido:{id}` - endereçada pelo id."""
    return f"{PEDIDO}:{int(pedido_id)}"


def chave_listagem(digest: str, geracao: Optional[int] = None) -> str:
    """Chave da listagem; sem `geracao`, usa a geração corrente do Redis."""
    if geracao is None:
        geracao = geracao_atual()
    return f"{LISTA}:g{geracao}:{digest}"


def geracao_atual() -> int:
    """Lê a geração corrente da listagem.

    `0` é uma geração legítima (nenhuma invalidação aconteceu ainda) e também é
    a resposta quando a chave não existe. Um Redis fora do ar produz o mesmo
    `None`, e a ambiguidade é resolvida adiante pelo resultado do `SET`: o
    cache-aside só rotula `MISS` quando a gravação no cache funcionou de fato.
    """
    valor = cache.get(CHAVE_GERACAO_LISTA)
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# métricas
# ---------------------------------------------------------------------------
@dataclass
class MetricasCache:
    """Contadores do endpoint, mantidos na memória do processo.

    Eles não vão para o Redis de propósito: são estado de observação, não
    estado de aplicação. Em troca, um `cache_stats` mostra o que *esta
    instância* atendeu, e o custo em requisição é o de um `dict.update` sob
    `Lock`.
    """

    lookups: int = 0
    hits: int = 0
    misses: int = 0
    bypass: int = 0
    erros: int = 0
    tempo_cache_ms: float = 0.0
    tempo_total_ms: float = 0.0

    @property
    def hit_rate(self) -> float:
        """Proporção de acertos entre as consultas que chegaram ao cache.

        `BYPASS` fica fora do denominador de propósito: uma resposta servida
        porque o cache estava desligado (ou fora do ar) não é acerto nem erro do
        cache, e incluí-la aqui falsearia o número que a aula quer medir.
        """
        consultas = self.hits + self.misses
        return (self.hits / consultas) if consultas else 0.0

    @property
    def tempo_medio_cache_ms(self) -> float:
        return (self.tempo_cache_ms / self.lookups) if self.lookups else 0.0

    @property
    def tempo_medio_total_ms(self) -> float:
        base = self.hits + self.misses + self.bypass
        return (self.tempo_total_ms / base) if base else 0.0

    def registrar(
        self, estado: str, cache_ms: float, total_ms: float, erro: bool = False
    ) -> None:
        if estado == HIT:
            self.lookups += 1
            self.hits += 1
        elif estado == MISS:
            self.lookups += 1
            self.misses += 1
        else:
            self.bypass += 1
            if erro:
                self.erros += 1
        self.tempo_cache_ms += cache_ms
        self.tempo_total_ms += total_ms


def metricas() -> Dict[str, MetricasCache]:
    """Cópia das métricas por endpoint (`lista`, `detalhe`, `pedido`)."""
    with _lock:
        return {nome: MetricasCache(**vars(m)) for nome, m in _metricas.items()}


def zerar_metricas() -> None:
    """Zera os contadores. Usado por `cache_stats --zerar` e pelos testes."""
    with _lock:
        _metricas.clear()
        _ultimo_log.clear()


def _metricas_de(endpoint: str) -> MetricasCache:
    with _lock:
        return _metricas.setdefault(endpoint, MetricasCache())


def _log_falha(contexto: str, erro: Optional[BaseException]) -> None:
    """Avisa que o Redis não respondeu, sem inundar o log.

    Com `IGNORE_EXCEPTIONS`, cada operação que falha vira `None` em silêncio. Se
    cada uma também fosse registrada, um Redis fora do ar geraria uma linha de
    log por requisição - ruído que esconde a informação útil. A regra é
    simples: primeira falha agora, as demais a cada 30 segundos por contexto.
    """
    agora = time.monotonic()
    with _lock:
        ultima = _ultimo_log.get(contexto, 0.0)
        if agora - ultima < INTERVALO_LOG_FALHA_S:
            return
        _ultimo_log[contexto] = agora
    logger.warning(
        "Cache indisponível em %s (%s): seguindo para o banco (fail-open).",
        contexto,
        erro or "Redis não respondeu",
    )


# ---------------------------------------------------------------------------
# cache-aside
# ---------------------------------------------------------------------------
def _normalizado(dado: Any) -> Any:
    """Converte a saída do serializer no JSON exato que iria na resposta.

    Guardar `Decimal`, `datetime` ou `ReturnDict` no cache funciona, mas amarra
    o dado ao Python. Renderizar antes deixa o payload autocontido (e legível
    com `redis-cli`) e faz o cache devolver exatamente o que o cliente veria.
    """
    return json.loads(_renderizador.render(dado))


def _cache_ativo() -> bool:
    return bool(getattr(settings, "CACHE_ENABLED", True))


def obter_listagem(
    digest: str, calcular: Callable[[], Dict[str, Any]]
) -> Tuple[Dict[str, Any], str]:
    """Cache-aside da listagem paginada.

    `calcular` é quem consulta o PostgreSQL e devolve `{"count": ...,
    "results": [...]}`. Ela só é chamada em `MISS` (ou `BYPASS`), e por isso
    pode ter o custo que tiver - no `HIT` a função devolve um dicionário já
    pronto, sem tocar no banco.
    """
    inicio = time.perf_counter()
    if not _cache_ativo():
        payload = calcular()
        estado = BYPASS
        cache_ms = 0.0
        erro = False
    else:
        inicio_cache = time.perf_counter()
        chave = chave_listagem(digest)
        payload = cache.get(chave)
        cache_ms = (time.perf_counter() - inicio_cache) * 1000
        if payload is not None:
            estado = HIT
            erro = False
        else:
            payload = _normalizado(calcular())
            inicio_cache = time.perf_counter()
            gravado = cache.set(chave, payload, settings.CACHE_TTL_LISTA)
            cache_ms += (time.perf_counter() - inicio_cache) * 1000
            if gravado is not True:
                # O Redis não respondeu: o `set` engoliu a exceção e devolveu
                # `None`. Os dados acima estão certos, só não há cache - e dizer
                # `MISS` seria mentir sobre a saúde da infraestrutura.
                _log_falha("listagem", None)
                estado = BYPASS
                erro = True
            else:
                estado = MISS
                erro = False

    _metricas_de("lista").registrar(
        estado, cache_ms, (time.perf_counter() - inicio) * 1000, erro
    )
    return payload, estado


def obter_detalhe(
    item_id: Any, calcular: Callable[[], Optional[Dict[str, Any]]]
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Cache-aside do detalhe de um item.

    `calcular` devolve `None` quando o item não existe; nesse caso nada é
    gravado e o `404` continua sendo decidido pelo banco. Como o detalhe é
    endereçado pelo id, ele não depende de geração: uma invalidação é um `DEL`
    na chave conhecida.
    """
    inicio = time.perf_counter()
    if not _cache_ativo():
        payload = calcular()
        estado = BYPASS
        cache_ms = 0.0
        erro = False
    else:
        chave = chave_detalhe(item_id)
        inicio_cache = time.perf_counter()
        payload = cache.get(chave)
        cache_ms = (time.perf_counter() - inicio_cache) * 1000
        if payload is not None:
            estado = HIT
            erro = False
        else:
            payload = calcular()
            if payload is None:
                _metricas_de("detalhe").registrar(
                    MISS, cache_ms, (time.perf_counter() - inicio) * 1000
                )
                return None, MISS
            payload = _normalizado(payload)
            inicio_cache = time.perf_counter()
            gravado = cache.set(chave, payload, settings.CACHE_TTL_DETALHE)
            cache_ms += (time.perf_counter() - inicio_cache) * 1000
            if gravado is not True:
                _log_falha("detalhe", None)
                estado = BYPASS
                erro = True
            else:
                estado = MISS
                erro = False

    _metricas_de("detalhe").registrar(
        estado, cache_ms, (time.perf_counter() - inicio) * 1000, erro
    )
    return payload, estado


def obter_pedido(
    pedido_id: Any, calcular: Callable[[], Optional[Dict[str, Any]]]
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Cache-aside do pedido (`GET /api/v1/pedidos/{id}/`).

    Mesmo molde do `obter_detalhe`: `calcular` devolve `None` quando o pedido
    não existe **ou** o usuário não pode vê-lo (query escopada no dono/admin) -
    nesses casos nada é gravado e o 404 segue decidido pelo banco. Quem chama
    (a view) ainda confere o dono no caminho do HIT, porque o payload cacheado
    pode ter sido gravado por outro usuário.

    TTL de `CACHE_TTL_PEDIDO`; invalidação por chave (`invalidar_pedido`) em
    toda escrita que muda o pedido - vinda da API ou do worker, com o Redis
    compartilhado ligando os processos.
    """
    inicio = time.perf_counter()
    if not _cache_ativo():
        payload = calcular()
        estado = BYPASS
        cache_ms = 0.0
        erro = False
    else:
        chave = chave_pedido(pedido_id)
        inicio_cache = time.perf_counter()
        payload = cache.get(chave)
        cache_ms = (time.perf_counter() - inicio_cache) * 1000
        if payload is not None:
            estado = HIT
            erro = False
        else:
            payload = calcular()
            if payload is None:
                _metricas_de("pedido").registrar(
                    MISS, cache_ms, (time.perf_counter() - inicio) * 1000
                )
                return None, MISS
            payload = _normalizado(payload)
            inicio_cache = time.perf_counter()
            gravado = cache.set(chave, payload, settings.CACHE_TTL_PEDIDO)
            cache_ms += (time.perf_counter() - inicio_cache) * 1000
            if gravado is not True:
                _log_falha("pedido", None)
                estado = BYPASS
                erro = True
            else:
                estado = MISS
                erro = False

    _metricas_de("pedido").registrar(
        estado, cache_ms, (time.perf_counter() - inicio) * 1000, erro
    )
    return payload, estado


# ---------------------------------------------------------------------------
# invalidação
# ---------------------------------------------------------------------------
def invalidar_listagem() -> Optional[int]:
    """Invalida **todas** as listagens com um único `INCR`.

    Devolve a nova geração, ou `None` se o Redis não respondeu - nesse caso os
    dados velhos sobrevivem até o TTL de 60s expirar, degradação aceitável e
    preferível a derrubar a escrita que o usuário acabou de fazer.

    `INCR` e não `GET` + `SET` porque é atômico: duas escritas simultâneas não
    podem ler a mesma geração e sobrescrever uma a outra. E o valor precisa ser
    um inteiro cru no Redis, então esta chave nunca pode receber um payload
    serializado - só números.
    """
    try:
        nova = cache.incr(CHAVE_GERACAO_LISTA)
    except ValueError:
        # Primeira invalidação desde que o cache foi limpo: `INCR` exige que a
        # chave já exista. `add` é atômico (é um `SETNX`), então duas requisições
        # simultâneas não correm - apenas uma cria a chave, e a outra incrementa
        # a partir dela. Custa uma ida extra ao Redis uma única vez.
        cache.add(CHAVE_GERACAO_LISTA, 0, timeout=None)
        nova = cache.incr(CHAVE_GERACAO_LISTA)
    if nova is None:
        _log_falha("invalidação da listagem", None)
        return None
    logger.debug("Listagem invalidada: geração %s.", nova)
    return int(nova)


def invalidar_detalhe(item_id: Any) -> bool:
    """Apaga a chave do detalhe de um item. `True` se a chave existia.

    O retorno não serve para detectar Redis fora do ar: `DEL` responde `False`
    tanto para "chave não existia" quanto para "não consegui falar com o Redis".
    A saúde do cache é observada em `invalidar_listagem` (que sempre roda na
    mesma escrita) e no cache-aside da leitura, onde o `None` do `GET` é
    cruzado com o `SET`.
    """
    return bool(cache.delete(chave_detalhe(item_id)))


def invalidar_pedido(pedido_id: Any) -> bool:
    """Apaga a chave do pedido. `True` se a chave existia.

    Chamado de qualquer processo que altere o pedido (API no pagamento recusado,
    `consumidor._executar_efeito` quando o worker muda o status): o `DEL` vai
    direto ao Redis compartilhado, então a invalidação não depende de estarmos
    no mesmo container do leitor. O retorno não diagnostica Redis fora do ar
    (mesma ressalva de `invalidar_detalhe`); o TTL de `CACHE_TTL_PEDIDO` é a
    rede de proteção.
    """
    return bool(cache.delete(chave_pedido(pedido_id)))


def invalidar_detalhes(item_ids: Iterable[Any]) -> int:
    """Apaga as chaves de detalhe de vários itens (itens de uma categoria).

    Existe para o caso da `Category`: renomear uma categoria altera o
    `category_name` que aparece no detalhe de *todos* os seus itens, e apagar a
    categoria derruba os itens por `CASCADE`. Nos dois casos o conjunto de ids
    afetado é conhecido e pequeno, então `DEL` por chave é mais barato e mais
    previsível do que varrer o Redis com `SCAN`.
    """
    total = 0
    for item_id in item_ids:
        if invalidar_detalhe(item_id):
            total += 1
    return total


def limpar_namespace() -> int:
    """Remove todas as chaves `catalogo:v1:*` (testes e comandos).

    Cobre a listagem, o detalhe e a chave de geração. Usa `delete_pattern`, que
    internamente faz `SCAN` + `DEL` em pipeline: o Redis nunca é bloqueado por
    um `KEYS *`. Diferente de `cache.clear()`, que executa `FLUSHDB` e apagaria
    também os contadores de throttling da Aula 7.
    """
    removidas = cache.delete_pattern(NAMESPACE)
    if removidas is None:
        _log_falha("limpeza do cache", None)
        return 0
    logger.info("Cache do catálogo limpo: %s chave(s).", removidas)
    return int(removidas)


def cliente_redis():
    """Cliente Redis cru, ou `None` se não der para conectar.

    Existe para os comandos (`cache_stats`, `cache_benchmark`) lerem `INFO` e
    `TTL`, que a API do Django não expõe.
    """
    try:
        from django_redis import get_redis_connection

        return get_redis_connection("default")
    except Exception as erro:  # pragma: no cover - depende do ambiente
        _log_falha("conexão com o Redis", erro)
        return None
