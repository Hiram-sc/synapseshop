import time
from unittest.mock import patch

import pytest
from django.conf import settings

from services.llm_service import (
    CircuitBreaker,
    CircuitBreakerError,
    LLMService,
    obter_llm_service,
)


def test_circuit_breaker_abre():
    cb = CircuitBreaker(limite_falhas=2, reset_s=10)
    assert cb.pode_chamar()
    cb.registrar_falha()
    assert cb.pode_chamar()
    cb.registrar_falha()
    assert not cb.pode_chamar()


def test_circuit_breaker_reseta():
    cb = CircuitBreaker(limite_falhas=1, reset_s=0.01)
    cb.registrar_falha()
    assert not cb.pode_chamar()
    time.sleep(0.02)
    assert cb.pode_chamar()


def test_chat_completions_sucesso(monkeypatch):
    svc = LLMService()
    mock_resp = {
        "choices": [{"message": {"content": "resposta"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }
    with patch.object(svc, "_chamar_api", return_value=mock_resp):
        res = svc.chat_completions(svc._montar_payload([{"role": "user", "content": "x"}], 0.2, 50))
    assert res["answer"] == "resposta"
    assert res["meta"]["tokens_prompt"] == 10
    assert res["meta"]["tokens_output"] == 20


def test_chat_completions_timeout_retry_falha(monkeypatch):
    svc = LLMService()
    svc.max_tentativas = 2
    with patch.object(svc, "_chamar_api", side_effect=TimeoutError("timeout")):
        with pytest.raises(TimeoutError):
            svc.chat_completions(svc._montar_payload([{"role": "u", "content": "x"}], 0.2, 10))


def test_chat_completions_429_nao_retry_ou_falha(monkeypatch):
    svc = LLMService()
    svc.max_tentativas = 3
    with patch.object(svc, "_chamar_api", side_effect=Exception("http_429:rate")):
        with pytest.raises(Exception) as exc:
            svc.chat_completions(svc._montar_payload([{"role": "u", "content": "x"}], 0.2, 10))
        assert "http_429" in str(exc.value)


def test_chat_completions_500_retry(monkeypatch):
    svc = LLMService()
    svc.max_tentativas = 2
    with patch.object(svc, "_chamar_api", side_effect=[Exception("http_500:error"), {"choices": [{"message": {"content": "ok"}}], "usage": {}}]):
        res = svc.chat_completions(svc._montar_payload([{"role": "u", "content": "x"}], 0.2, 10))
    assert res["answer"] == "ok"


def test_chat_completions_400_noretry(monkeypatch):
    svc = LLMService()
    svc.max_tentativas = 3
    with patch.object(svc, "_chamar_api", side_effect=Exception("http_400:bad")):
        with pytest.raises(Exception):
            svc.chat_completions(svc._montar_payload([{"role": "u", "content": "x"}], 0.2, 10))


def test_circuit_aberto_bloqueia(monkeypatch):
    svc = LLMService()
    svc.circuit.limite_falhas = 1
    svc.circuit.registrar_falha()
    assert not svc.circuit.pode_chamar()
    with pytest.raises(CircuitBreakerError):
        svc.chat_completions(svc._montar_payload([{"role": "u", "content": "x"}], 0.2, 10))


def test_resposta_invalida(monkeypatch):
    svc = LLMService()
    with patch.object(svc, "_chamar_api", return_value={"nao": "valido"}):
        res = svc.chat_completions(svc._montar_payload([{"role": "u", "content": "x"}], 0.2, 10))
    # choices vazio -> answer vazio
    assert res["answer"] == ""


def test_circuit_breaker_half_open_fecha_com_sucesso():
    cb = CircuitBreaker(limite_falhas=1, reset_s=0.01)
    cb.registrar_falha()
    assert cb.estado == CircuitBreaker.ABERTO
    assert not cb.pode_chamar()
    time.sleep(0.02)
    # vencido o reset, o breaker entra em half-open e libera uma sonda
    assert cb.pode_chamar()
    assert cb.estado == CircuitBreaker.MEIO_ABERTO
    # a sonda deu certo: volta a fechar
    cb.registrar_sucesso()
    assert cb.estado == CircuitBreaker.FECHADO
    assert cb.pode_chamar()


def test_circuit_breaker_half_open_reabre_com_falha():
    cb = CircuitBreaker(limite_falhas=2, reset_s=0.01)
    cb.registrar_falha()
    cb.registrar_falha()
    assert cb.estado == CircuitBreaker.ABERTO
    time.sleep(0.02)
    assert cb.pode_chamar()
    assert cb.estado == CircuitBreaker.MEIO_ABERTO
    # a sonda falhou: reabre imediatamente, sem esperar acumular falhas
    cb.registrar_falha()
    assert cb.estado == CircuitBreaker.ABERTO
    assert not cb.pode_chamar()


def test_half_open_libera_uma_unica_sonda():
    cb = CircuitBreaker(limite_falhas=1, reset_s=0.01)
    cb.registrar_falha()
    time.sleep(0.02)
    assert cb.pode_chamar()  # sonda
    assert not cb.pode_chamar()  # segunda chamada enquanto a sonda não resolve


def test_obter_llm_service_reutiliza_instancia(monkeypatch):
    from services import llm_service

    monkeypatch.setattr(llm_service, "_instancia_padrao", None)
    primeiro = obter_llm_service()
    segundo = obter_llm_service()
    assert primeiro is segundo


def test_assinar_e_resumir_redige_antes_de_enviar(monkeypatch):
    svc = LLMService()
    capturado = {}

    def fake_chamar_api(payload):
        capturado.update(payload)
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(svc, "_chamar_api", fake_chamar_api)
    svc.assinar_e_resumir(
        "summarize",
        "senha=supersegredo cpf 123.456.789-01 email vazamento@exemplo.com",
    )
    enviado = capturado["messages"][-1]["content"]
    assert "supersegredo" not in enviado
    assert "123.456.789-01" not in enviado
    assert "vazamento@exemplo.com" not in enviado
    assert "[REDACTED]" in enviado


def test_assinar_e_resumir_trunca_logs_ao_limite(monkeypatch):
    svc = LLMService()
    svc.max_logs_chars = 20
    capturado = {}
    monkeypatch.setattr(
        svc,
        "_chamar_api",
        lambda payload: (
            capturado.update(payload)
            or {"choices": [{"message": {"content": "ok"}}], "usage": {}}
        ),
    )
    svc.assinar_e_resumir("explain", "A" * 500)
    enviado = capturado["messages"][-1]["content"]
    assert "A" * 500 not in enviado
    assert "A" * 21 not in enviado
