"""Provider selection: local first, and honest when there is nothing."""

from __future__ import annotations

from nlpcc import models


def test_local_providers_are_preferred_over_hosted(monkeypatch) -> None:
    """The project should work without anyone holding an API key."""
    lm = models.Provider("lmstudio", "local-a")
    ol = models.Provider("ollama", "local-b")
    an = models.Provider("anthropic", "claude-opus-5-5")

    monkeypatch.setattr(models, "_lmstudio", lambda: lm)
    monkeypatch.setattr(models, "_ollama", lambda: ol)
    monkeypatch.setattr(models, "_anthropic", lambda: an)
    assert models.available() is lm              # all three present

    monkeypatch.setattr(models, "_lmstudio", lambda: None)
    assert models.available() is ol              # no LM Studio

    monkeypatch.setattr(models, "_ollama", lambda: None)
    assert models.available() is an              # neither local one

    assert lm.local and ol.local and not an.local


def test_a_provider_can_be_pinned(monkeypatch) -> None:
    lm = models.Provider("lmstudio", "local-a")
    an = models.Provider("anthropic", "claude-opus-5-5")
    monkeypatch.setattr(models, "_lmstudio", lambda: lm)
    monkeypatch.setattr(models, "_anthropic", lambda: an)
    assert models.available(prefer="anthropic") is an
    assert models.available(prefer="nonsense") is None


def test_describe_explains_what_to_do_when_there_is_no_model() -> None:
    """The conftest fixture already simulates having no model installed."""
    message = models.describe()
    assert "lms server start" in message or "Ollama" in message


def test_a_keyless_anthropic_client_is_not_a_provider(monkeypatch) -> None:
    """The SDK defers credential resolution, so a keyless client builds fine.

    Treating that as available picks it over a working local model and then
    fails every call.
    """
    import importlib

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    # The fixture stubs the probes; this test needs the real one.
    real = importlib.reload(models)._anthropic
    assert real() is None


def test_json_is_recovered_from_a_messy_reply() -> None:
    """Small local models wrap the answer in fences, prose and think blocks."""
    assert models._extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert models._extract_json("<think>hmm</think> here: {\"a\": 2} ok") == {"a": 2}
    assert models._extract_json('{"a": "brace } inside"}') == {"a": "brace } inside"}
    assert models._extract_json("no json here") is None
    assert models._extract_json('{"truncated": "val') is None


def test_schema_mismatch_is_treated_as_no_answer() -> None:
    from pydantic import BaseModel

    class Shape(BaseModel):
        name: str
        count: int

    original = models._local_chat
    models._local_chat = lambda *a, **k: '{"name": "x", "count": "not a number"}'
    try:
        got = models.complete("sys", "prompt", Shape,
                              provider=models.Provider("lmstudio", "m"))
    finally:
        models._local_chat = original
    assert got is None
