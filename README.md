# SynapseShop (SynapseTech)

API de catálogo — Django REST Framework + PostgreSQL + **Redis** com
cache-aside, **mensageria assíncrona com Apache Kafka** (broker padrão) e
**RabbitMQ como broker alternativo**, sobre uma base de JWT/papéis/throttling.

O serviço de inventário (`inventory/`, FastAPI + Alembic) é um processo
separado e segue intocado pela API principal.

## 📋 O que a API faz

| Recurso | Rotas | Acesso |
|---|---|---|
| Autenticação | `POST /api/v1/auth/login/`, `GET /api/v1/auth/me/` | token JWT |
| Categorias | `/api/v1/categories/` | leitura pública, escrita `admin` |
| Itens | `/api/v1/items/` | leitura pública, escrita `admin` |
| Pedidos | `POST /api/v1/pedidos/`, `GET /api/v1/pedidos/{id}/` | autenticado (dono ou `admin`) |
| Health check | `/health`, `/health/db` | público |

Leitura do catálogo é pública e **cacheada em Redis**; escrita exige Bearer token
com papel `admin` e **invalida o cache** do que mudou. A criação de pedido
**publica o evento `PedidoCriado` no tópico Kafka** `pedidos.pedidocriado`, e o
worker `pedido-worker` (mesmo grupo de consumo) confirma o pedido consumindo
esse tópico — com commit pós-processamento, idempotência e DLQ. O broker é
selecionável: `MENSAGERIA_BROKER=rabbitmq` reativa o fluxo alternativo em
RabbitMQ.

## 🛠️ Tecnologias

- Python 3.12
- Django 5.2 / Django REST Framework 3.16
- PostgreSQL 16
- Redis 7 (`django-redis`)
- Apache Kafka 3.9 (KRaft, `confluent-kafka`) — broker padrão
- RabbitMQ 3.13 (`pika`) — broker alternativo preservado
- SimpleJWT, django-filter, Docker Compose

## 🧭 Como executar

```bash
docker compose up -d --build      # sobe PostgreSQL, Redis, RabbitMQ, Kafka, API e worker
docker compose exec api python manage.py migrate
docker compose exec api python manage.py create_users
```

A API fica em `http://localhost:8000`, com Browsable API e `/docs`. O broker
padrão é o Kafka (KRaft single-node; porta do host `29092`), o RabbitMQ
Management continua em `http://localhost:15672` (usuário/senha `synapseshop`)
para inspeção do broker alternativo, e o consumidor roda no serviço
`pedido-worker`.

```bash
# login
curl -X POST http://localhost:8000/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# leitura cacheada: a segunda resposta traz X-Cache: HIT
curl -i "http://localhost:8000/api/v1/items/?limit=10"

# escrita (invalida o cache) precisa do token
curl -X PATCH http://localhost:8000/api/v1/items/1/ \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"price":"99.90"}'

# criação de pedido: publica PedidoCriado e confirma via worker
curl -X POST http://localhost:8000/api/v1/pedidos/ \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"idempotency_key":"pedido-001","itens":[{"item_id":2,"quantidade":2}]}'
```

## 💾 Cache

Cache-aside, leitura pública de itens:

- TTL **60s** na listagem, **300s** no detalhe;
- chave da listagem inclui digest de filtros, paginação e ordenação;
- invalidação por evento na escrita (`INCR` de geração, O(1));
- header `X-Cache`: `HIT`, `MISS` ou `BYPASS`;
- Redis fora do ar → `BYPASS` e resposta normal vinda do PostgreSQL.

```bash
docker compose exec api python manage.py cache_stats           # estado do cache
docker compose exec api python manage.py cache_benchmark --n 200  # ganho medido
CACHE_ENABLED=false docker compose up -d api                   # desliga o cache
```

## 📨 Mensageria assíncrona

