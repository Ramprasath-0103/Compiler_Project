"""Step 9: write C from a description, then hold it to the same standard.

The part that matters is not the generation -- it is that generated code goes
through this project's own pipeline before anyone sees it. A model that emits
a program with a syntax error is caught by our compiler, handed its own
diagnostics, and asked again. Code that still does not compile is returned
labelled as rejected rather than printed as if it worked.

So the pipeline is the referee, and the generator is just another source of
programs for it to judge. That is also why this step is last in the build
order: it is only trustworthy once everything before it works.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import models
from .diagnostics import Diagnostic

MAX_ATTEMPTS = 2

SYSTEM_PROMPT = """You write small, correct C programs.

Requirements:
- C99. Portable: it must compile with clang and gcc under -Wall -Wextra.
- A complete, self-contained program including every #include it needs.
- Check what can fail: the result of malloc, the return of scanf, array bounds.
- No undefined behaviour. No gets, strcpy, sprintf, or atoi.
- Initialize every variable before it is read.
- Name things for what they hold. Comment only the decisions the code cannot show.
- Exactly one program. No prose around it, no markdown fences."""


@dataclass
class Attempt:
    code: str
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        from .diagnostics import Severity
        return [d for d in self.diagnostics
                if d.severity in (Severity.ERROR, Severity.FATAL)]


@dataclass
class GenerationResult:
    description: str
    code: str = ""
    accepted: bool = False
    attempts: list[Attempt] = field(default_factory=list)
    notes: str = ""
    failure_reason: str = ""
    provider: str = ""

    @property
    def diagnostics(self) -> list[Diagnostic]:
        return self.attempts[-1].diagnostics if self.attempts else []

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "accepted": self.accepted,
            "attempts": len(self.attempts),
            "code": self.code,
            "notes": self.notes,
            "provider": self.provider,
            "failure_reason": self.failure_reason,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


_FENCE = re.compile(r"^\s*```[a-zA-Z]*\n(.*?)\n```\s*$", re.S)


def _strip_fence(text: str) -> str:
    """Remove a markdown fence the model added despite being asked not to."""
    match = _FENCE.match(text)
    return match.group(1) if match else text.strip()


_LEADING_PROSE = re.compile(r"^.*?(?=#include|/\*|//|\bint\s+main\b)", re.S)


def _clean(reply: str) -> str:
    """Recover the program from whatever the model wrapped around it."""
    code = _strip_fence(reply.strip())
    code = re.sub(r"<think>.*?</think>", "", code, flags=re.S).strip()
    code = _strip_fence(code)
    # Drop any sentence before the first thing that can begin a C file.
    trimmed = _LEADING_PROSE.sub("", code, count=1)
    return (trimmed or code).strip()


def _request(provider, description: str, previous: Attempt | None,
             reasons: list[str] | None = None) -> tuple[str, str]:
    """One generation call. Returns (code, notes)."""
    if previous is None:
        prompt = f"Write a C program that does the following:\n\n{description}"
    else:
        # Hand the model our own diagnostics, already in plain English.
        problems = "\n".join(
            f"  line {d.position.line if d.position else '?'}: {d.message}"
            + (f"\n      ({d.explanation})" if d.explanation else "")
            for d in previous.errors
        )
        prompt = (
            f"This program was written for the following task:\n\n{description}\n\n"
            f"It does not compile. Here is the program:\n\n{previous.code}\n\n"
            f"The compiler reported:\n\n{problems}\n\n"
            "Fix the program. Return the complete corrected program."
        )

    reply = models.complete_text(SYSTEM_PROMPT, prompt, provider=provider,
                                 max_tokens=4096, reasons=reasons)
    code = _clean(reply)
    if code and reasons is not None and not code.startswith(("#", "/", "i", "v", "s")):
        reasons.append("the reply did not look like a C program")
    return code, ""


def generate(description: str, workdir: Path | None = None,
             max_attempts: int = MAX_ATTEMPTS) -> GenerationResult:
    """Generate a C program for ``description`` and validate it with our pipeline."""
    import tempfile

    from . import pipeline

    result = GenerationResult(description=description)

    provider = models.available()
    if provider is None:
        result.failure_reason = models.describe()
        return result
    result.provider = str(provider)

    previous: Attempt | None = None
    with tempfile.TemporaryDirectory(prefix="nlpcc-gen-") as tmp:
        scratch = Path(workdir) if workdir else Path(tmp)
        scratch.mkdir(parents=True, exist_ok=True)
        candidate = scratch / "generated.c"

        for _ in range(max_attempts):
            reasons: list[str] = []
            try:
                code, notes = _request(provider, description, previous, reasons)
            except Exception as exc:             # noqa: BLE001
                result.failure_reason = f"the model call failed ({exc.__class__.__name__})"
                return result
            if not code.strip():
                detail = "; ".join(reasons) or "no reply"
                if result.attempts:
                    # Keep what the earlier attempt produced rather than
                    # reporting nothing; it is the better of the two answers.
                    result.failure_reason = (
                        f"the follow-up attempt produced nothing ({detail}); "
                        "the program below is the earlier attempt, and it does "
                        "not compile")
                else:
                    result.failure_reason = f"the model produced no usable code: {detail}"
                return result

            candidate.write_text(code)
            analysis = pipeline.analyze(candidate, run=False)
            attempt = Attempt(code=code, diagnostics=list(analysis.diagnostics))
            result.attempts.append(attempt)
            result.code, result.notes = code, notes

            if not attempt.errors:
                result.accepted = True
                return result
            previous = attempt

    result.failure_reason = (
        f"the generated program still does not compile after {len(result.attempts)} "
        "attempt(s); it is returned unaccepted so it is not mistaken for working code"
    )
    return result
