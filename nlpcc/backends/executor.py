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

import os
import re
import resource
import signal
import subprocess
import sys
import time
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
    sanitized: bool = True       # false when the sanitizer could not be linked
    diagnostics: list[Diagnostic] = field(default_factory=list)

    # What it cost to run. Measured, not estimated -- and worth reading with
    # the sanitizer in mind: -fsanitize=undefined instruments every operation,
    # so these numbers describe the checked build, not an optimised one.
    wall_ms: float = 0.0         # wall-clock time of the run
    cpu_ms: float = 0.0          # user + system CPU actually used
    peak_memory_kb: int = 0      # peak resident set of the child process
    build_ms: float = 0.0        # time the compiler took
    output_bytes: int = 0

    def measurements(self) -> dict:
        return {
            "wall_ms": round(self.wall_ms, 1),
            "cpu_ms": round(self.cpu_ms, 1),
            "build_ms": round(self.build_ms, 1),
            "peak_memory_kb": self.peak_memory_kb,
            "output_bytes": self.output_bytes,
            "sanitized": self.sanitized,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
        }


# Generous on purpose. At 5s a crashing program on a loaded machine
# intermittently reported R_TIMEOUT instead of the segfault that actually
# happened -- the timeout masked the real finding. A hung program still gets
# caught; it just takes a few seconds longer to say so.
DEFAULT_TIMEOUT = 15.0


@dataclass
class _Completed:
    """What subprocess.run would have returned."""

    returncode: int
    stdout: str
    stderr: str


def _run_measured(binary: Path, stdin: str, timeout: float, cwd: str):
    """Run the program and measure *it*, not the compiler that built it.

    resource.getrusage(RUSAGE_CHILDREN) is a high-water mark across every
    child this process has reaped, so after clang has run it reports clang's
    footprint -- 36 MB for a program that uses a few hundred kilobytes.
    os.wait4 returns the usage of one specific child, which is the number we
    actually want.

    Output goes to files rather than pipes so that reading it cannot deadlock
    with waiting for the process; communicate() would reap the child and take
    its usage with it.
    """
    out_path = Path(cwd) / "stdout.txt"
    err_path = Path(cwd) / "stderr.txt"

    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE,
                                   stdout=out, stderr=err, cwd=cwd)
        try:
            if stdin:
                process.stdin.write(stdin.encode())
            if process.stdin:
                process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

        deadline = time.monotonic() + timeout
        usage = None
        status = None
        while True:
            pid, status, usage = os.wait4(process.pid, os.WNOHANG)
            if pid != 0:
                break
            if time.monotonic() > deadline:
                process.kill()
                os.wait4(process.pid, 0)
                process.returncode = -9
                raise subprocess.TimeoutExpired(str(binary), timeout)
            time.sleep(0.005)

    if os.WIFSIGNALED(status):
        process.returncode = -os.WTERMSIG(status)
    else:
        process.returncode = os.WEXITSTATUS(status)

    def read(path: Path) -> str:
        try:
            return path.read_text(errors="replace")
        except OSError:
            return ""

    return (_Completed(process.returncode, read(out_path), read(err_path)), usage)


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
        def build_with(flags: tuple[str, ...]):
            return subprocess.run(
                [cc, "-g", "-O0", "-Wall", *flags, str(source), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)

        sanitized = bool(sanitize)
        flags = (*SANITIZE_FLAGS, *extra_flags) if sanitized else extra_flags
        build_started = time.perf_counter()
        build = build_with(flags)
        build_ms = (time.perf_counter() - build_started) * 1000

        if build.returncode != 0 and sanitized:
            # The sanitizer is an enhancement, not a requirement. Its runtime
            # can be missing or unlinkable -- a GNU gcc on macOS cannot link
            # Apple's, and a partial toolchain install breaks it too -- and
            # that must not be reported as the user's program failing to
            # build. Retry without it and say the runtime checks are off.
            plain = build_with(tuple(extra_flags))
            if plain.returncode == 0:
                build, sanitized = plain, False
            elif not parse_output(build.stderr, source):
                # Neither build produced a diagnostic we can attribute to the
                # source, so the toolchain itself is the problem.
                return RunResult(compiled=False, stderr=build.stderr, diagnostics=[
                    Diagnostic(phase=Phase.RUNTIME, severity=Severity.FATAL,
                               message="the program could not be built: "
                                       + (plain.stderr.strip().splitlines() or
                                          ["the compiler gave no reason"])[-1][:300],
                               file=str(source), code="nlpcc:build_failed",
                               origin=Origin.EXECUTOR,
                               fix="Check the compiler install: "
                                   "`xcode-select --install` on macOS.",
                               explanation="This is a problem with the compiler "
                                           "on this machine, not with the program.")])

        if build.returncode != 0:
            # Compilation failed: the diagnostics are the result.
            return RunResult(compiled=False, stderr=build.stderr,
                             diagnostics=parse_output(build.stderr, source))

        started = time.perf_counter()
        try:
            run, usage = _run_measured(binary, stdin, timeout, tmp)
        except subprocess.TimeoutExpired:
            return RunResult(compiled=True, timed_out=True, build_ms=build_ms,
                             wall_ms=(time.perf_counter() - started) * 1000,
                             sanitized=sanitized, diagnostics=[
                Diagnostic(phase=Phase.RUNTIME, severity=Severity.ERROR,
                           message=f"the program did not finish within {timeout:g}s",
                           file=str(source), code="R_TIMEOUT", origin=Origin.EXECUTOR,
                           meta={"timeout": timeout})])
        wall_ms = (time.perf_counter() - started) * 1000

    # ru_maxrss is bytes on macOS and kilobytes on Linux. Reporting the raw
    # number would be off by 1024x on one of them.
    divisor = 1024 if sys.platform == "darwin" else 1
    cpu_ms = (usage.ru_utime + usage.ru_stime) * 1000 if usage else 0.0
    peak_kb = int(usage.ru_maxrss / divisor) if usage else 0

    result = RunResult(compiled=True, stdout=run.stdout, stderr=run.stderr,
                       sanitized=sanitized,
                       wall_ms=wall_ms, cpu_ms=cpu_ms, build_ms=build_ms,
                       peak_memory_kb=peak_kb,
                       output_bytes=len(run.stdout.encode()) + len(run.stderr.encode()))
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