**Broker padrão: Apache Kafka** (KRaft, `apache/kafka:3.9.1`, com
`confluent-kafka`): log particionado com retenção configurável, consumo
escalável por partição e offsets por grupo. O **RabbitMQ permanece
preservado como alternativa** e é reativado com
`MENSAGERIA_BROKER=rabbitmq`. A seleção é feita por uma **facade**
(`services/mensageria/facade.py`), lida igualmente pela API e pelo worker.

**Contrato do evento** (`services/mensageria/envelope.py`),
JSON com valores monetários em string:

```json
{"event_id": "uuid", "event_type": "PedidoCriado", "version": "1.0",
 "occurred_at": "ISO", "correlation_id": "chave", "idempotency_key": "chave",
 "dados": {"pedido": {"id": 10, "usuario_id": 1, "status": "criado",
   "total": "20.00", "itens": [{"item_id": 2, "nome": "Item 1",
   "quantidade": 2, "preco_unitario": "10.00", "subtotal": "20.00"}]}}}
```

**Tópicos** (criação explícita e idempotente via `AdminClient`,
`KAFKA_AUTO_CREATE_TOPICS_ENABLE=false`):

| Tópico | Partições | Retenção |
|---|---|---|
| `pedidos.pedidocriado` | 3 | 7 dias |
| `pedidos.pedidocriado.dlq` | 1 | 28 dias |

**Produtor:** `POST /api/v1/pedidos/` persiste e publica **com
`key=idempotency_key`** (chave de partição) e confirmação pelo broker
(`acks=all`, `flush()` + delivery callback). A resposta diz o destino do
evento: `201 + evento_publicado: true`, `202 + false` (broker indisponível —
pedido persistido) ou `200 + false` (`idempotency_key` repetida, sem
republicar).

**Consumidor:** `enable.auto.commit=false` e **commit manual sempre depois do
processamento** (ou da confirmação de reentrega/DLQ). O `group.id` reparte as
3 partições entre réplicas do `pedido-worker` (até 3 workers escalam o
consumo; offsets são por grupo — um grupo novo relê do início sem afetar o
trabalho).

**Idempotência** — mesma `idempotency_key` deduplica em dois níveis:
`UNIQUE (usuario, idempotency_key)` no `Pedido`
para o POST repetido, e Redis com TTL (pergunta rápida) + `EventoProcessado`
(`UNIQUE event_type, idempotency_key`, fonte durável) para o consumo. Registro
**depois** do efeito; reentrega vira `resultado: "duplicada"` no log.

**Retry e backoff:** 4 tentativas (`PEDIDO_WORKER_MAX_TENTATIVAS`) com recuo
exponencial 250 → 500 → 1000 ms; a reentrega **republica no tópico principal**
com `x-retry-count + 1` e a mesma chave de partição, e só deixa o offset
avançar depois da republicação confirmada. Excedeu → **DLQ**:
`pedidos.pedidocriado.dlq` (1 partição, 28 dias), **sem consumidor** —
reprocessar é decisão operacional. Contrato inválido vai direto à DLQ, sem retry.

```bash
docker compose logs -f pedido-worker                              # fluxo do consumidor Kafka
docker compose exec -T api python scripts/smoke_test_mensageria_kafka.py          # smoke Kafka
docker compose exec -T api python scripts/smoke_test_mensageria_kafka.py --falha  # retry→DLQ
docker compose exec -T api python scripts/benchmark_mensageria_kafka.py --n 30    # latência/throughput/dedupe
PEDIDO_WORKER_FALHA_IDEM_KEYS='falha-*' docker compose up -d pedido-worker  # simulação
docker compose up -d pedido-worker                                # volta ao padrão

# broker alternativo (RabbitMQ):
MENSAGERIA_BROKER=rabbitmq docker compose up -d api pedido-worker
docker compose exec -T api python scripts/smoke_test_mensageria.py --falha
MENSAGERIA_BROKER=kafka docker compose up -d api pedido-worker    # volta ao padrão
```

