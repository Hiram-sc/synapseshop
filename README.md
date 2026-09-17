## 📋 Sobre o projeto

O SynapseShop possui, nesta etapa, uma API simples de **Health Check**, responsável por verificar se a aplicação está disponível.

A aplicação foi desenvolvida em Python utilizando o módulo nativo `http.server` e executada dentro de um container Docker.

O ambiente também possui um container separado para o banco de dados PostgreSQL, permitindo que a aplicação e o banco sejam executados de forma independente e organizada.

## 🛠️ Tecnologias utilizadas

- Python 3.12
- Docker
- Docker Compose
- PostgreSQL 16
- `http.server`
- `pip`

## 📁 Estrutura do projeto

```text
aula2/
├── api/
│   └── health.py
├── .dockerignore
├── .gitignore
├── docker-compose.yml
├── Dockerfile
├── README.md
└── requirements.txt

