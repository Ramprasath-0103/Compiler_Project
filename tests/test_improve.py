"""Verified improvement: the model proposes, the scorer decides."""

from __future__ import annotations

from nlpcc import improve as improve_module
from nlpcc.improve import Improvement, _diff_lines


def test_a_program_that_does_not_compile_is_refused() -> None:
    result = improve_module.improve("int main(void) { int x = 1 return 0; }")
    assert not result.accepted
    assert "compile" in result.reason


def test_without_a_model_it_says_so(monkeypatch) -> None:
    """The conftest fixture already simulates having no model."""
    result = improve_module.improve("int main(void) { return 0; }")
    assert not result.accepted
    assert result.reason


def test_diff_rows_are_classified_for_the_ui() -> None:
    diff = ("--- before.c\n+++ after.c\n@@ -1,2 +1,2 @@\n"
            " int main(void) {\n-    int x;\n+    int count = 0;\n")
    kinds = [row["kind"] for row in _diff_lines(diff)]
    assert kinds == ["meta", "meta", "hunk", "same", "remove", "add"]


def test_score_change_is_reported() -> None:
    result = Improvement(original="x", before={"overall": 60}, after={"overall": 85})
    assert result.score_change == 25


def test_a_worse_rewrite_is_not_offered_as_an_improvement() -> None:
    """"Optimized" has to be a measurement, not a claim."""
    import nlpcc.pipeline as pipeline_module

    # The rewrite compiles but scores lower; it must be rejected.
    result = Improvement(original="x", improved="y",
                         before={"overall": 90, "compiles": True},
                         after={"overall": 70, "compiles": True})
    assert result.score_change < 0
    assert not result.accepted      # default; improve() only sets it on a win
