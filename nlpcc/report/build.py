"""Assemble one report object from everything the pipeline found.

The JSON form is the single source of truth: the Markdown renderer, the
terminal output and anything built later all read this structure rather than
each other's text. That way a number can only be wrong in one place.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..diagnostics import Diagnostic, Origin, Phase, Severity
from ..optimize import OBJECTIVES, Recommendation
from ..quality.metrics import FileMetrics
from ..quality.score import Score


@dataclass
class Report:
    source: Path
    generated_at: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    score: Score | None = None
    recommendations: list[Recommendation] = field(default_factory=list)
    file_metrics: FileMetrics | None = None
    objectives: tuple[str, ...] = OBJECTIVES
    frontend_available: bool = False
    ran: bool = False
    program_output: str = ""

    # -- views the renderers use ------------------------------------------
    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics
                if d.severity in (Severity.ERROR, Severity.FATAL)]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.WARNING]

    @property
    def compile_errors(self) -> list[Diagnostic]:
        """Errors from the compiler, not from the quality rules.

        A buffer overflow is reported as an error because it is one, but it
        is not a *compile* error: the program built fine. Counting it as one
        made the report say "does not compile" about a program that had just
        compiled and run.
        """
        return [d for d in self.errors
                if d.origin in (Origin.FRONTEND, Origin.CLANG)]

    @property
    def compiles(self) -> bool:
        return not self.compile_errors

    def by_phase(self, phase: Phase) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.phase is phase]

    @property
    def phases_present(self) -> list[Phase]:
        seen = []
        for diag in self.diagnostics:
            if diag.phase not in seen:
                seen.append(diag.phase)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "generated_at": self.generated_at,
            "compiles": self.compiles,
            "frontend_available": self.frontend_available,
            "objectives": list(self.objectives),
            "summary": {
                "errors": len(self.errors),
                "compile_errors": len(self.compile_errors),
                "warnings": len(self.warnings),
                "findings": len(self.diagnostics),
                "recommendations": len(self.recommendations),
            },
            "score": self.score.to_dict() if self.score else None,
            "metrics": self._metrics_dict(),
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "program_output": self.program_output,
        }

    def _metrics_dict(self) -> dict[str, Any] | None:
        fm = self.file_metrics
        if fm is None:
            return None
        return {
            "total_lines": fm.total_lines,
            "code_lines": fm.code_lines,
            "comment_lines": fm.comment_lines,
            "comment_density": round(fm.comment_density, 3),
            "functions": [
                {"name": f.name, "line": f.line, "length": f.length,
                 "parameters": f.parameters, "complexity": f.complexity,
                 "max_nesting": f.max_nesting, "returns": f.returns,
                 "loops": f.loops}
                for f in fm.functions
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def build(analysis, recommendations: list[Recommendation] | None = None,
          objectives: tuple[str, ...] = OBJECTIVES) -> Report:
    """Build a Report from a pipeline.Analysis."""
    program_output = ""
    if analysis.run is not None:
        parts = [analysis.run.stdout or "", analysis.run.stderr or ""]
        program_output = "\n".join(p.rstrip() for p in parts if p.strip())

    return Report(
        source=analysis.source,
        generated_at=_dt.datetime.now().isoformat(timespec="seconds"),
        diagnostics=list(analysis.diagnostics),
        score=analysis.score,
        recommendations=list(recommendations or []),
        file_metrics=analysis.file_metrics,
        objectives=objectives,
        frontend_available=analysis.frontend_available,
        ran=analysis.run is not None,
        program_output=program_output,
    )
