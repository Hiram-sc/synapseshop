# etapa de preparação das dependências
FROM python:3.12-slim AS builder

# local das dependências
WORKDIR /deps

# copia somente o arquivo das dependências 
COPY requirements.txt .

# gera o wheels com as dependências - pip wheel
RUN pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt

# etapa de execução da aplicação
FROM python:3.12-slim AS runtime

# local da aplicação
WORKDIR /app

# copia os pacotes que foram preparados no builder para serem instalados
COPY --from=builder /wheels /wheels

# copia o arquivo de dependências para o runtime
COPY requirements.txt .

# instala as dependências a partir dos wheels
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# criação do usuário não-root chamado appuser
RUN useradd --create-home appuser

# copia os arquivos da aplicação para o diretório /app do container
COPY . .

# altera o proprietário dos arquivos/diretórios. dá acesso a aplicação app
RUN chown -R appuser:appuser /app

# diz para o docker utilizar esse usuário
USER appuser

EXPOSE 8000

CMD ["python", "api/health.py"]