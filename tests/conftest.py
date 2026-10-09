"""Fixtures globais da suíte de testes (Aula 12).

O que este ficheiro garante para todos os testes:

* **cache isolado** - `cache.clear()` (um `FLUSHDB` no Redis) roda no início e
  no fim de cada teste, mas apenas no DB de teste (o `REDIS_DB` é sobrescrito
  via `docker compose exec -e REDIS_DB=15 ...`), nunca no Redis da aplicação;
* **mensageria isolada** - as transportes de broker (Kafka e RabbitMQ) são
  substituídas por fakes que registram o que *seria* publicado. Nenhum evento
  de teste chega a um broker consumido pelos workers;
* **relógio controlável** - a fixture `relogio_fixo` congela
  `django.utils.timezone.now` quando a asserção depende de tempo.

As fixtures de dados (`base`) devolvem uma *factory* preguiçosa: os registos
são criados quando a factory é chamada, dentro do teste. Assim a mesma fixture
serve testes com `django_db` e com `django_db(transaction=True)` sem conflito
entre os dois modos de transação do pytest-django.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from repositories.models import Category, Item, Role
from repositories.user_repository import UserRepository
from services import cache as cache_service
from services.auth_service import AuthService
from services.mensageria import produtor as _produtor
from services.mensageria import produtor_kafka as _produtor_kafka

#: transportes reais, guardados antes de `broker_isolado` trocá-los pelos fakes.
#: Testes dos próprios transportes usam a fixture `transporte_real` para os
#: recuperar dentro do teste.
_REAL_RABBIT_PEDIDO_CRIADO = _produtor.publicar_pedido_criado
_REAL_KAFKA_PUBLICAR_EVENTO = _produtor_kafka.publicar_evento
_REAL_KAFKA_PEDIDO_CRIADO = _produtor_kafka.publicar_pedido_criado

SENHA_ADMIN = "senha-admin-de-teste"
SENHA_USER = "senha-user-de-teste"

#: instante determinístico usado pelas fixtures/asserções de tempo
INSTANTE_FIXO = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)


class BrokerIsolado:
    """Fake das transportes de mensageria.

    Registra cada publicação como `(canal, envelope)` e devolve `resultado`
    (confirmado por padrão), permitindo simular o broker recusando a
    publicação sem tocar rede nenhuma.
    """

    def __init__(self) -> None:
        self.publicacoes: list = []
        self.resultado = True

    def transporte(self, canal: str):
        def publicar(envelope):
            self.publicacoes.append((canal, envelope))
            return self.resultado

        return publicar


@pytest.fixture(autouse=True)
def broker_isolado(monkeypatch) -> BrokerIsolado:
    """Substitui as transportes de broker por fakes (nenhuma publicação real)."""
    from services.mensageria import produtor, produtor_kafka

    controle = BrokerIsolado()
    monkeypatch.setattr(
        produtor_kafka, "publicar_evento", controle.transporte("kafka")
    )
    monkeypatch.setattr(
        produtor_kafka, "publicar_pedido_criado", controle.transporte("kafka")
    )
    monkeypatch.setattr(
        produtor, "publicar_pedido_criado", controle.transporte("rabbitmq")
    )
    return controle


@pytest.fixture
def transporte_real(monkeypatch):
    """Devolve as transportes de broker reais dentro de um teste.

    Necessário para exercitar `produtor`/`produtor_kafka` de verdade: a fixture
    autouse `broker_isolado` já os substituiu por fakes antes do teste correr.
    """
    monkeypatch.setattr(
        _produtor, "publicar_pedido_criado", _REAL_RABBIT_PEDIDO_CRIADO
    )
    monkeypatch.setattr(
        _produtor_kafka, "publicar_evento", _REAL_KAFKA_PUBLICAR_EVENTO
    )
    monkeypatch.setattr(
        _produtor_kafka, "publicar_pedido_criado", _REAL_KAFKA_PEDIDO_CRIADO
    )


@pytest.fixture(autouse=True)
def cache_limpo():
    """Zera cache e métricas antes e depois de cada teste (Redis DB de teste)."""
    cache.clear()
    cache_service.zerar_metricas()
    yield
    cache.clear()
    cache_service.zerar_metricas()


@pytest.fixture
def client() -> APIClient:
    """Cliente HTTP de teste do DRF."""
    return APIClient()


@pytest.fixture
def users_repo() -> UserRepository:
    return UserRepository()


@pytest.fixture
def auth_service(users_repo) -> AuthService:
    return AuthService(users=users_repo)


@pytest.fixture
def cabecalho_de(auth_service):
    """Devolve o header `Authorization` com um token emitido para o usuário."""

    def _cabecalho(user) -> dict:
        return {"HTTP_AUTHORIZATION": f"Bearer {auth_service.issue_token(user)}"}

    return _cabecalho


@pytest.fixture
def base(users_repo, auth_service):
    """Factory preguiçosa com usuários e catálogo, no molde de `ApiTestCaseBase`.

    Chamada dentro do teste (`dados = base()`), cria os registos na transação
    já aberta pelo marker do pytest-django - funciona com `django_db` normal e
    com `django_db(transaction=True)`.
    """

    def _criar() -> SimpleNamespace:
        admin = users_repo.create_user("admin", SENHA_ADMIN, Role.ADMIN)
        usuario = users_repo.create_user("user", SENHA_USER, Role.USER)
        categoria = Category.objects.create(name="Eletrônicos")
        outra_categoria = Category.objects.create(name="Livros")
        return SimpleNamespace(
            admin=admin,
            usuario=usuario,
            auth=auth_service,
            senha_admin=SENHA_ADMIN,
            senha_user=SENHA_USER,
            categoria=categoria,
            outra_categoria=outra_categoria,
            item_ativo=Item.objects.create(
                name="Teclado mecânico",
                price="349.90",
                category=categoria,
            ),
            item_inativo=Item.objects.create(
                name="Teclado antigo",
                price="99.90",
                category=categoria,
                is_active=False,
            ),
            livro=Item.objects.create(
                name="Livro de Python",
                price="79.90",
                category=outra_categoria,
            ),
        )

    return _criar


@pytest.fixture
def relogio_fixo(monkeypatch):
    """Congela `django.utils.timezone.now` em `INSTANTE_FIXO`."""
    from django.utils import timezone

    monkeypatch.setattr(timezone, "now", lambda: INSTANTE_FIXO)
    return INSTANTE_FIXO
