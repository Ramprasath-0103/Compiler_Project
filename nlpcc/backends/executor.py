"""Compile and run a program, and turn how it died into diagnostics.

Runtime errors are the third error class on slide 5, and they are invisible to
any amount of static analysis: a division by zero or an out-of-bounds write
only shows up when the program runs. We compile to a scratch directory, run
with a wall-clock timeout, and translate the exit signal.

SECURITY: this executes user-supplied code. Right now the only containment is
a timeout and a scratch cwd, which is enough for a course demo and not enough
for anything public. Before this is ever exposed beyond the CLI, add rlimits
(CPU, address space, file size), drop network access, and run it as an
unprivileged user -- see docs/scope.md.
"""

from __future__ import annotations

import re
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..diagnostics import Diagnostic, Origin, Phase, Position, Severity
from .clang_runner import _source_line, find_compiler, parse_output

# What a fatal signal actually means, in the user's terms.
SIGNAL_MEANING: dict[int, tuple[str, str]] = {
    signal.SIGSEGV: ("R_SEGFAULT", "the program accessed memory it does not own"),
    signal.SIGFPE: ("R_ARITHMETIC", "an invalid arithmetic operation, usually division by zero"),
    signal.SIGABRT: ("R_ABORT", "the program aborted itself, usually a failed assertion "
                                "or a detected heap error"),
    signal.SIGBUS: ("R_BUS_ERROR", "the program used a misaligned or invalid address"),
    signal.SIGILL: ("R_ILLEGAL_INSTRUCTION", "the program jumped into something that is not code"),
    signal.SIGTRAP: ("R_FORTIFY_ABORT", "a libc safety check stopped the program before it "
                                        "could corrupt memory, usually a buffer overflow "
                                        "caught by a bounds-checked function"),
}

# UndefinedBehaviorSanitizer reports with a precise file:line:col, which a bare
# fatal signal does not. This is the difference between "it crashed" and "line 6
# divided by zero", so runtime analysis is sanitized by default.
SANITIZE_FLAGS = ("-fsanitize=undefined",)

_UBSAN = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+):\s+runtime error:\s+(?P<message>.*)$"
)

# message fragment -> stable code
_UBSAN_CODES: tuple[tuple[str, str], ...] = (
    ("division by zero", "R_DIVISION_BY_ZERO"),
    ("out of bounds", "R_INDEX_OUT_OF_BOUNDS"),
    ("null pointer", "R_NULL_DEREFERENCE"),
    ("signed integer overflow", "R_SIGNED_OVERFLOW"),
    ("shift exponent", "R_BAD_SHIFT"),
    ("misaligned address", "R_MISALIGNED_ACCESS"),
)


def parse_sanitizer_output(text: str, source: Path) -> list[Diagnostic]:
    """Turn UBSan's stderr into located runtime diagnostics."""
    out: list[Diagnostic] = []
    seen: set[tuple[int, str]] = set()
    for raw in text.splitlines():
        m = _UBSAN.match(raw.strip())
        if not m:
            continue
        message = m["message"].strip()
        code = next((c for frag, c in _UBSAN_CODES if frag in message.lower()),
                    "R_UNDEFINED_BEHAVIOR")
        line = int(m["line"])
        if (line, code) in seen:      # a sanitizer in a loop repeats itself
            continue
        seen.add((line, code))
        out.append(Diagnostic(
            phase=Phase.RUNTIME, severity=Severity.ERROR, message=message,
            file=m["file"], position=Position(line, int(m["col"])), code=code,
            origin=Origin.EXECUTOR,
            snippet=_source_line(source, line)))
    return out


@dataclass
class RunResult:
    compiled: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    signal_number: int | None = None
    timed_out: bool = False
    diagnostics: list[Diagnostic] = field(default_factory=list)


# Generous on purpose. At 5s a crashing program on a loaded machine
# intermittently reported R_TIMEOUT instead of the segfault that actually
# happened -- the timeout masked the real finding. A hung program still gets
# caught; it just takes a few seconds longer to say so.
DEFAULT_TIMEOUT = 15.0


def compile_and_run(source: Path, stdin: str = "", timeout: float = DEFAULT_TIMEOUT,
                    extra_flags: tuple[str, ...] = (),
                    sanitize: bool = True) -> RunResult:
    cc = find_compiler()
    if cc is None:
        return RunResult(compiled=False, diagnostics=[
            Diagnostic(phase=Phase.RUNTIME, severity=Severity.FATAL,
                       message="no C compiler found on PATH", file=str(source),
                       code="nlpcc:no_compiler", origin=Origin.EXECUTOR)])

    with tempfile.TemporaryDirectory(prefix="nlpcc-run-") as tmp:
        binary = Path(tmp) / "program"
        flags = (*SANITIZE_FLAGS, *extra_flags) if sanitize else extra_flags
        build = subprocess.run(
            [cc, "-g", "-O0", "-Wall", *flags, str(source), "-o", str(binary)],
            capture_output=True, text=True, timeout=60)
        if build.returncode != 0:
            # Compilation failed: the diagnostics are the result.
            return RunResult(compiled=False, stderr=build.stderr,
                             diagnostics=parse_output(build.stderr, source))

        try:
            run = subprocess.run([str(binary)], input=stdin, capture_output=True,
                                 text=True, timeout=timeout, cwd=tmp)
        except subprocess.TimeoutExpired:
            return RunResult(compiled=True, timed_out=True, diagnostics=[
                Diagnostic(phase=Phase.RUNTIME, severity=Severity.ERROR,
                           message=f"the program did not finish within {timeout:g}s",
                           file=str(source), code="R_TIMEOUT", origin=Origin.EXECUTOR,
                           meta={"timeout": timeout})])

    result = RunResult(compiled=True, stdout=run.stdout, stderr=run.stderr)
    result.diagnostics.extend(parse_sanitizer_output(run.stderr, source))
    if run.returncode < 0:
        signo = -run.returncode
        result.signal_number = signo
        code, meaning = SIGNAL_MEANING.get(
            signo, (f"R_SIGNAL_{signo}", f"the program was killed by signal {signo}"))
        name = signal.Signals(signo).name if signo in set(s.value for s in signal.Signals) else str(signo)
        result.diagnostics.append(
            Diagnostic(phase=Phase.RUNTIME, severity=Severity.ERROR,
                       message=f"program terminated by {name}: {meaning}",
                       file=str(source), code=code, origin=Origin.EXECUTOR,
                       meta={"signal": signo, "stderr": run.stderr[-2000:]}))
    else:
        result.exit_code = run.returncode
        if run.returncode != 0:
            result.diagnostics.append(
                Diagnostic(phase=Phase.RUNTIME, severity=Severity.WARNING,
                           message=f"program exited with status {run.returncode}",
                           file=str(source), code="R_NONZERO_EXIT", origin=Origin.EXECUTOR,
                           meta={"exit_code": run.returncode, "stderr": run.stderr[-2000:]}))
    return result
