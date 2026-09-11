# Spec: Esqueleto do Projeto e Conteinerização (Aula 2)

## 1. Objetivo
Estruturar o esqueleto inicial do projeto em camadas lógicas e preparar o ambiente de conteinerização. O sistema deve estar pronto para rodar isoladamente antes da implementação de qualquer framework web ou banco de dados.

## 2. Contexto
Com o repositório criado na Aula 1, a equipe agora deve traduzir o design arquitetural para o sistema de arquivos. O foco é a infraestrutura de desenvolvimento (Docker). **Importante:** Não configurem o Django REST Framework (Aula 4), FastAPI (Aula 5) ou PostgreSQL (Aula 6) neste momento[cite: 1].

## 3. Requisitos Arquiteturais (Esqueleto)
A estrutura de pastas principal deve refletir a separação de responsabilidades. Crie, na raiz do repositório, a seguinte estrutura de diretórios baseados nas camadas[cite: 1]:
- `api/` (Destinada aos futuros entrypoints e roteamento)
- `services/` (Destinada à lógica de negócio e regras de domínio)
- `repositories/` (Destinada ao acesso a dados e infraestrutura)

## 4. Requisitos de Infraestrutura (Containers)
O projeto deve ser conteinerizado utilizando Docker e Docker Compose[cite: 1].
- [ ] Criar um `Dockerfile` na raiz do projeto utilizando uma imagem oficial do Python.
- [ ] Criar um arquivo `docker-compose.yml` na raiz do projeto.
- [ ] O serviço inicial no `docker-compose.yml` deve iniciar um container Python simples (pode ser executando um script *dummy* provisório de "Hello World" ou mantendo o container em loop/idle) apenas para validar o funcionamento.

## 5. Requisitos de Entrega (Definition of Done)
- [ ] A estrutura de diretórios (`api/`, `services/`, `repositories/`) foi criada e versionada (usar `.gitkeep` em pastas vazias, se necessário)[cite: 1].
- [ ] Os arquivos `Dockerfile` e `docker-compose.yml` foram configurados corretamente[cite: 1].
- [ ] É possível levantar o ambiente completo utilizando **exclusivamente** o comando `docker-compose up` no terminal[cite: 1].
- [ ] O código gerado foi commitado e "pushado" (pushed) para o repositório central no GitHub.