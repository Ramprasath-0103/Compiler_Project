import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nlpcc import models  # noqa: E402

CORPUS = Path(__file__).parent / "corpus"


@pytest.fixture(autouse=True)
def no_live_model(monkeypatch):
    """No test may depend on a model being installed, running or reachable.

    Without this the suite's runtime and its results depend on whether LM
    Studio happens to be open: one test went from instant to 92 seconds when
    a local server was running, and would have given different answers on a
    different machine. A test that needs the AI path injects a fake.
    """
    for probe in ("_gemini", "_anthropic", "_lmstudio", "_ollama"):
        monkeypatch.setattr(models, probe, lambda: None)
