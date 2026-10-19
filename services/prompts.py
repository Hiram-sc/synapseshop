from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class PromptTemplates:
    summarize_system = (
        "Você é um assistente técnico. Resuma logs em português do Brasil de forma "
        "concisa, objetiva e sem inventar fatos. Cite apenas informações presentes "
        "nos logs fornecidos."
    )
    summarize_user = (
        "Resuma os logs a seguir, destacando erros, avisos e pontos críticos. "
        "Mantenha a resposta em português do Brasil.\n\nLogs:\n{logs}"
    )
    explain_system = (
        "Você é um assistente técnico. Explique o que aconteceu com base nos logs "
        "fornecidos, de forma clara e objetiva. Não invente eventos não presentes "
        "nos logs."
    )
    explain_user = (
        "Explique os eventos abaixo em português do Brasil, indicando causas "
        "prováveis com base apenas nos dados fornecidos. Destaque erros e "
        "recomendações práticas.\n\nLogs:\n{logs}"
    )


def serializar_logs(logs: Any) -> str:
    """Normaliza `logs` (string, lista de strings/dicts ou qualquer outro) em texto.

    É o ponto único de conversão usado por `montar_prompt` e pelo `llm_service`;
    aceitar `str` e `list` é o contrato do endpoint `/api/v1/assist`.
    """
    if logs is None:
        return ""
    if isinstance(logs, str):
        return logs
    if isinstance(logs, (list, tuple)):
        linhas: List[str] = []
        for item in logs:
            if isinstance(item, dict):
                try:
                    partes = []
                    for chave in ("timestamp", "level", "message", "msg"):
                        if chave in item and item[chave] is not None:
                            partes.append(f"{chave}={item[chave]}")
                    if not partes and item:
                        partes = [f"{k}={v}" for k, v in item.items()]
                    linhas.append(" ".join(str(p) for p in partes) if partes else str(item))
                except Exception:
                    linhas.append(str(item))
            else:
                linhas.append(str(item))
        return "\n".join(linhas)
    return str(logs)


def montar_prompt(mode: str, logs: Any) -> Dict[str, Any]:
    templates = PromptTemplates()
    modo = mode.lower() if isinstance(mode, str) else ""
    logs_str = serializar_logs(logs)

    if modo == "summarize":
        system = templates.summarize_system
        user = templates.summarize_user.format(logs=logs_str)
    elif modo == "explain":
        system = templates.explain_system
        user = templates.explain_user.format(logs=logs_str)
    else:
        system = templates.explain_system
        user = templates.explain_user.format(logs=logs_str)

    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "system_prompt": system,
        "user_prompt": user,
    }
