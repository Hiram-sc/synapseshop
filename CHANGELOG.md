# Changelog

Histórico de versões do SynapseShop. As mudanças seguem o fluxo incremental do
SpecDD (uma aula por pedaço de especificação) e são registradas a partir da
Aula 13; o histórico anterior (Aulas 1-12) está resumido na seção final, com
referência à documentação técnica.

## [1.0.0] - 2026-10-09

### Adicionado (Aula 13 - Documentação da API)

- Contrato **OpenAPI 3.0.3 gerado do código** com `drf-spectacular`
  (`docs/openapi.yaml`), versionado e regenerável por
  `python manage.py gerar_openapi`.
- Rotas de documentação em `config/urls.py`: `/api/schema/` (contrato),
  `/api/docs/` (Swagger UI) e `/api/redoc/` (ReDoc), com assets servidos pela
  própria aplicação via `drf-spectacular-sidecar` (sem CDN externa).
- Anotação de todas as views com `@extend_schema`/`@extend_schema_view`:
  resumos, tags, request/response explícitos (login, pedido, pagamento, 409)
  e permissões (`bearerAuth` vs. público).
- Hook de pós-processamento (`api/openapi.py`): respostas de erro padrão
  (401/403/429) em toda operação e inclusão de `/health` e `/health/pronto`
  (views Django puras) no contrato.
- `ErrorSerializer` e serializers de resposta composta em `api/serializers.py`
  (sem participar do fluxo de negócio).
- `ENUM_NAME_OVERRIDES` para enums `PedidoStatus`/`PagamentoStatus` sem nome
  colidido no schema.
- Validação estática com **Spectral** (`.spectral.yaml` estendendo
  `spectral:oas`): lint do contrato com **0 problemas**.
- Coleção Postman expandida para 52 requisições em 8 pastas, incluindo
  `Saude e Documentacao` e `Pedidos (Aula 13)`; novo environment
  `SynapseShop.postman_environment.json`.
- Guia do integrador externo (`docs/INTEGRADOR_EXTERNO.md`) e documentação da
  aula (`docs/AULA_13_OPENAPI_SWAGGER_POSTMAN.md`).
- Testes de contrato (`tests/integration/test_contrato_openapi.py`): schema
  servido, UIs, componentes e segurança declarada.
- Comando de teste no README corrigido para `pytest` (a suíte usa testes em
  função, que `manage.py test` não executa), e a menção a `/docs` removida em
  favor das rotas reais `/api/docs/` + `/api/redoc/` + `/api/schema/`.

### Observado (sem mudança de comportamento)

- Divergências entre a spec de contrato e a API real registradas (idempotência
  no corpo vs. header, ausência de `X-Trace-Id`, paginação `page/limit` vs.
  cursor, `?ordering=` vs. `sort=`, erros DRF vs. RFC 7807) — evolução futura,
  fora do escopo da Aula 13.

## Histórico anterior (Aulas 1-12)

Antes da Aula 13 o repositório não mantinha changelog; o resumo abaixo reflete
o estado entregue ao final da Aula 12.

- **Aulas 1-5 (fundação):** diretriz SpecDD (`specs/00_diretriz_*`), specs das
  lições e do projeto (`specs/specs_da_aula_*`), setup da API Django REST.
- **Aula 6 (persistência):** PostgreSQL, migrations e `inventory/` separado.
  -> `docs/AULA_06_PERSISTENCIA_POSTGRESQL.md`
- **Aula 7 (JWT/papéis/throttling):** login JWT, roles, limites de
  requisição, paginação e filtros. -> `docs/AULA_07_JWT_ROLES_THROTTLING.md`
- **Aula 8 (cache):** cache-aside com Redis, invalidação por eventos e
  fail-open. -> `docs/AULA_08_CACHE_ASIDE_REDIS.md`
- **Aula 9 (mensageria):** `PedidoCriado` por RabbitMQ, idempotência, retry
  com backoff e DLQ. -> `docs/AULA_09_MENSAGERIA_ASSINCRONA.md`
- **Aula 10 (Kafka):** Apache Kafka KRaft como broker padrão (RabbitMQ
  preservado), offsets por grupo, retenção e DLQ. ->
  `docs/AULA_10_MENSAGERIA_APACHE_KAFKA.md`
- **Aula 11 (fluxo pagamento/notificação):** `PagamentoProcessado`,
  `notificacao-worker`, `NotificacaoEnviada`, cancelamento por rejeição,
  readiness `/health/pronto` e cache-aside de pedido (documentado no README).
- **Aula 12 (testes):** suíte `pytest` + cobertura contra PostgreSQL/Redis
  reais (`pytest.ini`, `tests/`).