"""Provider selection: local first, and honest when there is nothing."""

from __future__ import annotations

from nlpcc import models


def test_a_configured_key_wins_and_local_is_the_fallback(monkeypatch) -> None:
    """Order is about quality; the guarantee is that local always works.

    A small local model is far worse at this than a configured hosted one, so
    when someone has set a key, use it. With no key at all the local providers
    still answer, which is what keeps the project usable with no account.
    """
    ge = models.Provider("gemini", "gemini-3-flash-preview")
    an = models.Provider("anthropic", "claude-opus-5-5")
    lm = models.Provider("lmstudio", "local-a")
    ol = models.Provider("ollama", "local-b")

    for name, found in (("_gemini", ge), ("_anthropic", an),
                        ("_lmstudio", lm), ("_ollama", ol)):
        monkeypatch.setattr(models, name, lambda found=found: found)
    assert models.available() is ge              # all four present

    monkeypatch.setattr(models, "_gemini", lambda: None)
    assert models.available() is an

    monkeypatch.setattr(models, "_anthropic", lambda: None)
    assert models.available() is lm              # falls back to local

    monkeypatch.setattr(models, "_lmstudio", lambda: None)
    assert models.available() is ol

    assert lm.local and ol.local
    assert not an.local and not ge.local


def test_a_provider_can_be_pinned(monkeypatch) -> None:
    lm = models.Provider("lmstudio", "local-a")
    ge = models.Provider("gemini", "gemini-3-flash-preview")
    monkeypatch.setattr(models, "_lmstudio", lambda: lm)
    monkeypatch.setattr(models, "_gemini", lambda: ge)
    assert models.available(prefer="lmstudio") is lm
    assert models.available(prefer="gemini") is ge
    assert models.available(prefer="nonsense") is None


def test_gemini_needs_a_key(monkeypatch) -> None:
    import importlib

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    real = importlib.reload(models)._gemini
    assert real() is None

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    provider = real()
    assert provider is not None and provider.kind == "gemini"


def test_describe_explains_what_to_do_when_there_is_no_model() -> None:
    """The conftest fixture already simulates having no model installed."""
    message = models.describe()
    assert "GEMINI_API_KEY" in message or "lms server start" in message


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
