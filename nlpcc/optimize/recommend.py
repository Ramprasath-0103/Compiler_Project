"""Suggest improvements for the objective the user picked.

Most of the detection already exists: a quality finding and an optimization
are the same observation addressed to different audiences. R_DEEP_NESTING
tells you the code is hard to read; the matching recommendation tells you to
invert the conditions and return early. So this module maps quality findings
to advice rather than walking the AST again, and adds only the patterns that
are purely about cost and carry no quality judgement.

The objective is the point. "Optimize this" has no single answer -- making
code faster and making it readable pull in opposite directions often enough
that a tool which ignores the question gives useless advice. Each
recommendation declares which objectives it serves and is filtered out when
the user did not ask for those.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..diagnostics import Diagnostic
from ..quality import metrics, rules
from ..quality.astjson import LOOPS, parse

SPEED = "speed"
MEMORY = "memory"
READABILITY = "readability"
MAINTAINABILITY = "maintainability"

OBJECTIVES = (SPEED, MEMORY, READABILITY, MAINTAINABILITY)

# Pure functions worth hoisting when called repeatedly with the same arguments.
PURE_CALLS = frozenset({"strlen", "strnlen", "abs", "labs", "sqrt", "pow", "floor", "ceil"})


@dataclass
class Recommendation:
    code: str
    objectives: tuple[str, ...]
    title: str
    rationale: str
    action: str
    line: int | None = None
    where: str = ""              # function name, when it applies to one
    snippet: str = ""
    effort: str = "small"        # small | moderate | large
    impact: str = "moderate"     # low | moderate | high

    def serves(self, wanted: tuple[str, ...]) -> bool:
        return any(o in self.objectives for o in wanted)

    def to_dict(self) -> dict:
        return {
            "code": self.code, "objectives": list(self.objectives),
            "title": self.title, "rationale": self.rationale,
            "action": self.action, "line": self.line, "where": self.where,
            "effort": self.effort, "impact": self.impact,
        }


@dataclass
class _Advice:
    """What a quality finding implies for each objective."""

    objectives: tuple[str, ...]
    title: str
    rationale: str
    action: str
    effort: str = "small"
    impact: str = "moderate"


# Quality finding -> optimization advice.
FROM_QUALITY: dict[str, _Advice] = {
    "P_CALL_IN_LOOP_CONDITION": _Advice(
        objectives=(SPEED,),
        title="Hoist the length calculation out of the loop",
        rationale=(
            "The condition is evaluated once per iteration, so a call that scans the "
            "whole string turns a single pass into one pass per character. For a "
            "string of n characters that is n-squared work where n was enough."
        ),
        action="Assign the value to a variable before the loop and compare against it.",
        impact="high",
    ),
    "P_ALLOCATION_IN_LOOP": _Advice(
        objectives=(SPEED, MEMORY),
        title="Allocate once, outside the loop",
        rationale=(
            "Each iteration pays for a trip through the allocator, which costs "
            "far more than the work around it, and every block not freed in the "
            "same iteration adds to the program's peak memory."
        ),
        action="Move the allocation above the loop and reuse the buffer, growing it "
               "only when an iteration needs more than it already has.",
        impact="high",
    ),
    "S_LEAKED_ALLOCATION": _Advice(
        objectives=(MEMORY,),
        title="Release the memory when you are done with it",
        rationale=(
            "The block is held until the process exits. Harmless in a program that "
            "runs once and stops; unbounded growth in anything that loops or serves."
        ),
        action="Add a matching free() on every path out of the function.",
    ),
    "R_DEEP_NESTING": _Advice(
        objectives=(READABILITY, MAINTAINABILITY),
        title="Flatten the nesting with early returns",
        rationale=(
            "Inverting each condition and returning immediately when it fails moves "
            "the main path back to the left margin, so the reader follows one "
            "sequence instead of holding five conditions in mind at once."
        ),
        action="Replace each `if (ok) { ... }` wrapper with `if (!ok) return ...;` "
               "and continue at the outer level.",
        effort="moderate",
        impact="high",
    ),
    "M_HIGH_COMPLEXITY": _Advice(
        objectives=(MAINTAINABILITY, READABILITY),
        title="Split this function along its decisions",
        rationale=(
            "Complexity is the number of independent paths, and therefore the "
            "minimum number of tests needed to cover the function. Splitting it "
            "divides that number between units that can each be tested alone."
        ),
        action="Extract each independent decision into its own named function.",
        effort="large",
        impact="high",
    ),
    "R_LONG_FUNCTION": _Advice(
        objectives=(READABILITY, MAINTAINABILITY),
        title="Break the function into named steps",
        rationale=(
            "A function that does not fit on a screen cannot be verified by reading "
            "it. The subsections usually already exist as paragraphs separated by "
            "blank lines -- those are the functions waiting to be named."
        ),
        action="Extract each paragraph into a function named after what it does.",
        effort="moderate",
    ),
    "M_TOO_MANY_PARAMETERS": _Advice(
        objectives=(MAINTAINABILITY,),
        title="Group the related parameters into a struct",
        rationale=(
            "C checks parameter types but not their meaning, so two adjacent "
            "parameters of the same type can be passed in the wrong order and still "
            "compile. A struct makes each one named at the call site."
        ),
        action="Define a struct for the parameters that belong together and pass it.",
        effort="moderate",
    ),
    "R_OPAQUE_NAMES": _Advice(
        objectives=(READABILITY,),
        title="Rename the variables after what they hold",
        rationale=(
            "A name is the only documentation that cannot fall out of date with the "
            "code. Single letters spend it for nothing, and the reader has to "
            "reconstruct each variable's purpose from its uses every time."
        ),
        action="Rename each one for the value it holds.",
    ),
    "R_MAGIC_NUMBER": _Advice(
        objectives=(READABILITY, MAINTAINABILITY),
        title="Give the repeated number a name",
        rationale=(
            "Nothing connects the occurrences of a bare literal, so a change has to "
            "find every one by eye. The site that gets missed becomes a bug that "
            "appears only for some inputs."
        ),
        action="Define it once with #define or a const and use the name everywhere.",
    ),
    "M_GLOBAL_STATE": _Advice(
        objectives=(MAINTAINABILITY,),
        title="Pass the value instead of sharing it globally",
        rationale=(
            "A global can be changed by anything, so a function that reads it cannot "
            "be tested without reproducing the whole program's history."
        ),
        action="Pass it as a parameter, or mark it const if it never changes.",
        effort="moderate",
    ),
}


def _repeated_pure_calls(source: Path) -> list[Recommendation]:
    """The same pure call made several times in one function."""
    out: list[Recommendation] = []
    for top in parse(source):
        if top.kind != "FunctionDecl":
            continue
        names = [metrics._called_name(c) for c in top.find("CallExpr")]
        counts = Counter(n for n in names if n in PURE_CALLS)
        for name, count in sorted(counts.items()):
            if count < 2:
                continue
            line = next((c.line for c in top.find("CallExpr")
                         if metrics._called_name(c) == name), top.line)
            out.append(Recommendation(
                code="O_CACHE_REPEATED_CALL",
                objectives=(SPEED,),
                title=f"Compute {name}() once and reuse it",
                rationale=(
                    f"{name}() is called {count} times in '{top.name}'. It has no side "
                    "effects, so every call after the first recomputes a value that "
                    "has not changed."
                ),
                action=f"Store the result of {name}() in a variable and use that.",
                line=line, where=top.name, impact="moderate",
            ))
    return out


def _loops_without_bounds_check(source: Path) -> list[Recommendation]:
    """Array writes inside a loop whose index is not visibly bounded."""
    out: list[Recommendation] = []
    for top in parse(source):
        if top.kind != "FunctionDecl":
            continue
        for node in top.walk():
            if node.kind not in LOOPS:
                continue
            subscripts = node.find("ArraySubscriptExpr")
            if not subscripts:
                continue
            out.append(Recommendation(
                code="O_BOUNDS_CHECK_IN_LOOP",
                objectives=(SPEED, MAINTAINABILITY),
                title="Lift the array bound into a named constant",
                rationale=(
                    "The loop indexes an array, so its bound and the array's length "
                    "have to agree. When both are written as literals, nothing keeps "
                    "them in step and the mismatch is an out-of-bounds write."
                ),
                action="Name the length once and use it for both the array and the "
                       "loop condition.",
                line=subscripts[0].line, where=top.name, impact="moderate",
            ))
            break               # one per function is enough to make the point
    return out


def recommend(source: Path, objectives: tuple[str, ...] = OBJECTIVES,
              quality_findings: list[Diagnostic] | None = None) -> list[Recommendation]:
    """Recommendations for ``source``, keeping only those serving ``objectives``."""
    unknown = [o for o in objectives if o not in OBJECTIVES]
    if unknown:
        raise ValueError(f"unknown objective(s): {', '.join(unknown)}; "
                         f"choose from {', '.join(OBJECTIVES)}")

    findings = quality_findings if quality_findings is not None else rules.analyze(source)

    out: list[Recommendation] = []
    for diag in findings:
        advice = FROM_QUALITY.get(diag.code)
        if advice is None:
            continue
        out.append(Recommendation(
            code=f"O_{diag.code}",
            objectives=advice.objectives,
            title=advice.title,
            rationale=advice.rationale,
            action=advice.action,
            line=diag.position.line if diag.position else None,
            where=str(diag.meta.get("function") or ""),
            snippet=diag.snippet,
            effort=advice.effort,
            impact=advice.impact,
        ))

    out.extend(_repeated_pure_calls(source))
    out.extend(_loops_without_bounds_check(source))

    kept = [r for r in out if r.serves(objectives)]
    order = {"high": 0, "moderate": 1, "low": 2}
    kept.sort(key=lambda r: (order.get(r.impact, 1), r.line or 0))
    return kept
