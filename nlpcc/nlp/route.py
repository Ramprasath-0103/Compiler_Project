"""Carry out what the intent detector decided.

One router for both the CLI and the HTTP API, so a sentence typed into the
web page and the same sentence typed into the terminal do the same thing.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .. import generate as generator
from .. import pipeline
from ..optimize import OBJECTIVES, recommend
from ..report import build as build_report
from .intent import ANALYZE, EXPLAIN, GENERATE, OPTIMIZE, RUN, SCORE, Intent, detect


def _objectives(intent: Intent) -> tuple[str, ...]:
    return intent.objectives or OBJECTIVES


def run(text: str, code: str = "", source: Path | None = None,
        use_model: bool = False) -> dict:
    """Detect what ``text`` asks for, do it, and return a JSON-ready result."""
    has_code = bool(code.strip()) or source is not None
    intent = detect(text, has_code=has_code)

    result: dict = {"intent": intent.to_dict()}

    if intent.action == GENERATE:
        generated = generator.generate(intent.description or text)
        result["generated"] = generated.to_dict()
        return result

    if not has_code:
        result["error"] = "there is no code to work on"
        return result

    with tempfile.TemporaryDirectory(prefix="nlpcc-ask-") as tmp:
        path = source
        if path is None:
            path = Path(tmp) / "submitted.c"
            path.write_text(code)

        analysis = pipeline.analyze(
            path,
            run=(intent.action == RUN),
            quality=intent.action in (SCORE, OPTIMIZE, ANALYZE),
            ai=use_model and intent.action in (SCORE, ANALYZE),
        )

        if use_model and intent.action == EXPLAIN:
            from ..explain import llm
            llm.explain_unmatched(analysis.diagnostics, source=path)

        objectives = _objectives(intent)
        recommendations = []
        if intent.action in (OPTIMIZE, ANALYZE):
            findings = [d for d in analysis.diagnostics if d.origin.value == "rules"]
            recommendations = recommend(path, objectives,
                                        quality_findings=findings,
                                        score=analysis.score)

        report = build_report(analysis, recommendations, objectives)
        payload = report.to_dict()
        payload["source"] = path.name
        for diag in payload["diagnostics"]:
            diag["file"] = path.name
        result["report"] = payload
        return result
