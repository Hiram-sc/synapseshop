# SynapseShop — Visão Geral do Projeto & Arquitetura-Alvo
**Curso de Programação Python Avançada com IA** · 100 horas · 25 aulas  
**Empresa Simulada:** SynapseTech  

---

## 1. Contexto do Projeto e Objetivo
Ao longo das 25 aulas deste curso, os alunos vivenciam um processo seletivo prático para a vaga de **Programador(a) Backend com IA** na **SynapseTech** — uma empresa fictícia especializada no desenvolvimento de produtos web integrados com inteligência artificial (assistentes, automação, recomendação e análise preditiva)[cite: 1].

O desafio central das equipes é construir, de forma incremental utilizando **SpecDD (Specification-Driven Development)**, o MVP de um backend de pedidos completo, containerizado, testado, documentado e com funcionalidades reais de IA[cite: 1].

---

## 2. O que é o SynapseShop
O **SynapseShop** é o backend de uma loja online voltada à gestão de produtos, pedidos, pagamentos simulados e notificações. Sobre essa base transacional robusta, opera uma camada inteligente composta por três pilares de IA:
1. **Assistente de Suporte (`/assist`):** Responde dúvidas de clientes sobre pedidos e status utilizando um serviço de IA resiliente (com estratégias de *retry* e *circuit breaker*)[cite: 1].
2. **Consulta em Linguagem Natural (`/ask_sql`):** Permite realizar perguntas de negócio em texto corrido (ex.: *"quais os produtos mais vendidos este mês?"*), convertendo-as dinamicamente em consultas SQL e retornando o resultado[cite: 1].
3. **Assistente de Documentação via RAG (`/ask_docs`):** Responde a questionamentos com base no próprio `README` e na documentação técnica da API, garantindo sempre a citação rigorosa das fontes[cite: 1].

*(Nota: O domínio de "loja online" pode ser adaptado por cada squad — ex: livraria, e-commerce de eletrônicos —, desde que preserve os requisitos técnicos obrigatórios)[cite: 1].*

---

## 3. Arquitetura-Alvo em 6 Camadas
A infraestrutura do sistema é projetada em 6 camadas lógicas, conteinerizadas via Docker e orquestradas com Docker Compose[cite: 1]:

1. **Clientes & Canais de Acesso:** Loja Web/Mobile, Painel do Administrador e o Canal de Suporte com IA.
2. **Gateway de API & Autenticação:** Django REST Framework (DRF) para a API principal, FastAPI para microsserviços, controle de acesso via JWT com papéis (Roles) e *throttling*.
3. **Serviços de Negócio:** 
   * *Order Service* (criação de pedidos e publicação de eventos)[cite: 1].
   * *Payment Service* (pagamento simulado)[cite: 1].
   * *Inventory Service* (microsserviço em FastAPI para estoque e produtos)[cite: 1].
   * *Notification Service* (consumidor de eventos e disparos)[cite: 1].
4. **Camada de Inteligência Artificial:** Serviços de Assistant, NL-to-SQL e RAG integrados a provedores de LLM externos (compatíveis com OpenAI/DeepSeek)[cite: 1].
5. **Infraestrutura de Dados & Mensageria:** 
   * PostgreSQL (dados relacionais + migrações via SQLAlchemy/Alembic)[cite: 1].
   * Redis (cache-aside para catálogo e pedidos)[cite: 1].
   * RabbitMQ / Kafka (eventos de pedido criado, garantia de idempotência e Dead Letter Queue - DLQ)[cite: 1].
6. **Observabilidade, Qualidade & Entrega:** Dashboards gerados em Streamlit, suíte de testes automatizados com `pytest`, documentação OpenAPI/Swagger e pipeline de CI/CD com GitHub Actions[cite: 1].

---

