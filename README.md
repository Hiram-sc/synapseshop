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
| Pagamento | `POST /api/v1/pedidos/{id}/pagamento/` | autenticado (dono ou `admin`) |
| Assistente de IA | `POST /api/v1/assist/` | autenticado |
| Health check | `/health`, `/health/pronto` | público |
| Documentação | `/api/schema/`, `/api/docs/`, `/api/redoc/` | público |

Leitura do catálogo é pública e **cacheada em Redis**; escrita exige Bearer token
com papel `admin` e **invalida o cache** do que mudou. A criação de pedido
**publica o evento `PedidoCriado` no tópico Kafka** `pedidos.pedidocriado`, e o
worker `pedido-worker` (mesmo grupo de consumo) confirma o pedido consumindo
esse tópico — com commit pós-processamento, idempotência e DLQ. O broker é
selecionável: `MENSAGERIA_BROKER=rabbitmq` reativa o fluxo alternativo em
RabbitMQ.

Na Aula 11 o fluxo **Pedido → Pagamento → Notificação** fecha o ciclo: o
pagamento simulado publica o `PagamentoProcessado` (com `status` `APROVADO` ou
`RECUSADO`, decidido pelo cliente no corpo da requisição), o `notificacao-worker`
consome e registra a `Notificacao` — publicando o evento terminal
`NotificacaoEnviada` — e o pedido **cancelado** por pagamento recusado é o
desfecho complementar. O `GET /pedidos/{id}/` também passa a **cache-aside** em
Redis (TTL 60s, `X-Cache` `HIT`/`MISS`/`BYPASS`, sem vazar pedido alheio), e a API
ganhou um **readiness** (`/health/pronto`) que atesta PostgreSQL, Redis e broker.

## 🛠️ Tecnologias

- Python 3.12
- Django 5.2 / Django REST Framework 3.18
- PostgreSQL 16
- Redis 7 (`django-redis`)
- Apache Kafka 3.9 (KRaft, `confluent-kafka`) — broker padrão
- RabbitMQ 3.13 (`pika`) — broker alternativo preservado
- SimpleJWT, django-filter, **drf-spectacular** (OpenAPI/Swagger/ReDoc), Docker Compose

## 🧭 Como executar

```bash
docker compose up -d --build      # sobe PostgreSQL, Redis, RabbitMQ, Kafka, API e worker
docker compose exec api python manage.py migrate
docker compose exec api python manage.py create_users
```

A API fica em `http://localhost:8000`, com Browsable API e documentação
interativa: **Swagger UI** em `/api/docs/`, **ReDoc** em `/api/redoc/` e o
**contrato OpenAPI** em `/api/schema/`. O broker
padrão é o Kafka (KRaft single-node; porta do host `29092`), o RabbitMQ
Management continua em `http://localhost:15672` (usuário/senha `synapseshop`)
para inspeção do broker alternativo, e os consumidores rodam nos serviços
`pedido-worker` e `notificacao-worker`.

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

## ⚙️ Pagamento e notificação (Aula 11)

O desfecho é simulação do cliente: `{"status": "APROVADO"}` (padrão, se o corpo
vier vazio) publica `PagamentoProcessado` e mantém o pedido no fluxo normal; o
`notificacao-worker` registra a `Notificacao` e publica `NotificacaoEnviada`:
`{"status": "RECUSADO"}` grava `status=cancelado` no pedido e **não** gera
notificação. Respostas do endpoint: **201** (evento confirmado), **202**
(persistiu, broker recusou a publicação), **409** (pedido já tinha pagamento,
sem republicar), **404** (pedido inexistente ou alheio), **400** (status
inválido).

```bash
curl -X POST http://localhost:8000/api/v1/pedidos/1/pagamento/ \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"status":"APROVADO"}'
```

O **cache do pedido** atende o `GET /pedidos/{id}/` com TTL 60s
(`CACHE_TTL_PEDIDO`) e `X-Cache: HIT/MISS/BYPASS`. No `HIT`, a régua do dono
(`usuario` do payload + papel do requester) continua sendo aplicada, então uma
chave quente gravada por um usuário **não vaza** o pedido para outro; e toda
escrita que muda o pedido (o `pedido-worker` ao confirmar, o pagamento recusado
a ao cancelar) **invalida a chave** — o Redis compartilhado liga os processos,
e o TTL é a rede de proteção.

**Readiness:** `/health/pronto` responde 200 somente com **PostgreSQL** (`SELECT
1`), **Redis** (`PING`) e o **broker ativo** (metadados Kafka ou conexão AMQP)
saudáveis; qualquer queda responde 503. É o alvo do healthcheck da `api` no
Compose (`python -c urllib`, pois a imagem não tem `curl`).

