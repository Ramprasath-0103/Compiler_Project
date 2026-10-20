"""Ground-truth front end: run a real compiler and normalize what it says.

Our own front end (``nlpcc.frontend``) only understands the C subset named in
docs/scope.md. clang understands everything, so we run both and compare: any
diagnostic clang reports that we miss is a gap in our analyzer, and the corpus
tests make that visible.

Nothing here parses C. It shells out, scrapes the diagnostic lines, and emits
``Diagnostic`` objects.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
from pathlib import Path

from ..diagnostics import Diagnostic, Origin, Phase, Position, Severity

# file:line:col: severity: message [-Wflag]
_DIAG = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<severity>fatal error|error|warning|note):\s+(?P<message>.*)$"
)
_FLAG = re.compile(r"\s*\[(-W[A-Za-z0-9_+-]+)\]\s*$")

# Which phase a clang message belongs to. Ordered -- first match wins.
_PHASE_HINTS: list[tuple[re.Pattern[str], Phase]] = [
    (re.compile(r"unterminated|invalid (digit|character)|missing terminating"), Phase.LEXICAL),
    (re.compile(r"^expected|extraneous|unexpected|expected_[a-z]"), Phase.SYNTAX),
    (re.compile(r"undeclared|incompatible|conflicting|too (few|many) arguments"
                r"|not a function|redefinition|incomplete type|no member"), Phase.SEMANTIC),
]

# Quoted punctuation carries the whole meaning of messages like "expected ')'",
# so name it before slugging -- otherwise every such message collapses to
# the single useless code "clang:expected".
_TOKEN_NAMES = {
    ";": "semicolon", ",": "comma", ":": "colon", ".": "dot",
    "(": "lparen", ")": "rparen", "{": "lbrace", "}": "rbrace",
    "[": "lbracket", "]": "rbracket", "=": "equals", "*": "star",
}

# clang first, deliberately. Every diagnostic flag below is a clang spelling
# and the output parser is written against clang's format, so preferring a
# real GNU gcc -- which a Homebrew install puts on PATH as gcc-15 -- meant
# sending it options it does not have:
#
#   -ferror-limit=0         gcc calls this -fmax-errors
#   -fno-caret-diagnostics  gcc calls this -fno-diagnostics-show-caret
#   -fno-color-diagnostics  gcc calls this -fno-diagnostics-color
#
# GNU gcc on macOS also cannot link Apple's UndefinedBehaviorSanitizer
# runtime, so -fsanitize=undefined fails at link time there. On a Mac with
# Homebrew gcc installed, the old order broke both the syntax check and the
# sanitized run while a plain clang test worked perfectly -- which is exactly
# how it looks from the outside.
CC_CANDIDATES = ("clang", "cc", "gcc", "gcc-15", "gcc-14")

CLANG_FLAGS = (
    "-fsyntax-only",
    "-fno-caret-diagnostics",
    "-fno-color-diagnostics",
    "-ferror-limit=0",      # report every error, do not stop at the 20th
    "-Wall",
    "-Wextra",
)

GCC_FLAGS = (
    "-fsyntax-only",
    "-fno-diagnostics-show-caret",
    "-fno-diagnostics-color",
    "-fmax-errors=0",       # the gcc spelling of the same thing
    "-Wall",
    "-Wextra",
)

DEFAULT_FLAGS = CLANG_FLAGS      # kept for callers that pass flags themselves


def find_compiler() -> str | None:
    """The C compiler to use, clang for preference."""
    for name in CC_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


@functools.lru_cache(maxsize=8)
def is_clang(compiler: str) -> bool:
    """Ask the compiler what it is.

    The name proves nothing in either direction: on macOS ``gcc`` is a clang
    shim, and on Linux ``cc`` is usually real gcc. Only --version is honest.
    """
    try:
        proc = subprocess.run([compiler, "--version"], capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return "clang" in (proc.stdout + proc.stderr).lower()


def flags_for(compiler: str) -> tuple[str, ...]:
    """Diagnostic flags this compiler actually understands."""
    return CLANG_FLAGS if is_clang(compiler) else GCC_FLAGS


def _classify(message: str, flag: str, severity: Severity) -> tuple[Phase, str]:
    """Return the phase and the stable code for one clang message."""
    for pattern, phase in _PHASE_HINTS:
        if pattern.search(message):
            break
    else:
        phase = Phase.QUALITY if severity in (Severity.WARNING, Severity.NOTE) else Phase.SEMANTIC

    if flag:
        return phase, flag
    # No warning flag (plain errors): derive a slug so the explain layer has a key.
    text = message.lower()
    for token, name in _TOKEN_NAMES.items():
        text = text.replace(f"'{token}'", f" {name} ")
    # Drop quoted identifiers and types -- they are specific to this program and
    # would give every occurrence its own code.
    text = re.sub(r"'[^']*'", " ", text)
    slug = re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:48]
    return phase, f"clang:{slug}"


def _source_line(path: Path, line: int) -> str:
    try:
        return path.read_text(errors="replace").splitlines()[line - 1].rstrip()
    except (OSError, IndexError):
        return ""


def parse_output(text: str, source: Path) -> list[Diagnostic]:
    """Scrape compiler stderr into Diagnostic objects."""
    out: list[Diagnostic] = []
    for raw in text.splitlines():
        m = _DIAG.match(raw.strip())
        if not m:
            continue
        message = m["message"].strip()
        flag_match = _FLAG.search(message)
        flag = flag_match.group(1) if flag_match else ""
        if flag_match:
            message = message[: flag_match.start()].rstrip()

        severity = Severity.FATAL if m["severity"] == "fatal error" else Severity(m["severity"])
        line = int(m["line"])

        # A note is never a finding of its own: clang emits it to elaborate on
        # the diagnostic above it ("declared here", "to match this"). Attach it
        # there so the report shows one problem with its context, not two.
        if severity is Severity.NOTE and out:
            out[-1].meta.setdefault("related", []).append(
                {"message": message, "line": line, "column": int(m["col"]),
                 "snippet": _source_line(source, line)})
            continue

        phase, code = _classify(message, flag, severity)
        out.append(
            Diagnostic(
                phase=phase,
                severity=severity,
                message=message,
                file=m["file"],
                position=Position(line, int(m["col"])),
                code=code,
                origin=Origin.CLANG,
                snippet=_source_line(source, line),
            )
        )
    return out


def analyze(source: Path, extra_flags: tuple[str, ...] = (), timeout: float = 20.0) -> list[Diagnostic]:
    """Syntax-check ``source`` with the system compiler."""
    cc = find_compiler()
    if cc is None:
        return [
            Diagnostic(
                phase=Phase.SYNTAX,
                severity=Severity.FATAL,
                message="no C compiler found on PATH (looked for: "
                        + ", ".join(CC_CANDIDATES) + ")",
                file=str(source),
                code="nlpcc:no_compiler",
                origin=Origin.CLANG,
            )
        ]
    proc = subprocess.run(
        [cc, *flags_for(cc), *extra_flags, str(source)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return parse_output(proc.stderr, source)
