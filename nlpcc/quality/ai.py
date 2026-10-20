"""AI code analysis -- the diagram's analysis stage.

Reads the program and reports problems across the same five dimensions the
rule engine uses. It runs alongside the rules, not instead of them: the rules
catch what they know and always work offline, and this catches what no fixed
threshold can describe.

Optional, like every other model-backed part of this project. No package, no
credentials, no network or a bad reply leaves the analysis exactly as the
rules produced it.
"""

from __future__ import annotations

from pathlib import Path

from ..diagnostics import Diagnostic, Origin, Phase, Position, Severity
from .rules import MAINTAINABILITY, PERFORMANCE, PRACTICES, READABILITY, SECURITY

MODEL = "claude-opus-5-5"
DIMENSIONS = (SECURITY, READABILITY, MAINTAINABILITY, PERFORMANCE, PRACTICES)
MAX_FINDINGS = 8

SYSTEM_PROMPT = f"""You review C code across exactly five dimensions: \
{', '.join(DIMENSIONS)}.

Report only real problems in the code you are given. Say nothing about style
preferences, and nothing a compiler already reports.

For each finding give: the dimension, the line number, a short title, an
explanation a first-year student understands, a one-sentence fix, and a
penalty from 5 to 40 for how much it should cost that dimension's score.

Report at most {MAX_FINDINGS} findings. If the code is fine, report none."""


def _client():
    try:
        import anthropic
    except ImportError:
        return None, "the 'anthropic' package is not installed"
    try:
        return anthropic.Anthropic(), ""
    except Exception as exc:                     # noqa: BLE001
        return None, f"no API credentials available ({exc.__class__.__name__})"


def analyze(source: Path) -> list[Diagnostic]:
    """AI findings for one file. Empty when the model is unavailable."""
    client, _reason = _client()
    if client is None:
        return []

    try:
        from pydantic import BaseModel
    except ImportError:
        return []

    class Finding(BaseModel):
        dimension: str
        line: int
        title: str
        explanation: str
        fix: str
        penalty: int

    class Review(BaseModel):
        findings: list[Finding]

    try:
        code = source.read_text(errors="replace")
    except OSError:
        return []

    numbered = "\n".join(f"{i:4d}  {line}"
                         for i, line in enumerate(code.splitlines(), 1))
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            output_config={"effort": "medium"},
            messages=[{"role": "user",
                       "content": f"Review this C program:\n\n{numbered}"}],
            output_format=Review,
        )
    except Exception:                            # noqa: BLE001 - never fail the pipeline
        return []

    parsed = getattr(response, "parsed_output", None)
    if parsed is None:
        return []

    lines = code.splitlines()
    out: list[Diagnostic] = []
    for finding in parsed.findings[:MAX_FINDINGS]:
        if finding.dimension not in DIMENSIONS:
            continue
        line = max(0, min(finding.line, len(lines)))
        out.append(Diagnostic(
            phase=Phase.QUALITY,
            severity=Severity.WARNING,
            message=finding.title.strip(),
            file=str(source),
            position=Position(line) if line else None,
            code=f"AI_{finding.dimension.upper()}",
            origin=Origin.AI,
            snippet=lines[line - 1].strip() if line else "",
            explanation=finding.explanation.strip(),
            fix=finding.fix.strip(),
            meta={"title": finding.title.strip(),
                  "explained_by": "ai",
                  "generated": True,
                  "dimension": finding.dimension,
                  "penalty": max(5, min(40, finding.penalty))},
        ))
    return out