```bash
docker compose exec -T api python scripts/smoke_test_fluxo_pagamento.py   # smoke do fluxo

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
| `pedidos.pagamentoprocessado` | 3 | 7 dias |
| `pedidos.pagamentoprocessado.dlq` | 1 | 28 dias |
| `pedidos.notificacaoenviada` | 3 | 7 dias |
| `pedidos.notificacaoenviada.dlq` | 1 | 28 dias |

O registro `EVENTOS` (em `services/mensageria/envelope.py`) passa a conhecer o
`PagamentoProcessado` e o `NotificacaoEnviada`, com o mesmo contrato e valores
monetários em string. O produtor é genérico (`publicar_evento`, resolvendo o
tópico por `event_type`); cada evento carrega a própria `idempotency_key`:
`pagamento:{pedido_id}` e `notificacao:{pagamento_id}`. No RabbitMQ
(alternativo), apenas o `PedidoCriado` tem rota — os eventos da Aula 11
retornam `False` e o `notificacao-worker` **recusa iniciar** se o broker ativo
for o RabbitMQ.

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
trabalho). O `notificacao-worker` consome o `pedidos.pagamentoprocessado` com
**group próprio** (`KAFKA_GROUP_ID_NOTIFICACAO`), publica o
`NotificacaoEnviada` para `APROVADO` e encerra sem publicação para `RECUSADO`.

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
docker compose exec -T api python scripts/smoke_test_fluxo_pagamento.py           # fluxo pagamento+notificação+cache+health
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

## 📜 Documentação da API (Aula 13)

O contrato **OpenAPI 3.0.3 é gerado do código** (serializers/views) com
`drf-spectacular` e versionado em `docs/openapi.yaml`:

```bash
docker compose exec -T api python manage.py gerar_openapi   # regenera docs/openapi.yaml
npx --yes @stoplight/spectral-cli lint docs/openapi.yaml    # validação estática (0 problemas)
```

| Rota | O que é |
|---|---|
| `/api/schema/` | contrato OpenAPI (YAML), gerado on-the-fly |
| `/api/docs/` | **Swagger UI** (navegar e tentar as chamadas) |
| `/api/redoc/` | **ReDoc** (leitura) |

- **Convenções documentadas:** paginação `page`/`limit` (teto 100) com envelope
  `count/next/previous/results`; filtros `?search=`, `?is_active=`,
  `?category=`; ordenação `?ordering=`; idempotência por `idempotency_key` no
  **corpo** do `POST /pedidos/`.
- **Divergências assumidas da spec** (sem mudança de comportamento):
  `Idempotency-Key` header → campo `idempotency_key`; `X-Trace-Id` não
  aplicado; paginação por cursor inexistente; filtro `?status=` inexistente;
  `sort=` → `ordering=`; erros no formato DRF (não RFC 7807). Registradas em
  `docs/AULA_13_OPENAPI_SWAGGER_POSTMAN.md` e `docs/INTEGRADOR_EXTERNO.md`.
- **Postman/Insomnia:** 52 requisições em `docs/postman/` + environment
  `SynapseShop.postman_environment.json`.
- **Erros padrão:** 400 validação (mapa por campo), 401/403 `{"detail"}`,
  404 `{"detail"}`, 409 idempotência/duplicidade, 429 com `Retry-After`.

## 🤖 Assistente de IA e `llm_service` (Aula 14)

A camada de IA é um serviço isolado (`services/llm_service.py`) exposto pelo
endpoint autenticado `POST /api/v1/assist/`, que **resume** (`summarize`) ou
**explica** (`explain`) logs. O provedor é acessível por HTTP/JSON no formato
OpenAI (`/chat/completions`); o padrão é o DeepSeek, trocável por
`LLM_API_BASE_URL`/`LLM_MODEL`.

```bash
curl -X POST http://localhost:8000/api/v1/assist/ \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"mode":"summarize","logs":["2026-01-02 ERROR falha ao gravar pedido",
       "2026-01-02 WARN retry em 2s"],"temperature":0.1,"max_tokens":256}'
