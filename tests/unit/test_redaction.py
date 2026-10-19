import pytest

from services import redaction


def test_redigir_chaves_sensiveis():
    dados = {"password": "123", "api_key": "abc", "token": "t"}
    r = redaction.redigir_dicionario(dados)
    assert r["password"] == "[REDACTED]"
    assert r["api_key"] == "[REDACTED]"
    assert r["token"] == "[REDACTED]"


def test_redigir_texto_pii():
    texto = "cpf 12345678901 email teste@exemplo.com"
    r = redaction.redigir_texto(texto)
    assert "[REDACTED]" in r


def test_truncar_texto():
    assert redaction.truncar_texto("abc", 2) == "ab..."
    assert redaction.truncar_texto("abc", 5) == "abc"
