"""Turn findings into a score that can be argued with.

Design rules, in order of importance:

1. Every point deducted names the rule that deducted it. ``Score.contributions``
   is the audit trail; if a dimension reads 62, the breakdown says which rules
   cost the other 38. A score nobody can trace is a score nobody can defend.

2. A critical security finding caps the overall score. Without the cap a
   weighted mean lets a program with a buffer overflow score in the eighties
   because its naming is tidy, which is exactly the wrong answer -- the one
   open question docs/scope.md raised about this design.

3. Code that does not compile cannot score well. Compiler errors are folded
   into the practices dimension, because a file full of syntax errors scoring
   100 for quality would discredit the whole report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..diagnostics import Diagnostic, Phase, Severity
from .rules import (MAINTAINABILITY, PERFORMANCE, PRACTICES, READABILITY,
                    RULES, SECURITY)

# Published weights. Security is heaviest because its failures are the only
# ones an attacker chooses the input for.
WEIGHTS: dict[str, float] = {
    SECURITY: 0.30,
    READABILITY: 0.20,
    MAINTAINABILITY: 0.20,
    PERFORMANCE: 0.15,
    PRACTICES: 0.15,
}

DIMENSIONS = tuple(WEIGHTS)

# A critical security finding limits the overall score no matter what else is true.
CRITICAL_SECURITY_CAP = 60

# How far the overall score may sit above its weakest dimension. A weighted
# mean alone dilutes one bad dimension into nothing: five levels of nesting and
# meaningless names scored 93 "excellent" because the other four dimensions
# were clean. Quality is a weakest-link property, so the headline follows the
# worst dimension rather than averaging it away.
WEAKEST_LINK_MARGIN = 20

# What an AI finding costs, when it does not say.
AI_FINDING_PENALTY = 10

# What a compiler diagnostic costs the practices dimension.
COMPILE_ERROR_PENALTY = 30
COMPILE_WARNING_PENALTY = 8

GRADES = ((90, "excellent"), (75, "good"), (60, "fair"), (40, "weak"), (0, "poor"))


@dataclass
class Contribution:
    code: str
    dimension: str
    penalty: int
    detail: str
    line: int | None = None


@dataclass
class Score:
    dimensions: dict[str, int] = field(default_factory=dict)
    overall: int = 100
    contributions: list[Contribution] = field(default_factory=list)
    capped_by_security: bool = False
    limited_by: str = ""        # the weakest dimension, when it held the score down

    @property
    def grade(self) -> str:
        return next(label for threshold, label in GRADES if self.overall >= threshold)

    def for_dimension(self, dimension: str) -> list[Contribution]:
        return [c for c in self.contributions if c.dimension == dimension]

    def to_dict(self) -> dict:
        return {
            "overall": self.overall,
            "grade": self.grade,
            "capped_by_security": self.capped_by_security,
            "limited_by": self.limited_by,
            "weights": dict(WEIGHTS),
            "dimensions": {
                name: {
                    "score": value,
                    "weight": WEIGHTS[name],
                    "deductions": [
                        {"code": c.code, "penalty": c.penalty,
                         "detail": c.detail, "line": c.line}
                        for c in self.for_dimension(name)
                    ],
                }
                for name, value in self.dimensions.items()
            },
        }


def compute(diagnostics: Iterable[Diagnostic]) -> Score:
    """Score a program from everything found about it."""
    score = Score(dimensions={name: 100 for name in DIMENSIONS})
    critical = False

    for diag in diagnostics:
        rule = RULES.get(diag.code)

        if rule is not None:
            dimension, penalty = rule.dimension, rule.penalty
            critical = critical or rule.critical
        elif diag.meta.get("dimension") in WEIGHTS:
            # AI findings bring their own dimension and penalty.
            dimension = diag.meta["dimension"]
            penalty = int(diag.meta.get("penalty", AI_FINDING_PENALTY))
        elif diag.phase is Phase.QUALITY and diag.origin.value == "rules":
            continue                      # a quality finding with no rule: not scorable
        elif diag.severity in (Severity.ERROR, Severity.FATAL):
            dimension, penalty = PRACTICES, COMPILE_ERROR_PENALTY
        elif diag.severity is Severity.WARNING:
            dimension, penalty = PRACTICES, COMPILE_WARNING_PENALTY
        else:
            continue                      # notes do not cost anything

        score.dimensions[dimension] = max(0, score.dimensions[dimension] - penalty)
        score.contributions.append(Contribution(
            code=diag.code or diag.severity.value,
            dimension=dimension,
            penalty=penalty,
            detail=diag.message,
            line=diag.position.line if diag.position else None,
        ))

    weighted = sum(score.dimensions[name] * weight for name, weight in WEIGHTS.items())
    score.overall = int(round(weighted))

    weakest = min(score.dimensions.values())
    if score.overall > weakest + WEAKEST_LINK_MARGIN:
        score.overall = weakest + WEAKEST_LINK_MARGIN
        score.limited_by = min(score.dimensions, key=lambda d: score.dimensions[d])

    if critical and score.overall > CRITICAL_SECURITY_CAP:
        score.overall = CRITICAL_SECURITY_CAP
        score.capped_by_security = True

    return score