```

Contrato do corpo: `mode` (`summarize|explain`), `logs` (**string** ou
**array** de linhas), `temperature` (`0.0-1.0`, padrão `0.2`) e `max_tokens`
(`1-4096`, padrão `256`). A resposta segue a estrutura da spec:

```json
{ "answer": "...", "meta": { "tokens_prompt": 5, "tokens_output": 7 } }
```

### Arquitetura do serviço

`LLMService` monta o prompt, aplica redaction/truncamento e chama o provedor
com resiliência:

- **Timeout** por chamada (`LLM_TIMEOUT_S`, padrão 5s).
- **Retry** com **backoff exponencial + jitter** apenas para falhas
  transientes (`408/429/5xx`); erros de cliente (`400/401/403`) não repetem.
- **Circuit breaker** com os três estados `closed`/`open`/`half-open`
  (`CircuitBreaker`): ao abrir, bloqueia novas chamadas por
  `LLM_CIRCUITO_RESET_S`; vencido o intervalo entra em `half-open` e libera
  **uma** chamada-sonda — sucesso fecha, falha reabre.
- **Instância compartilhada** (`obter_llm_service()`): o breaker é o **mesmo
  entre requisições**; sem isso cada `POST` criaria um breaker zerado e a
  proteção nunca dispararia.
- **Telemetria** (`TelemetriaLLM`) por chamada: latência, tokens de
  prompt/saída, custo estimado (`LLM_PRECO_ENTRADA_POR_1K`/
  `LLM_PRECO_SAIDA_POR_1K`), tentativas e sucesso. É registrada em log
  estruturado (`logger "llm_service"`), **sem** conteúdo de log nem segredos.

Mapeamento de falhas do endpoint: timeout → **504**; resposta inválida do
provedor → **502**; `429` do provedor e **circuito aberto/sem configuração** →
**503**; sem token → **401**; corpo inválido → **400**.

### Templates de prompt

`services/prompts.py` isola os templates (`PromptTemplates`) — um *system* e um
*user* por modo — e `montar_prompt(mode, logs)` devolve as mensagens no formato
do provedor. `serializar_logs()` normaliza `string`/`array` (inclusive dicts com
`timestamp/level/message`) num único bloco de texto antes do envio.

### Privacidade (LGPD/RGPD) e redaction

`services/redaction.py` roda **antes** do envio ao provedor
(`LLM_REDACTAR_ENVIO=true`) e no que for registrado em log:

- **Segredos** por chave (`password`, `token`, `api_key`, `secret`, `senha`,
  `private_key`, …) e por padrão textual (`senha=...`, `Bearer ...`, JWT).
- **PII** por regex: CPF, CNPJ, cartão de crédito, e-mail, telefone, CEP.
- **Truncamento** do bloco de logs a `LLM_MAX_LOGS_CARACTERES` (padrão 2000).

O que sai do `LLMService.montar_logs()` — já redigido e truncado — é o único
conteúdo enviado ao provedor.

### Limites configuráveis

| Variável | Padrão | Papel |
|---|---|---|
| `LLM_API_KEY` | *(vazio)* | credencial do provedor (nunca versionada) |
| `LLM_API_BASE_URL` | `https://api.deepseek.com/v1` | base do provedor OpenAI-compatível |
| `LLM_MODEL` | `deepseek-chat` | modelo |
| `LLM_TIMEOUT_S` | `5` | timeout por chamada |
| `LLM_MAX_TENTATIVAS` | `3` | tentativas de retry |
| `LLM_BACKOFF_BASE_S` / `LLM_BACKOFF_MAX_S` | `0.1` / `1.0` | base e teto do backoff |
| `LLM_CIRCUITO_LIMITE_FALHAS` / `LLM_CIRCUITO_RESET_S` | `5` / `60` | limiar e janela do breaker |
| `LLM_MAX_TOKENS_LIMITE` | `1024` | teto de `max_tokens` |
| `LLM_MAX_LOGS_CARACTERES` | `2000` | truncamento dos logs no envio |
| `LLM_REDACTAR_ENVIO` | `true` | liga/desliga a redaction no envio |
| `LLM_PRECO_ENTRADA_POR_1K` / `LLM_PRECO_SAIDA_POR_1K` | `0.001` / `0.002` | estimativa de custo |

Sem `LLM_API_KEY`, o endpoint responde **503** (serviço não configurado) e
registra o aviso; nenhuma chamada de rede é feita. Nos testes o provedor é
sempre **mockado** — nenhuma credencial real entra no repositório.

### Melhorias futuras e pendências (registro de issues)

Não há *issue tracker* configurado neste repositório (sem `.github/` e sem
remote), então as pendências ficam registradas aqui como backlog:

- **Cache de respostas** do assistente (chave por `mode`+hash dos logs
  redigidos) para reduzir custo/latência de chamadas repetidas.
- **Guardrails adicionais**: limites por usuário (rate limit dedicado) no
  `/assist/`, filtro de conteúdo e validação de saída.
- **Métricas Prometheus** para telemetria (hoje só em log estruturado).
- **Streaming** da resposta do provedor.
- Evolução de RAG/embeddings fora do escopo desta aula.

## 🧪 Testes

399 testes contra PostgreSQL e Redis reais:

```bash
docker compose exec -T api pytest
```

> O `manage.py test` **não** coleta os testes do projeto (eles usam o estilo
> função do pytest); o executor correto é o `pytest` acima, com cobertura em
> `pytest.ini`/`.coveragerc`.

## 📁 Estrutura

