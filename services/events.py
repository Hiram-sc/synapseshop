"""Barramento de eventos em processo.

A aula pede que a escrita do catálogo **notifique** as partes da
aplicação em vez de a view chamar a invalidação direto. Assim, quando uma
segunda coisa precisar reagir a uma escrita (mandar e-mail, atualizar o
índice de busca, registrar métrica), ela se inscreve em um evento e a view não
muda.

Duas limitações são deliberadas, e é bom saber quais são:

* **Não há broker.** A entrega é síncrona, no mesmo thread e no mesmo processo
  que atendeu a requisição. Isso basta para a topologia atual (uma instância da
  API) e evita introduzir Redis Streams, RabbitMQ ou Celery antes da hora. Se
  um dia os assinantes ficarem lentos ou precisarem de entrega garantida, o
  lugar de trocar não é este módulo: é o `publicar`.
* **Um assinante que quebra não derruba os outros nem a requisição.** Um erro é
  registrado e a entrega continua. Perder uma invalidação é ruim; devolver 500
  para o cliente depois de gravar o item no banco é pior - o cliente repetiria a
  escrita e criaria duplicata.

Os nomes dos eventos ficam em constantes: eles são o contrato entre quem
publica e quem escuta, e são escritos com a notação `entidade.ação`.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

# --- eventos do catálogo ---------------------------------------------------
ITEM_CRIADO = "item.criado"
ITEM_ATUALIZADO = "item.atualizado"
ITEM_REMOVIDO = "item.removido"
CATEGORIA_CRIADA = "categoria.criada"
CATEGORIA_ATUALIZADA = "categoria.atualizada"
CATEGORIA_REMOVIDA = "categoria.removida"


@dataclass(frozen=True)
class Evento:
    """O que foi publicado: um nome e um dicionário de dados.

    `dados` é um `dict` simples (e não uma instância de modelo) porque quem
    assina não deve poder chegar ao banco por acaso através do evento - ele só
    precisa dos ids para saber o que invalidar.
    """

    nome: str
    dados: Dict[str, Any] = field(default_factory=dict)


Assinante = Callable[[Evento], None]


class Barramento:
    """Registro de assinantes por nome de evento.

    Um `Lock` protege a lista porque o servidor Django pode atender requisições
    em várias threads, e `publicar` acontece dentro da requisição de escrita.
    """

    def __init__(self) -> None:
        self._assinantes: Dict[str, List[Assinante]] = {}
        self._lock = threading.Lock()

    def inscrever(self, nome: str, assinante: Assinante) -> None:
        with self._lock:
            self._assinantes.setdefault(nome, []).append(assinante)

    def publicar(self, evento: Evento) -> None:
        """Entrega o evento a todos os assinantes, isolando falhas."""
        with self._lock:
            assinantes = list(self._assinantes.get(evento.nome, ()))

        for assinante in assinantes:
            try:
                assinante(evento)
            except Exception:
                # a falha de um assinante não pode virar 500 depois da escrita
                logger.exception(
                    "Assinante %s falhou ao tratar o evento %s.",
                    getattr(assinante, "__qualname__", assinante),
                    evento.nome,
                )

    def assinar_de(self, nome: str) -> List[Assinante]:
        """Assinantes registrados para um evento (cópia da lista)."""
        with self._lock:
            return list(self._assinantes.get(nome, ()))

    def cancelar(self, nome: str, assinante: Assinante) -> None:
        """Remove uma inscrição específica.

        Existe para os testes: um `limpar()` geral derrubaria junto as regras de
        invalidação do cache, que foram registradas no boot do processo, e
        depois disso nenhuma escrita invalidaria nada mais.
        """
        with self._lock:
            try:
                self._assinantes.get(nome, []).remove(assinante)
            except ValueError:
                pass  # não estava inscrito; nada a fazer


#: barramento único do processo
barramento = Barramento()


def publicar(nome: str, **dados: Any) -> None:
    """Publica um evento do catálogo."""
    barramento.publicar(Evento(nome=nome, dados=dados))


def inscrever(nome: str, assinante: Assinante) -> None:
    """Registra um assinante para um evento do catálogo."""
    barramento.inscrever(nome, assinante)


def cancelar(nome: str, assinante: Assinante) -> None:
    """Cancela a inscrição de um assinante."""
    barramento.cancelar(nome, assinante)
