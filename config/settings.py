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