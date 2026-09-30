"""Coleta simples de tempos das operações transacionais da Aula 6.

Mede inserção, consulta por SKU e atualização com `time.perf_counter`,
repetindo cada operação para separar o custo fixo de rede do custo da
operação. É uma medição didática, não observabilidade: não há métricas,
traças ou instrumentação de produção aqui.

Execução, de dentro da rede do Docker Compose:

    docker compose run --rm inventory python scripts/measure_times.py
"""

from __future__ import annotations

import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List

# permite executar o script direto, sem instalar o pacote
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.schemas import InventoryItemCreate, InventoryItemUpdate  # noqa: E402
from app.storage import InventoryStore  # noqa: E402

INSERCOES = 50
CONSULTAS_POR_SKU = 200
ATUALIZACOES = 50


@dataclass(frozen=True)
class Resultado:
    """Tempos de uma operação, em milissegundos."""

    operacao: str
    execucoes: int
    media: float
    minima: float
    maxima: float

    def como_linha(self) -> str:
        return (
            f"| {self.operacao} | {self.execucoes} | {self.media:.3f} | "
            f"{self.minima:.3f} | {self.maxima:.3f} |"
        )


def medir(operacao: str, execucoes: int, acao: Callable[[int], None]) -> Resultado:
    """Executa `acao` N vezes e devolve os tempos em milissegundos."""
    amostras: List[float] = []
    for i in range(execucoes):
        inicio = time.perf_counter()
        acao(i)
        amostras.append((time.perf_counter() - inicio) * 1000)
    return Resultado(
        operacao=operacao,
        execucoes=execucoes,
        media=statistics.fmean(amostras),
        minima=min(amostras),
        maxima=max(amostras),
    )


def main() -> None:
    """Executa as três medições e imprime o resultado em tabela Markdown."""
    sessao = SessionLocal()
    store = InventoryStore(sessao)

    try:
        # aquece o pool de conexões para não medir a abertura do TCP
        store.create_item(InventoryItemCreate(sku="WARMUP", name="Aquecimento", quantity=0))
        store.delete_item(1)

        def inserir(i: int) -> None:
            store.create_item(
                InventoryItemCreate(sku=f"BENCH-{i:04d}", name=f"Item {i}", quantity=i)
            )

        skus = [f"BENCH-{i:04d}" for i in range(INSERCOES)]

        def consultar(_: int) -> None:
            store.get_item_by_sku(skus[0])

        def atualizar(i: int) -> None:
            store.update_item(
                i + 1, InventoryItemUpdate(quantity=i + 1)
            )

        resultados = [
            medir("inserção", INSERCOES, inserir),
            medir("consulta por SKU", CONSULTAS_POR_SKU, consultar),
            medir("atualização", ATUALIZACOES, atualizar),
        ]

        print("| Operação | Execuções | Média (ms) | Mínima (ms) | Máxima (ms) |")
        print("| --- | --- | --- | --- | --- |")
        for resultado in resultados:
            print(resultado.como_linha())

        # limpa os dados criados pela medição
        for i in range(INSERCOES):
            store.delete_item(i + 1)
    finally:
        sessao.close()


if __name__ == "__main__":
    main()
