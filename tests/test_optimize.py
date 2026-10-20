"""Step 7: objective-driven recommendations."""

from __future__ import annotations

import pytest

from conftest import CORPUS
from nlpcc.optimize import OBJECTIVES, recommend


def test_nothing_to_recommend_for_clean_code() -> None:
    assert recommend(CORPUS / "clean.c", OBJECTIVES) == []


def test_the_objective_changes_the_answer() -> None:
    """The point of the feature: "optimize this" has no single answer."""
    case = CORPUS / "quality" / "deep_nesting.c"
    readability = recommend(case, ("readability",))
    speed = recommend(case, ("speed",))
    assert readability, "deeply nested code has readability advice"
    assert not speed, "and nothing to say about speed"


def test_recommendations_declare_the_objectives_they_serve() -> None:
    for rec in recommend(CORPUS / "quality" / "deep_nesting.c", OBJECTIVES):
        assert rec.objectives
        assert set(rec.objectives) <= set(OBJECTIVES)
        assert rec.rationale and rec.action
        assert rec.impact in ("low", "moderate", "high")
        assert rec.effort in ("small", "moderate", "large")


def test_unknown_objective_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown objective"):
        recommend(CORPUS / "clean.c", ("faster",))


def test_high_impact_comes_first() -> None:
    recs = recommend(CORPUS / "quality" / "deep_nesting.c", OBJECTIVES)
    order = {"high": 0, "moderate": 1, "low": 2}
    ranks = [order[r.impact] for r in recs]
    assert ranks == sorted(ranks)
