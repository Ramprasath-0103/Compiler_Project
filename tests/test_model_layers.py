"""Steps 5b and 9: the model-backed layers.

These tests never reach the network. What matters about these two layers is
that they are optional -- the pipeline must behave identically when there is
no API key, no package, or no connection -- so that is what is tested here.
"""

from __future__ import annotations

import json

from conftest import CORPUS
from nlpcc import generate, pipeline
from nlpcc.diagnostics import Diagnostic, Phase, Position, Severity
from nlpcc.explain import llm


def _unexplained() -> Diagnostic:
    return Diagnostic(phase=Phase.SYNTAX, severity=Severity.ERROR,
                      message="something the rules have never seen",
                      file="x.c", position=Position(3, 1), code="clang:novel_thing")


# --- step 5b -----------------------------------------------------------------
def test_offline_mode_leaves_diagnostics_untouched() -> None:
    diag = _unexplained()
    outcome = llm.explain_unmatched([diag], allow_network=False)
    assert not outcome.available
    assert outcome.attempted == 1
    assert diag.explanation == ""        # unchanged, not invented


def test_rule_explained_findings_are_never_sent_to_the_model() -> None:
    """The deterministic layer comes first; the model only sees the remainder."""
    analysis = pipeline.analyze(CORPUS / "syntax" / "missing_semicolon.c")
    outcome = llm.explain_unmatched(analysis.diagnostics, allow_network=False)
    assert outcome.attempted == 0


def test_cache_round_trip(tmp_path) -> None:
    diag = _unexplained()
    answer = llm.ModelExplanation("A novel problem", "The long explanation.", "Do this.")
    llm._write_cache(llm._cache_key(diag), answer, tmp_path)

    outcome = llm.explain_unmatched([diag], cache_dir=tmp_path, allow_network=False)
    assert outcome.explained == 1 and outcome.from_cache == 1
    assert diag.explanation == "The long explanation."
    assert diag.meta["explained_by"] == "llm-cache"
    assert diag.meta["generated"] is True        # the report must label it


def test_cache_key_ignores_program_specific_names() -> None:
    """`undeclared identifier 'totl'` and `'xyz'` are the same problem."""
    a = Diagnostic(phase=Phase.SEMANTIC, severity=Severity.ERROR,
                   code="clang:undeclared", message="use of undeclared identifier 'totl'")
    b = Diagnostic(phase=Phase.SEMANTIC, severity=Severity.ERROR,
                   code="clang:undeclared", message="use of undeclared identifier 'xyz'")
    assert llm._cache_key(a) == llm._cache_key(b)


def test_model_output_is_labelled_as_generated() -> None:
    diag = _unexplained()
    llm._apply(diag, llm.ModelExplanation("T", "E", "F"), source="llm")
    assert diag.meta["explained_by"] == "llm"
    assert diag.meta["generated"] is True


# --- step 9 ------------------------------------------------------------------
def test_markdown_fences_are_stripped() -> None:
    assert generate._strip_fence("```c\nint main(void){return 0;}\n```") == \
        "int main(void){return 0;}"
    assert generate._strip_fence("int main(void){return 0;}") == \
        "int main(void){return 0;}"


def test_generation_without_a_model_fails_honestly() -> None:
    """It must not return code it never validated, or claim success.

    The conftest fixture simulates a machine with no model available.
    """
    import nlpcc.generate as module

    result = module.generate("a program that prints hello")

    assert result.accepted is False
    assert result.code == ""
    assert result.failure_reason
    assert json.loads(json.dumps(result.to_dict()))["accepted"] is False


def test_rejected_program_is_not_presented_as_working() -> None:
    """An attempt that still has errors is returned unaccepted."""
    attempt = generate.Attempt(
        code="int main(void){",
        diagnostics=[Diagnostic(phase=Phase.SYNTAX, severity=Severity.ERROR,
                                message="expected '}'", code="clang:expected_rbrace")])
    assert attempt.errors


# --- recovering a program from a model's reply --------------------------------
def test_code_is_recovered_from_whatever_wraps_it() -> None:
    """Models wrap code in fences, prose and think blocks, however asked not to.

    Generation asks for raw C rather than JSON-wrapped C on purpose: escaping
    a program into a JSON string defeated a small local model every time, and
    every failure was the escaping, not the C.
    """
    expected = "#include <stdio.h>\nint main(void) { return 0; }"

    assert generate._clean(expected) == expected
    assert generate._clean(f"```c\n{expected}\n```") == expected
    assert generate._clean(f"Here you go:\n\n{expected}") == expected
    assert generate._clean(f"<think>planning</think>\n{expected}") == expected
    assert generate._clean(f"Sure!\n```\n{expected}\n```\nHope that helps") \
        .startswith("#include")


def test_failures_say_which_failure_it_was() -> None:
    """'No code' is indistinguishable from slow, unreachable, or malformed."""
    from nlpcc import models

    reasons: list[str] = []
    models.complete_text("sys", "prompt", reasons=reasons)
    assert reasons == ["no model available"]
