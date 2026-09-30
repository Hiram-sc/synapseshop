"""Configuração da conexão do microsserviço de inventário com o PostgreSQL.

Concentra a leitura das variáveis de ambiente, a criação da engine do
SQLAlchemy, a base declarativa e a fábrica de sessões. Nenhuma regra de
negócio e nenhuma consulta vivem neste módulo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# variáveis obrigatórias para montar a URL de conexão
VARIAVEIS_OBRIGATORIAS = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")


class Base(DeclarativeBase):
    """Base declarativa compartilhada pelos modelos do microsserviço."""


@dataclass(frozen=True)
class DatabaseSettings:
    """Parâmetros de conexão lidos do ambiente.

    A senha fica fora do `repr` para não vazar em logs ou tracebacks.
    """

    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        """Monta as configurações a partir das variáveis de ambiente.

        Falha de forma explícita quando alguma variável obrigatória não está
        presente: uma conexão silenciosa com valor padrão apenas adia o
        erro para um momento mais difícil de diagnosticar.
        """
        ausentes = [nome for nome in VARIAVEIS_OBRIGATORIAS if nome not in os.environ]
        if ausentes:
            raise RuntimeError(
                "Variáveis de ambiente obrigatórias ausentes: "
                + ", ".join(ausentes)
                + ". Configure-as no serviço do Docker Compose antes de iniciar."
            )
        return cls(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            database=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
        )

    @property
    def url(self) -> str:
        """URL de conexão no formato aceito pelo SQLAlchemy."""
        return (
            f"postgresql+psycopg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    def __str__(self) -> str:
        return f"postgresql+psycopg://{self.user}@{self.host}:{self.port}/{self.database}"


def create_db_engine() -> Engine:
    """Cria a engine do SQLAlchemy a partir das variáveis de ambiente."""
    configuracoes = DatabaseSettings.from_env()
    return create_engine(configuracoes.url, pool_pre_ping=True)


engine: Engine = create_db_engine()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Dependency do FastAPI: devolve uma sessão por requisição.

    A sessão é sempre fechada ao final da requisição, inclusive quando a
    rota levanta exceção.
    """
    sessao = SessionLocal()
    try:
        yield sessao
    finally:
        sessao.close()