```text
aula2/
├── api/                    views, serializers, permissões, throttling, paginação
│   ├── views.py            cache-aside nas leituras (itens e pedido), eventos nas
│   │                       escritas, PedidoViewSet e PagamentoView (Aula 11),
│   │                       AssistView (Aula 14); anotações @extend_schema (Aula 13)
│   ├── openapi.py          hook do contrato: erros padrão e /health* (Aula 13)
│   ├── health.py           readiness /health/pronto (PostgreSQL, Redis, broker)
│   └── management/commands/gerar_openapi.py   gera docs/openapi.yaml (Aula 13)
├── config/settings.py      DRF, JWT, CACHES no Redis, TTLs do cache
├── repositories/           models, services, commands, migrations
│   ├── models.py               Pedido, PedidoItem, Pagamento, Notificacao, EventoProcessado
│   ├── pedido_repository.py    escrita atômica do pedido + itens
│   └── management/commands/
│       ├── cache_stats.py      inventário de chaves e métricas
│       ├── cache_benchmark.py  sem cache vs MISS vs HIT
│       ├── pedido_worker.py    consumidor do broker ativo (Kafka ou RabbitMQ)
│       ├── notificacao_worker.py  consumidor do PagamentoProcessado (Aula 11)
│       └── limpar_eventos_processados.py  limpeza de EventoProcessado
├── services/               cache, events, auth, user, pedido, pagamento, notificação, llm
│   ├── cache.py            chaves, geração, TTL, fail-open, métricas (itens e pedido)
│   ├── events.py           barramento in-process
│   ├── cache_invalidation.py  regras de invalidação
│   ├── pedido_service.py   regras do pedido (total no servidor)
│   ├── pagamento_service.py    um pagamento por pedido; RECUSADO cancela (Aula 11)
│   ├── notificacao_service.py  efeito do PagamentoProcessado no banco (Aula 11)
│   ├── llm_service.py      LLMService, circuit breaker e telemetria (Aula 14)
│   ├── prompts.py          templates e montagem do prompt (Aula 14)
│   ├── redaction.py        ocultação de segredos/PII e truncamento (Aula 14)
│   └── mensageria/         envelope, idempotência, config e:
│       ├── facade.py           seleção do broker (MENSAGERIA_BROKER) e publicar_evento
│       ├── produtor.py         produtor RabbitMQ (broker alternativo)
│       ├── consumidor.py       consumidor RabbitMQ (broker alternativo)
│       ├── topologia.py        topologia RabbitMQ (broker alternativo)
│       ├── produtor_kafka.py   produtor Kafka (key=idempotency_key)
│       ├── consumidor_kafka.py consumidor Kafka (commit pós-processamento, DLQ)
│       └── topologia_kafka.py  tópicos e retenção (AdminClient)
├── scripts/
│   ├── smoke_test_mensageria.py          validação pontual do fluxo RabbitMQ
│   ├── smoke_test_mensageria_kafka.py    validação pontual do fluxo Kafka
│   ├── smoke_test_fluxo_pagamento.py     fluxo pagamento+notificação+cache+health
│   └── benchmark_mensageria_kafka.py     latência, throughput e trade-off do dedupe
├── tests/                  suíte Django (399 testes)
├── docs/                   documentação técnica do projeto
│   ├── AULA_06_PERSISTENCIA_POSTGRESQL.md
│   ├── AULA_07_JWT_ROLES_THROTTLING.md
│   ├── AULA_08_CACHE_ASIDE_REDIS.md
│   ├── AULA_09_MENSAGERIA_ASSINCRONA.md
│   ├── AULA_10_MENSAGERIA_APACHE_KAFKA.md
│   ├── AULA_13_OPENAPI_SWAGGER_POSTMAN.md     documentação da API (Aula 13)
│   ├── INTEGRADOR_EXTERNO.md                  guia de quem consome a API
│   ├── openapi.yaml                           contrato OpenAPI versionado
│   └── postman/                               coleção (52 requests) + environment
├── inventory/              microsserviço FastAPI separado (intocado)
├── docker-compose.yml      db, cache, rabbitmq, kafka, api, pedido-worker, notificacao-worker
├── Dockerfile
├── .env.example            variáveis de ambiente sem segredos
├── .spectral.yaml          regras de lint do contrato OpenAPI (Aula 13)
├── CHANGELOG.md            histórico de versões
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
| `docs/AULA_13_OPENAPI_SWAGGER_POSTMAN.md` | OpenAPI gerado, Swagger UI/ReDoc, Spectral, Postman, erros padrão, divergências |
| `docs/INTEGRADOR_EXTERNO.md` | guia do integrador: auth, pedido idempotente, pagamento, erros, limites |
| `docs/openapi.yaml` | contrato OpenAPI 3.0.3 versionado (gerado por `gerar_openapi`) |
| `CHANGELOG.md` | histórico de versões |

