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
from .diagnostics import Diagnostic, Origin, Severity, sort_diagnostics
from .explain import rules
from .frontend import lexer, parser, semantic
from .quality import ai as quality_ai
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
    subset_limited: bool = False     # our front end could not model this file

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics
                if d.severity in (Severity.ERROR, Severity.FATAL)]

    @property
    def ok(self) -> bool:
        return not self.errors


def _demote_subset_artifacts(own: list[Diagnostic],
                             clang_findings: list[Diagnostic]) -> tuple[list[Diagnostic], bool]:
    """If clang compiled it, our errors are subset limits, not errors.

    Our front end models a subset of C; clang models all of it. So when our
    analyzer reports errors on a file clang compiles without any, the program
    is valid C that uses something we do not cover -- and saying "error" about
    it is simply wrong.

    This came from real output: `for (size_t i = 0; ...)` made our parser emit
    six cascading errors on a program clang accepted silently, because the
    subset has no typedefs and the for-init clause had no path for an unknown
    type name. Patching that one clause would leave every other gap to be
    found the same way, by a user being told their working code is broken.

    Declaring clang the ground truth means acting on it: the errors become one
    note saying which construct we could not model, and the file is clang's.
    """
    clang_errors = [d for d in clang_findings
                    if d.severity in (Severity.ERROR, Severity.FATAL)]
    own_errors = [d for d in own if d.severity in (Severity.ERROR, Severity.FATAL)]
    if clang_errors or not own_errors:
        return own, False

    kept = [d for d in own if d.severity not in (Severity.ERROR, Severity.FATAL)]
    if not any(d.code == "W_OUTSIDE_SUBSET" for d in kept):
        first = own_errors[0]
        kept.append(Diagnostic(
            phase=first.phase,
            severity=Severity.NOTE,
            message="this file uses C our analyzer does not model, so the system "
                    "compiler's findings are the ones that apply here",
            file=first.file,
            position=first.position,
            code="W_OUTSIDE_SUBSET",
            origin=Origin.FRONTEND,
            snippet=first.snippet,
            meta={"demoted_from": sorted({d.code for d in own_errors})},
        ))
    return kept, True


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
            quality: bool = False, ai: bool = False) -> Analysis:
    """Analyze one file.

    ``quality`` adds the analysis pass: it needs a parseable program, so it is
    skipped when the file does not compile -- there is no useful complexity
    figure for a function the parser could not finish reading.

    ``ai`` adds the AI code analysis stage beside the rules. It is optional
    and silent when the model is unavailable.
    """
    result = Analysis(source=source)

    own, available = _run_own_frontend(source)
    result.frontend_available = available

    clang_findings = clang_runner.analyze(source)
    own, demoted = _demote_subset_artifacts(own, clang_findings)
    result.subset_limited = demoted
    result.diagnostics.extend(own)

    seen = {d.key for d in own}
    for diag in clang_findings:
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

        if ai:
            for diag in quality_ai.analyze(source):
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

    # Scored only when the quality pass actually ran. A program that does not
    # compile never reaches that pass, and scoring it on its compile errors
    # alone produced a 90 "excellent" for code that does not build -- the
    # weighted mean had almost nothing to deduct from. No score is the honest
    # answer there: fix the errors, then the quality analysis has something to
    # measure.
    if quality and result.file_metrics is not None:
        result.score = quality_score.compute(result.diagnostics)

    return result
