"""Terminal interface. `python -m nlpcc analyze program.c`"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import pipeline
from .diagnostics import Severity, to_json
from .explain import rules

_COLOR = {Severity.FATAL: "\033[1;31m", Severity.ERROR: "\033[31m",
          Severity.WARNING: "\033[33m", Severity.NOTE: "\033[36m"}
_RESET = "\033[0m"


# The sanitizer's own report is already a Diagnostic with an explanation above;
# echoing its raw form too is just noise in the demo.
_SANITIZER_NOISE = ("runtime error:", "UndefinedBehaviorSanitizer", "SUMMARY: Undefined",
                    "AddressSanitizer", "SUMMARY: Address")


def _strip_sanitizer_noise(text: str) -> str:
    return "\n".join(line for line in text.splitlines()
                     if not any(marker in line for marker in _SANITIZER_NOISE))


def _render(result: pipeline.Analysis, color: bool, verbose: bool) -> None:
    src = result.source
    print(f"\n  {src}")
    if not result.frontend_available:
        print("  (own front end not implemented yet -- findings below are from the system compiler)")

    if not result.diagnostics:
        print("\n  No problems found.\n")
    for diag in result.diagnostics:
        tint = _COLOR.get(diag.severity, "") if color else ""
        reset = _RESET if color else ""
        where = f"line {diag.position.line}" if diag.position else "file"
        title = diag.meta.get("title") or diag.message
        print(f"\n  {tint}{diag.severity.value.upper()}{reset}  {where}  ({diag.phase.value})")
        print(f"  {title}")
        if diag.snippet:
            print(f"      {diag.snippet.strip()}")
        for related in diag.meta.get("related", []):
            print(f"  also, line {related['line']}: {related['message']}")
            if related.get("snippet"):
                print(f"      {related['snippet'].strip()}")
        if diag.explanation:
            print(f"\n  What it means: {diag.explanation}")
            print(f"  How to fix it: {diag.fix}")
        else:
            print(f"  Compiler said: {diag.message}")
            print("  (no explanation rule for this yet -- see nlpcc/explain/rules.py)")
        if verbose:
            print(f"  [code={diag.code} origin={diag.origin.value}]")

    if result.run is not None:
        print("\n  --- program output ---")
        if result.run.stdout:
            print(result.run.stdout.rstrip())
        stderr = _strip_sanitizer_noise(result.run.stderr)
        if stderr:
            print(stderr.rstrip())
        if result.run.exit_code is not None:
            print(f"  (exit status {result.run.exit_code})")

    gaps = rules.unexplained(result.diagnostics)
    if gaps and not verbose:
        print(f"\n  {len(gaps)} finding(s) have no explanation rule yet.")
    print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="nlpcc",
        description="NLP-driven compiler assistant: compile, explain, analyze.")
    sub = ap.add_subparsers(dest="command", required=True)

    an = sub.add_parser("analyze", help="compile a program and explain its problems")
    an.add_argument("source", type=Path)
    an.add_argument("--run", action="store_true", help="also execute it if it compiles")
    an.add_argument("--stdin", default="", help="input to feed the program when running")
    an.add_argument("--json", action="store_true", help="machine-readable output")
    an.add_argument("-v", "--verbose", action="store_true", help="show codes and origins")
    an.add_argument("--no-color", action="store_true")

    sub.add_parser("rules", help="list the explanation rules")

    # Placeholders so the command surface matches docs/scope.md. Each prints
    # what it will do and exits; they light up as their step lands.
    for name, help_text, step in (
        ("score", "rate code quality across the five dimensions", "step 6"),
        ("optimize", "suggest improvements for a chosen objective", "step 7"),
        ("report", "write the full analysis report", "step 8"),
        ("generate", "write C from a natural-language description", "step 9"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("source", type=Path, nargs="?")
        p.set_defaults(_todo=(help_text, step))

    args = ap.parse_args(argv)

    if getattr(args, "_todo", None):
        help_text, step = args._todo
        print(f"'{args.command}' will {help_text} -- not implemented yet ({step}).")
        return 2

    if args.command == "rules":
        for rule in rules.RULES:
            print(f"  {rule.code or rule.pattern}\n      {rule.title}")
        print(f"\n  {len(rules.RULES)} rules.")
        return 0

    if not args.source.exists():
        print(f"nlpcc: no such file: {args.source}", file=sys.stderr)
        return 1

    result = pipeline.analyze(args.source, run=args.run, stdin=args.stdin)
    if args.json:
        print(json.dumps({
            "source": str(result.source),
            "ok": result.ok,
            "frontend_available": result.frontend_available,
            "diagnostics": json.loads(to_json(result.diagnostics)),
        }, indent=2))
    else:
        _render(result, color=not args.no_color and sys.stdout.isatty(),
                verbose=args.verbose)
    return 0 if result.ok else 1
