"""The one path everything takes: source in, explained diagnostics out.

Order matters. Our own front end runs first so its findings own the report;
clang runs second as ground truth; the executor runs only if it compiled.

Our front end and clang describe the same mistakes in different words, so
without suppression every error is reported twice -- one missing semicolon
produced both E_MISSING_SEMICOLON and clang:expected_semicolon_at_end_of_
declaration, at the same line, and the report read as two problems. Clang's
copy is dropped where ours already covers that line (see ``_supersedes``);
the dropped findings are kept on the Analysis so nothing vanishes silently
and a gap in our analyzer is still measurable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .backends import clang_runner, executor
from .diagnostics import Diagnostic, Severity, sort_diagnostics
from .explain import rules
from .frontend import lexer, parser, semantic
from .quality import metrics as quality_metrics
from .quality import rules as quality_rules
from .quality import score as quality_score


@dataclass
class Analysis:
    source: Path
    diagnostics: list[Diagnostic] = field(default_factory=list)
    run: executor.RunResult | None = None
    frontend_available: bool = False
    score: "quality_score.Score | None" = None
    file_metrics: "quality_metrics.FileMetrics | None" = None
    superseded: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics
                if d.severity in (Severity.ERROR, Severity.FATAL)]

    @property
    def ok(self) -> bool:
        return not self.errors


def _supersedes(own: list[Diagnostic], other: Diagnostic) -> bool:
    """Does our own front end already account for this clang finding?

    True when we reported at the same line and either we called it an error --
    in which case clang's findings there are the same mistake and the cascade
    after it -- or we reported at the same severity. A clang finding at a line
    we said nothing about is a real gap and always kept.
    """
    if other.position is None:
        return False
    line = other.position.line
    for mine in own:
        if mine.position is None or mine.position.line != line:
            continue
        if mine.severity in (Severity.ERROR, Severity.FATAL):
            return True
        if mine.severity is other.severity:
            return True
    return False


def _run_own_frontend(source: Path) -> tuple[list[Diagnostic], bool]:
    """Run our analyzer if it exists yet. Returns (diagnostics, available)."""
    try:
        tokens, diags = lexer.tokenize_file(source)
        unit_parser = parser.Parser(tokens, str(source))
        unit = unit_parser.parse()
        diags += unit_parser.diagnostics
        analyzer = semantic.SemanticAnalyzer(unit, str(source))
        diags += analyzer.analyze()
        return diags, True
    except NotImplementedError:
        # Expected until step 4 lands; clang carries the pipeline until then.
        return [], False


def analyze(source: Path, run: bool = False, stdin: str = "",
            quality: bool = False) -> Analysis:
    """Analyze one file.

    ``quality`` adds the step-6 pass: it needs a parseable program, so it is
    skipped when the file does not compile -- there is no useful complexity
    figure for a function the parser could not finish reading.
    """
    result = Analysis(source=source)

    own, available = _run_own_frontend(source)
    result.frontend_available = available
    result.diagnostics.extend(own)

    seen = {d.key for d in own}
    for diag in clang_runner.analyze(source):
        if diag.key in seen:
            continue
        if _supersedes(own, diag):
            result.superseded.append(diag)
            continue
        result.diagnostics.append(diag)
        seen.add(diag.key)

    if quality and not result.errors:
        result.file_metrics = quality_metrics.measure(source)
        for diag in quality_rules.analyze(source, measured=result.file_metrics):
            if diag.key not in seen:
                result.diagnostics.append(diag)
                seen.add(diag.key)

    if run and not result.errors:
        result.run = executor.compile_and_run(source, stdin=stdin)
        for diag in result.run.diagnostics:
            if diag.key not in seen:
                result.diagnostics.append(diag)
                seen.add(diag.key)

    result.diagnostics = sort_diagnostics(rules.explain_all(result.diagnostics))

    # Scored from everything found, so compiler errors cost the practices
    # dimension rather than being invisible to the score.
    if quality:
        result.score = quality_score.compute(result.diagnostics)

    return result
