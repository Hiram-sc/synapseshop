"""Throttling (rate limiting) da API.

Regras da aula:

* o limite é contado por **IP** para requisições anônimas e por **usuário**
  para requisições autenticadas, exceto no login, que é sempre por IP;
* a janela é de 60 segundos (`THROTTLE_*_RATE` no ambiente, ex.: `5/min`);
* estourar o limite devolve **HTTP 429** com o header `Retry-After`.

O contador vive no cache do Django. Desde a Aula 8 o cache padrão é o **Redis**
(`django_redis`), então o limite passou a valer para todas as réplicas da API e
não apenas para a memória de uma instância. Isso traz duas consequências
práticas:

* `docker compose up` sem o serviço `redis` derruba o contador junto com a API -
  o throttle só volta a valer quando o Redis sobe;
* os testes usam o mesmo Redis do ambiente de desenvolvimento, e o `cache.clear()`
  da base de testes apaga também os contadores (é inofensivo para os testes,
  que limpam o estado de qualquer forma).

Com `CACHE_ENABLED=false` o catálogo ignora o Redis, mas o throttle continua
precisando dele: as duas coisas são independentes.
"""

from __future__ import annotations

from rest_framework.throttling import (
    AnonRateThrottle as DRFAnonRateThrottle,
    ScopedRateThrottle as DRFScopedRateThrottle,
    UserRateThrottle as DRFUserRateThrottle,
)

# cabeçalho que diz ao cliente quanto tempo esperar antes de tentar de novo
RETRY_AFTER_HEADER = "Retry-After"

#: ações do ViewSet que alteram dados (as demais são de leitura)
ACOES_DE_ESCRITA = frozenset({"create", "update", "partial_update", "destroy"})


class RetryAfterThrottleMixin:
    """Acrescenta o header `Retry-After` à resposta 429 do DRF.

    O DRF calcula os segundos restantes em `wait()`, mas não os expõe no
    header; sem isso o cliente descobre o intervalo errado (ou por tentativa
    e erro) ao tomar 429.
    """

    def throttled_response(self, request, wait: int):
        response = super().throttled_response(request, wait)
        response[RETRY_AFTER_HEADER] = f"{max(int(wait), 0)}"
        return response


class AnonRateThrottle(RetryAfterThrottleMixin, DRFAnonRateThrottle):
    """Limite por IP para requisições sem token válido.

    Escopo `anon`, configurado em `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`.
    """


class UserRateThrottle(RetryAfterThrottleMixin, DRFUserRateThrottle):
    """Limite por usuário autenticado.

    Escopo `user`, configurado em `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`.
    Não conta requisições anônimas (o `AnonRateThrottle` cobre esse caso).
    """


class ScopedRateThrottle(RetryAfterThrottleMixin, DRFScopedRateThrottle):
    """Limite por escopo, para rotas que merecem um teto próprio.

    Só age quando a view declara `throttle_scope`; o limite vem de
    `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"][scope]`.
    """


class LoginRateThrottle(ScopedRateThrottle):
    """Limite do endpoint de login: 5 tentativas por minuto, por IP.

    O login é a rota mais sensível da API: é a única acessível sem token e a
    que recebe credenciais, então é a primeira a sofrer tentativa de força
    bruta. O limite é por IP (e não por usuário) porque o atacante escolhe o
    username, mas não o endereço de origem.

    O escopo `login` é declarado pela view em `throttle_scope`, que é de onde
    o DRF lê - o atributo `scope` da classe de throttle não é consultado.
    """

    def get_cache_key(self, request, view):
        """Sempre por IP, mesmo que a rota receba credenciais válidas depois."""
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class AdminWriteThrottle(ScopedRateThrottle):
    """Limite `admin_write` (30/min por padrão) só nas ações de escrita.

    Por que uma classe própria, e não só `throttle_scope = "admin_write"` na
    view: o DRF resolve o escopo lendo a **view** dentro de `allow_request`
    (`self.scope = getattr(view, "throttle_scope", None)`). Se a view
    declarasse o escopo, a leitura pública do catálogo pagaria o mesmo
    contador das escritas - e a vitrine é justamente a rota que precisa do
    teto maior, por IP.

    Aqui o escopo vem da view (portanto o contador é o `admin_write`), mas a
    chave só é construída nas ações de escrita; nas demais, `get_cache_key`
    devolve `None` e o DRF libera a requisição sem tocar no contador.
    """

    def get_cache_key(self, request, view):
        if getattr(view, "action", None) not in ACOES_DE_ESCRITA:
            return None
        return super().get_cache_key(request, view)