Detalhes, métricas e evidências: `docs/AULA_10_MENSAGERIA_APACHE_KAFKA.md`;
a alternativa RabbitMQ está documentada em `docs/AULA_09_MENSAGERIA_ASSINCRONA.md`.

## 🧪 Testes

118 testes contra PostgreSQL e Redis reais:

```bash
docker compose exec -T api python manage.py test -v 1
```

## 📁 Estrutura

```text
aula2/
├── api/                    views, serializers, permissões, throttling, paginação
│   └── views.py            cache-aside nas leituras, eventos nas escritas,
│                           PedidoViewSet (produtor do PedidoCriado)
├── config/settings.py      DRF, JWT, CACHES no Redis
├── repositories/           models, services, commands, migrations
│   ├── models.py               Pedido, PedidoItem, EventoProcessado
│   ├── pedido_repository.py    escrita atômica do pedido + itens
│   └── management/commands/
│       ├── cache_stats.py      inventário de chaves e métricas
│       ├── cache_benchmark.py  sem cache vs MISS vs HIT
│       ├── pedido_worker.py    consumidor do broker ativo (Kafka ou RabbitMQ)
│       └── limpar_eventos_processados.py  limpeza de EventoProcessado
├── services/               cache, events, auth, user, pedido, mensageria
│   ├── cache.py            chaves, geração, TTL, fail-open, métricas
│   ├── events.py           barramento in-process
│   ├── cache_invalidation.py  regras de invalidação
│   ├── pedido_service.py   regras do pedido (total no servidor)
│   └── mensageria/         envelope, idempotência, config e:
│       ├── facade.py           seleção do broker (MENSAGERIA_BROKER)
│       ├── produtor.py         produtor RabbitMQ (broker alternativo)
│       ├── consumidor.py       consumidor RabbitMQ (broker alternativo)
│       ├── topologia.py        topologia RabbitMQ (broker alternativo)
│       ├── produtor_kafka.py   produtor Kafka (key=idempotency_key)
│       ├── consumidor_kafka.py consumidor Kafka (commit pós-processamento, DLQ)
│       └── topologia_kafka.py  tópicos e retenção (AdminClient)
├── scripts/
│   ├── smoke_test_mensageria.py          validação pontual do fluxo RabbitMQ
│   ├── smoke_test_mensageria_kafka.py    validação pontual do fluxo Kafka
│   └── benchmark_mensageria_kafka.py     latência, throughput e trade-off do dedupe
├── tests/                  suíte Django (118 testes)
├── docs/                   documentação técnica do projeto
│   ├── AULA_06_PERSISTENCIA_POSTGRESQL.md
│   ├── AULA_07_JWT_ROLES_THROTTLING.md
│   ├── AULA_08_CACHE_ASIDE_REDIS.md
│   ├── AULA_09_MENSAGERIA_ASSINCRONA.md
│   └── AULA_10_MENSAGERIA_APACHE_KAFKA.md
├── inventory/              microsserviço FastAPI separado (intocado)
├── docker-compose.yml      db, cache, rabbitmq, kafka, api, pedido-worker
├── Dockerfile
├── .env.example            variáveis de ambiente sem segredos
└── requirements.txt
```

## 📚 Documentação

| Documento | Conteúdo |
|---|---|
| `docs/AULA_06_PERSISTENCIA_POSTGRESQL.md` | inventário em PostgreSQL com SQLAlchemy/Alembic |
| `docs/AULA_07_JWT_ROLES_THROTTLING.md` | JWT, papéis, throttling, paginação, filtros |
| `docs/AULA_08_CACHE_ASIDE_REDIS.md` | cache-aside com Redis, invalidação, fail-open |
| `docs/AULA_09_MENSAGERIA_ASSINCRONA.md` | RabbitMQ, evento `PedidoCriado`, idempotência, retry/backoff e DLQ |
| `docs/AULA_10_MENSAGERIA_APACHE_KAFKA.md` | Apache Kafka (KRaft), partições/ordem, commit por offset, retenção, DLQ; RabbitMQ preservado |

