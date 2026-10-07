"""Remove registros antigos de idempotência do PostgreSQL.

    python manage.py limpar_eventos_processados --dias 7

O TTL do Redis expira sozinho a chave do caminho rápido, mas **não** apaga a
linha durável em `EventoProcessado` - sem esta limpeza, a tabela cresceria
para sempre. Padrão de 7 dias: muito acima do maior atraso plausível de
reentrega (o backoff da Aula 9 mede segundos), sem apagar cedo demais algo
que ainda pode ser consultado por auditoria.

Execução idempotente: rodar de novo só encontra menos linhas.
"""

from __future__ import annotations

from typing import Any, Dict

from django.core.management.base import BaseCommand

from services.mensageria.idempotencia import limpar_registros


class Command(BaseCommand):
    help = "Apaga EventoProcessado mais antigos que N dias (TTL do Redis não cobre o banco)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dias",
            type=int,
            default=7,
            help="Idade mínima, em dias, para o registro ser removido (padrão: 7).",
        )

    def handle(self, *args: Any, **options: Dict[str, Any]) -> None:
        dias = options["dias"]
        if dias < 1:
            self.stderr.write("--dias deve ser maior que zero.")
            return

        removidos = limpar_registros(dias)
        self.stdout.write(
            self.style.SUCCESS(
                f"{removidos} registro(s) de EventoProcessado com mais de {dias} dia(s) removido(s)"
            )
        )
