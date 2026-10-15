# Guia do integrador externo - SynapseShop API

Este guia é o ponto de partida de quem vai consumir a API de fora do time.
Contrato de verdade: `docs/openapi.yaml` (gerado do código, versão atual da
API). UIs para navegar: `/api/docs/` (Swagger UI) e `/api/redoc/` (ReDoc) na
própria API.

## 1. Começar

1. Descubra a **base URL** (ex.: `http://localhost:8000` para desenvolvimento,
   diferente em produção — o contrato traz a URL em `info.title`/`servers`).
2. Leia o contrato em `docs/openapi.yaml` ou abra `/api/redoc/` no navegador.
3. Autentique para obter um token JWT:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"SEU_USUARIO","password":"SUA_SENHA"}'
```

Resposta `200`:

```json
{
  "access": "<token>",
  "token_type": "Bearer",
  "expires_in": 1800,
  "user": {"id": 1, "username": "usuario", "role": "user"}
}
```

Enviamos o token em toda chamada autenticada:

```
Authorization: Bearer <access>
```

`GET /api/v1/auth/me/` confirma quem é o dono do token. O token expira em
`expires_in` segundos.

## 2. O que a API expõe

| Endpoint | Método | Acesso | Uso |
|---|---|---|---|
| `/health`, `/health/pronto` | GET | público | liveness/readiness |
| `/api/v1/auth/login/` | POST | público | trocar credencial por JWT |
| `/api/v1/auth/me/` | GET | token | quem sou eu |
| `/api/v1/categories/` | GET | público | listar categorias |
| `/api/v1/categories/{id}/` | GET | público | categoria |
| `/api/v1/categories/` | POST/PATCH/PUT/DELETE | admin | gerenciar categorias |
| `/api/v1/items/` | GET | público | listar itens |
| `/api/v1/items/{id}/` | GET | público | item |
| `/api/v1/items/` | POST/PATCH/PUT/DELETE | admin | gerenciar itens |
| `/api/v1/pedidos/` | POST | token | criar pedido (idempotente) |
| `/api/v1/pedidos/{id}/` | GET | dono/admin | consultar pedido |
| `/api/v1/pedidos/{id}/pagamento/` | POST | dono/admin | pagamento simulado |

## 3. Convenções

* **Base**: tudo que é negócio fica sob `/api/v1/`. Rotas terminam com `/`
  (convenção Django).
* **Listagens** usam o envelope do DRF: `count`, `next`, `previous`, `results`.
  Paginação por `?page=<n>&limit=<1..100>` (padrão 20).
* **Filtros** de itens: `?search=`, `?is_active=true|false`, `?category=<id>`.
  Categorias: `?search=`. Lista de campos de busca no contrato (parameters).
* **Ordenação**: `?ordering=campo` (asc) ou `?ordering=-campo` (desc).
  Campos fora da whitelist são ignorados.
* **Valores monetários** são sempre **string** (ex.: `"79.90"`), nunca float.
* **Idempotência de pedido**: campo `idempotency_key` no **corpo** (não é
  header). Reenviar a mesma chave + mesmo usuário devolve o mesmo pedido
  (200, sem recriar).
* **Cache**: as leituras de itens e pedido respondem com `X-Cache: HIT`.
  Chamadas repetidas dentro do TTL não custam consulta nova.

## 4. Criar um pedido

```bash
curl -X POST http://localhost:8000/api/v1/pedidos/ \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"idempotency_key": "pedido-2026-0001",
       "itens": [{"item_id": 2, "quantidade": 2}]}'
```

Respostas:

| Status | Significado | `corpo` |
|---|---|---|
| 201 | criado e evento publicado | `pedido`, `evento_publicado: true` |
| 202 | criado, mas o broker recusou a publicação | `pedido`, `evento_publicado: false`, `detalhe` |
| 200 | `idempotency_key` repetida | mesmo `pedido`, `detalhe` explica |
| 400 | corpo inválido | `{"idempotency_key": ["..."]}` ou `{"itens": ["..."]}` |

`GET /api/v1/pedidos/{id}/` devolve o pedido com `status` (`criado` →
`confirmado` quando o worker consome o evento) e `itens` com nome/preço
congelados na compra.

## 5. Pagamento (simulado)

```bash
curl -X POST http://localhost:8000/api/v1/pedidos/1/pagamento/ \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"status": "APROVADO"}'
```

`status` aceita `APROVADO` (padrão, corpo vazio também vale) ou `RECUSADO`
(marca o pedido `cancelado` na mesma transação). Respostas: **201** (evento
publicado), **202** (persistiu, broker recusou), **409** (pedido **já tinha**
pagamento — o corpo traz o pagamento e o pedido existentes), **404** (pedido
inexistente ou alheio), **400** (status inválido).

## 6. Erros

| Status | Formato |
|---|---|
| 400 | `{"campo": ["mensagem", ...], ...}` (validação do DRF) |
| 401 | `{"detail": "..."}` — sem token, token inválido ou expirado |
| 403 | `{"detail": "..."}` — autenticado, sem permissão para a operação |
| 404 | `{"detail": "..."}` — recurso inexistente ou de outro dono |
| 409 | `{"detail": "...", "pagamento": {...}, "pedido": {...}}` |
| 429 | `{"detail": "..."}` + header `Retry-After` (segundos) |

**Throttling** (limites de requisição): anônimo 60/min por IP; usuário
autenticado 300/min; login 5/min por IP; escrita de catálogo (admin) 30/min.
Espere `Retry-After` após um 429.

## 7. Headers úteis

| Header | Onde | O quê |
|---|---|---|
| `Authorization: Bearer <token>` | chamadas autenticadas | JWT de acesso |
| `Content-Type: application/json` | POST/PATCH/PUT com corpo | corpo JSON |
| `X-Cache` | respostas de itens/pedido | `HIT`/`MISS`/`BYPASS` |
| `Retry-After` | respostas 429 | segundos de espera |

## 8. O que a API não faz (e o contrato diz isso)

* **`X-Trace-Id` não existe** no nível HTTP; correlação fica dentro dos
  eventos da mensageria. Não dependa dele para rastrear chamadas.
* **Paginação por cursor não existe**; use `page`/`limit`.
* **Listagem de pedidos e filtro `?status=` não existem**; a observabilidade
  de pedido é `GET /pedidos/{id}/`.
* **`sort=campo:ordem` não existe**; use `?ordering=`.
* **Erros não seguem RFC 7807** (`application/problem+json`); seguem o
  formato DRF descrito na seção 6.
* Pagamento é **simulação determinística** — nenhuma regra por valor.

Se o seu contrato for automatizado, impeça o CICD de aceitar versões que
adicionem endpoints desconhecidos (lint em `.spectral.yaml`):
`npx --yes @stoplight/spectral-cli lint docs/openapi.yaml`.