from services import prompts


def test_montar_prompt_summarize():
    res = prompts.montar_prompt("summarize", ["erro", {"level": "warn", "msg": "x"}])
    assert "messages" in res
    assert res["messages"][0]["role"] == "system"
    assert "Logs:" in res["messages"][1]["content"]


def test_montar_prompt_explain_lista():
    res = prompts.montar_prompt("explain", ["linha1", "linha2"])
    assert len(res["messages"]) == 2
    assert "linha1" in res["messages"][1]["content"]


def test_montar_prompt_default():
    res = prompts.montar_prompt("outro", "algo")
    assert "messages" in res
