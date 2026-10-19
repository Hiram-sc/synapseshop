def test_truncar_texto_limite():
    from services import redaction

    # acima do limite: resultado com exatamente `limite` caracteres
    assert redaction.truncar_texto("abcdef", 5) == "ab..."
    # dentro do limite: texto preservado
    assert redaction.truncar_texto("abc", 5) == "abc"
    # limite <= 0 desliga o truncamento
    assert redaction.truncar_texto("abcdef", 0) == "abcdef"
    assert redaction.truncar_texto("", 5) == ""
