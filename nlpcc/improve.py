"""Rewrite a program to address its findings, and prove the rewrite is better.

The model proposes; this module checks. A rewrite is accepted only when it
still compiles and its quality score is no worse than the original's -- so
"optimized" is a measurement, not a claim, and a rewrite that makes things
worse is reported as rejected rather than offered as an improvement.

The diff is computed here rather than in the browser so the terminal and the
web interface show the same one.
"""

from __future__ import annotations

import difflib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import generate, models, pipeline
from .diagnostics import Diagnostic, Origin, Severity
from .optimize import OBJECTIVES

SYSTEM_PROMPT = """You improve small C programs.

You are given a program and a list of problems found in it. Rewrite the
program so those problems are gone.

Rules:
- Keep what the program does identical. Same inputs, same outputs.
- Fix the listed problems. Do not make unrelated changes.
- C99, portable, compiles clean under -Wall -Wextra.
- Check what can fail; no gets, strcpy, sprintf or atoi.
- Name things for what they hold.

Reply with the C source and nothing else: no explanation before or after it,
no markdown fence. Your whole reply must be the contents of a .c file."""


@dataclass
class Improvement:
    original: str
    improved: str = ""
    diff: str = ""
    accepted: bool = False
    reason: str = ""
    provider: str = ""
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    objectives: tuple[str, ...] = OBJECTIVES

    @property
    def score_change(self) -> int:
        return int(self.after.get("overall", 0)) - int(self.before.get("overall", 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "provider": self.provider,
            "original": self.original,
            "improved": self.improved,
            "diff": self.diff,
            "diff_lines": _diff_lines(self.diff),
            "before": self.before,
            "after": self.after,
            "score_change": self.score_change,
            "objectives": list(self.objectives),
        }


def _diff_lines(diff: str) -> list[dict[str, str]]:
    """The diff as rows the UI can colour without parsing it again."""
    rows = []
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            kind = "meta"
        elif line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+"):
            kind = "add"
        elif line.startswith("-"):
            kind = "remove"
        else:
            kind = "same"
        rows.append({"kind": kind, "text": line})
    return rows


def _snapshot(analysis) -> dict[str, Any]:
    score = analysis.score
    return {
        "overall": score.overall if score else None,
        "grade": score.grade if score else None,
        "dimensions": dict(score.dimensions) if score else {},
        "findings": len([d for d in analysis.diagnostics
                         if d.severity in (Severity.ERROR, Severity.WARNING)]),
        "compiles": not [d for d in analysis.diagnostics
                         if d.severity in (Severity.ERROR, Severity.FATAL)
                         and d.origin in (Origin.CLANG, Origin.FRONTEND)],
    }


def _problem_list(analysis) -> str:
    lines = []
    for diag in analysis.diagnostics:
        if diag.severity is Severity.NOTE:
            continue
        where = f"line {diag.position.line}" if diag.position else "the file"
        title = diag.meta.get("title") or diag.message
        lines.append(f"- {where}: {title}")
        if diag.fix:
            lines.append(f"    fix: {diag.fix}")
    return "\n".join(lines) or "- no specific findings; improve it where you can"


def improve(code: str, objectives: tuple[str, ...] = OBJECTIVES) -> Improvement:
    """Ask for a better version of ``code`` and verify that it is better."""
    result = Improvement(original=code, objectives=objectives)

    with tempfile.TemporaryDirectory(prefix="nlpcc-improve-") as tmp:
        before_path = Path(tmp) / "before.c"
        before_path.write_text(code)
        before = pipeline.analyze(before_path, quality=True)
        result.before = _snapshot(before)

        # Checked before looking for a model: there is no point spending a
        # call on a program the compiler has already rejected, and the user
        # needs to hear about the errors rather than about the model.
        if not result.before["compiles"]:
            result.reason = ("the program has to compile before it can be improved; "
                             "fix the errors first")
            return result

        provider = models.available()
        if provider is None:
            result.reason = models.describe()
            return result
        result.provider = str(provider)

        prompt = (
            f"Improve this program, with an eye to {', '.join(objectives)}.\n\n"
            f"The program:\n\n{code}\n\n"
            f"Problems found in it:\n\n{_problem_list(before)}\n"
        )
        reasons: list[str] = []
        reply = models.complete_text(SYSTEM_PROMPT, prompt, provider=provider,
                                     max_tokens=4096, reasons=reasons)
        improved = generate._clean(reply)
        if not improved.strip():
            result.reason = ("the model returned no code: "
                             + ("; ".join(reasons) or "no reply"))
            return result

        after_path = Path(tmp) / "after.c"
        after_path.write_text(improved)
        after = pipeline.analyze(after_path, quality=True)
        result.improved = improved
        result.after = _snapshot(after)

        result.diff = "".join(difflib.unified_diff(
            code.splitlines(keepends=True),
            improved.splitlines(keepends=True),
            fromfile="before.c", tofile="after.c", n=3))

        if not result.after["compiles"]:
            result.reason = ("the rewrite does not compile, so it is not offered "
                             "as an improvement")
            return result

        before_score = result.before.get("overall") or 0
        after_score = result.after.get("overall") or 0
        if after_score < before_score:
            result.reason = (f"the rewrite scored {after_score} against "
                             f"{before_score}, so it is not an improvement")
            return result

        result.accepted = True
        if after_score == before_score:
            result.reason = (f"the score is unchanged at {after_score}; the rewrite "
                             "addresses the findings without moving it")
        else:
            result.reason = f"score {before_score} to {after_score}"
        return result
