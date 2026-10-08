import os
import warnings
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "dev-insecure-synapseshop-change-me"
)

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    # `auth` entra na Aula 7: é ele que fornece o hasher de senha, as
    # permissões e o modelo de usuário customizado em `repositories.User`.
    "django.contrib.auth",
    "django.contrib.staticfiles",
    "rest_framework",
    # `django_filters` precisa estar instalado para que os templates do
    # formulário de filtro da Browsable API sejam encontrados
    "django_filters",
    "repositories",
    "api",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# o usuário da aplicação é o modelo do app `repositories`, e não o
# `auth.User` padrão, porque a role é parte do domínio
AUTH_USER_MODEL = "repositories.User"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB"),
        "USER": os.environ.get("POSTGRES_USER"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD"),
        "HOST": os.environ.get("POSTGRES_HOST", "postgres"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "UNAUTHENTICATED_USER": None,
    # Autenticação da API: o token JWT chega no header `Authorization: Bearer`.
    # A biblioteca lê o header, valida assinatura/expiração e carrega o
    # usuário do banco; nenhuma sessão ou cookie é criado.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    # Nenhuma rota "abre por acaso": cada view declara explicitamente quem
    # pode acessá-la em `api/permissions.py`.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    # Paginação padrão das listas, com `?page=` e `?limit=`.
    "DEFAULT_PAGINATION_CLASS": "api.pagination.CatalogoPagination",
    "PAGE_SIZE": 20,
    # Throttling padrão: por IP para quem não está autenticado e por usuário
    # para quem está. Os limites são lidos do ambiente (ver `THROTTLE_RATES`).
    "DEFAULT_THROTTLE_CLASSES": [
        "api.throttling.AnonRateThrottle",
        "api.throttling.UserRateThrottle",
        "api.throttling.AdminWriteThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.environ.get("THROTTLE_ANON_RATE", "60/min"),
        "user": os.environ.get("THROTTLE_USER_RATE", "300/min"),
        # escopos com regra própria, ver api/throttling.py
        "login": os.environ.get("THROTTLE_LOGIN_RATE", "5/min"),
        "admin_write": os.environ.get("THROTTLE_ADMIN_WRITE_RATE", "30/min"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

# Assinatura e expiração do JWT. A chave nunca é escrita no código: vem do
# ambiente e, na ausência de `JWT_SIGNING_KEY`, cai na `SECRET_KEY` do Django
# (que também é lida do ambiente).
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=int(os.environ.get("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", "30"))
    ),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "ALGORITHM": "HS256",
    "SIGNING_KEY": os.environ.get("JWT_SIGNING_KEY", SECRET_KEY),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

if DEBUG and len(SIMPLE_JWT["SIGNING_KEY"].encode()) < 32:
    # HS256 com chave curta é fraquinha (a RFC 7518 pede >= 256 bits). Não é
    # erro de execução - assim a aula roda sem configuração extra - mas o
    # aviso evita que a chave fraca vire hábito.
    warnings.warn(
        "SIMPLE_JWT['SIGNING_KEY'] tem menos de 32 bytes; defina "
        "JWT_SIGNING_KEY com um valor forte (ex.: openssl rand -hex 32).",
        RuntimeWarning,
        stacklevel=2,
    )

# ---------------------------------------------------------------------------
# Aula 8 - cache-aside com Redis
# ---------------------------------------------------------------------------
# O Redis é o cache default do Django: além do catálogo, é onde o contador do
# throttling da Aula 7 passa a morar (ver `api/throttling.py`).
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
REDIS_DB = os.environ.get("REDIS_DB", "0")
# `REDIS_URL` inteiro pode ser injetado (útil quando o Redis exige senha);
# sem ele, a URL é montada a partir das três variáveis acima.
REDIS_URL = os.environ.get("REDIS_URL") or f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

# Kill-switch do cache. Com `false`, toda leitura vai direto ao PostgreSQL e o
# header `X-Cache` responde `BYPASS` - é o botão de emergência para desligar o
# cache sem derrubar e sem reiniciar a aplicação.
CACHE_ENABLED = os.environ.get("CACHE_ENABLED", "true").lower() in ("1", "true", "yes")

# TTLs do cache-aside. A listagem muda mais rápido (e é a rota mais pesada,
# com `COUNT` + ordenação), então expira antes; o detalhe de um item é estável
# e pode ficar mais tempo no cache. O pedido (Aula 11) também é relativamente
# estável, mas muda de estado com o worker - TTL curto, com invalidação
# explícita em toda escrita que altera o pedido.
CACHE_TTL_LISTA = int(os.environ.get("CACHE_TTL_LISTA", "60"))
CACHE_TTL_DETALHE = int(os.environ.get("CACHE_TTL_DETALHE", "300"))
CACHE_TTL_PEDIDO = int(os.environ.get("CACHE_TTL_PEDIDO", "60"))

# Prefixo aplicado pelo Django a todas as chaves, para o mesmo Redis poder
# servir outra aplicação sem colisão de nomes.
CACHE_KEY_PREFIX = os.environ.get("CACHE_KEY_PREFIX", "synapseshop")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": CACHE_KEY_PREFIX,
        # TTL padrão quando o `cache.set()` não recebe timeout explícito
        "TIMEOUT": CACHE_TTL_LISTA,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            # fail-open: erro do Redis vira `None` em vez de exceção, para que
            # uma falha de cache nunca vire indisponibilidade da API
            "IGNORE_EXCEPTIONS": True,
            # o padrão do django-redis é pickle. Trocar por JSON deixa o valor
            # gravado legível (`redis-cli GET ...`) e independente da versão do
            # Python: o payload do catálogo já é normalizado para JSON puro em
            # `services/cache.py`, e os contadores de throttling são inteiros.
            "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
            # com o Redis travado, nenhuma operação pode segurar a requisição
            # por muito tempo (ver `CACHE_CONNECT_TIMEOUT`)
            "SOCKET_CONNECT_TIMEOUT": float(
                os.environ.get("CACHE_CONNECT_TIMEOUT", "1")
            ),
            "SOCKET_TIMEOUT": float(os.environ.get("CACHE_SOCKET_TIMEOUT", "1")),
        },
    }
}

# Desligado por padrão: o django-redis loga a *traceback inteira* de cada
# operação engolida, e com o Redis fora do ar isso vira uma linha de stack por
# requisição. Quem registra o erro é a camada de cache da aplicação
# (`services/cache.py`), que sabe dizer qual endpoint falhou e limiter o log.
# Ligue esta flag para depurar uma conexão que não está sendo estabelecida.
DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS = (
    os.environ.get("CACHE_LOG_IGNORED_EXCEPTIONS", "false").lower()
    in ("1", "true", "yes")
)

# O Django não configura logger nenhum por padrão; sem esta seção, os avisos de
# cache (falha do Redis, evento de invalidação) apareceriam sem contexto. Só o
# que a aplicação registra sai daqui - o nível é ajustável pelo ambiente.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simples": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simples",
        },
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
    },
}