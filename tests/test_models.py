"""Provider selection: local first, and honest when there is nothing."""

from __future__ import annotations

from nlpcc import models


def test_local_providers_are_preferred_over_hosted() -> None:
    """The project should work without anyone holding an API key."""
    order = models.available.__doc__
    assert order  # documented
    lm = models.Provider("lmstudio", "m")
    ol = models.Provider("ollama", "m")
    an = models.Provider("anthropic", "claude-opus-5-5")
    assert lm.local and ol.local
    assert not an.local


def test_describe_explains_what_to_do_when_there_is_no_model() -> None:
    original = models.available
    models.available = lambda prefer="": None
    try:
        message = models.describe()
    finally:
        models.available = original
    assert "lms server start" in message or "Ollama" in message


def test_a_keyless_anthropic_client_is_not_a_provider() -> None:
    """The SDK defers credential resolution, so a keyless client builds fine.

    Treating that as available picks it over a working local model and then
    fails every call.
    """
    import os
    saved = {k: os.environ.pop(k, None)
             for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    try:
        assert models._anthropic() is None
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


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