## 4. Principais Funcionalidades do MVP (Definition of Done Global)
O projeto só é considerado concluído quando atende aos seguintes critérios arquiteturais e funcionais:
* Sobe por completo utilizando um único comando (`docker-compose up`)[cite: 1].
* Autenticação JWT funcional com pelo menos dois papéis de usuário distintos[cite: 1].
* Fluxo ponta a ponta estruturado: `Pedido` ➔ `Pagamento` ➔ `Notificação`, operando com mensageria assíncrona, idempotência e DLQ[cite: 1].
* Cobertura de testes automatizados dentro da meta estipulada pela turma[cite: 1].
* Documentação OpenAPI/Swagger publicada e coleções Postman/Insomnia disponíveis[cite: 1].
* Pelo menos uma funcionalidade de IA operacional (assistente, texto-para-SQL ou RAG)[cite: 1].
* Pipeline de CI/CD executando *builds* e testes automatizados a cada push[cite: 1].
* Dashboard analítico exibindo métricas de negócio ou desempenho da API[cite: 1].
* Documentação completa em Markdown na raiz, incluindo o histórico obrigatório de prompts de IA no arquivo `PROMPTS.md`[cite: 1].

---

## 5. Trilha de Entregas (Roadmap de 25 Aulas)

| Bloco de Aulas | Entrega / Marco no Projeto |
| :--- | :--- |
| **Aula 1** | Apresentação da situação de aprendizagem, formação dos times e criação do repositório[cite: 1]. |
| **Aulas 2–3** | Esqueleto do projeto em camadas (API/Service/Repository) e conteinerização inicial com Docker[cite: 1]. |
| **Aula 4** | Implementação do CRUD da API principal utilizando Django REST Framework[cite: 1]. |
| **Aula 5** | Desenvolvimento do microsserviço complementar de estoque (`inventory`) em FastAPI[cite: 1]. |
| **Aula 6** | Modelagem relacional do banco de dados, definição de índices e migrações (Alembic)[cite: 1]. |
| **Aula 7** | Implementação de autenticação JWT baseada em papéis (roles) e regras de *throttling*[cite: 1]. |
| **Aula 8** | Aplicação de cache-aside com Redis para otimização do catálogo e pedidos[cite: 1]. |
| **Aulas 9–11** | Mensageria assíncrona com RabbitMQ/Kafka: fluxo de pagamentos e notificações com idempotência e DLQ[cite: 1]. |
| **Aulas 12–13** | Construção da suíte de testes automatizados (`pytest`) e documentação da API (Swagger/Postman)[cite: 1]. |
| **Aulas 14–15** | Integração do serviço de IA (`llm_service`) e aplicação de técnicas avançadas de Engenharia de Prompt[cite: 1]. |
| **Aulas 16–18** | Desafio em grupo: consolidação do sistema completo e customização do domínio escolhido[cite: 1]. |
| **Aulas 19–20** | Desenvolvimento de dashboards gerenciais em Streamlit (vendas e performance da API)[cite: 1]. |
| **Aula 21** | Implementação da consulta de dados de negócio em linguagem natural (Texto-para-SQL)[cite: 1]. |
| **Aulas 22–23** | Configuração do pipeline de Integração e Entrega Contínua (CI/CD) com GitHub Actions[cite: 1]. |
| **Aula 24** | Implementação do assistente de documentação via RAG, com indexação e citação de fontes[cite: 1]. |
| **Aula 25** | Apresentação final e defesa do MVP entregue por cada squad[cite: 1]. |

---

## 6. Stack Tecnológica de Referência
* **Linguagem & POO:** Python (Programação Orientada a Objetos avançada)[cite: 1].
* **Frameworks Web:** Django REST Framework (DRF) & FastAPI[cite: 1].
* **Banco de Dados & Migrações:** PostgreSQL, SQLAlchemy, Alembic[cite: 1].
* **Infraestrutura & Mensageria:** Docker, Docker Compose, Redis, RabbitMQ / Kafka[cite: 1].
* **Inteligência Artificial:** LLM via API externa (OpenAI / DeepSeek), Prompt Engineering, RAG (Embeddings + Vector Index)[cite: 1].
* **Qualidade, Testes & Docs:** Pytest, OpenAPI/Swagger, Postman/Insomnia[cite: 1].
* **Observabilidade & DevOps:** Streamlit, GitHub Actions, Git, Controle de Prompts (`PROMPTS.md`)[cite: 1].