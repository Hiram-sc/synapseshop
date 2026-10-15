"""Gera o contrato OpenAPI versionado em `docs/openapi.yaml` (Aula 13).

Equivale a `python manage.py spectacular --file docs/openapi.yaml`, mas com o
caminho de saída padrão do projeto, para que a regeneração seja um comando só:

    python manage.py gerar_openapi

O arquivo é derivado do código (serializers/views + hooks em `api.openapi`) e
deve ser commitado junto com as mudanças de contrato.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema

#: caminho padrão, relativo à raiz do projeto
SAIDA_PADRAO = "docs/openapi.yaml"


class Command(BaseCommand):
    help = "Gera o contrato OpenAPI da API em docs/openapi.yaml."

    def add_arguments(self, parser):
        parser.add_argument(
            "--saida",
            default=SAIDA_PADRAO,
            help=f"Caminho do arquivo de saída (padrão: {SAIDA_PADRAO}).",
        )
        parser.add_argument(
            "--sem-validar",
            action="store_true",
            help="Pula a validação do schema (por padrão ele é validado).",
        )

    def handle(self, *args, **options):
        generator = SchemaGenerator()
        schema = generator.get_schema(request=None, public=True)

        if not options["sem_validar"]:
            validate_schema(schema)

        destino = Path(settings.BASE_DIR) / options["saida"]
        destino.parent.mkdir(parents=True, exist_ok=True)
        with destino.open("w", encoding="utf-8") as arquivo:
            yaml.safe_dump(
                schema,
                arquivo,
                sort_keys=False,
                allow_unicode=True,
            )

        self.stdout.write(
            self.style.SUCCESS(f"Contrato OpenAPI gerado em {destino}")
        )
