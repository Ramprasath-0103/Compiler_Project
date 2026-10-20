"""Check whether this machine can run the analyzer, and say what is missing.

Written for the case where it works on one laptop and not another. Each check
reports what it found and, when something is wrong, the command that fixes it
-- rather than leaving a compiler error to be interpreted.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import models
from .backends.clang_runner import CC_CANDIDATES, find_compiler, flags_for, is_clang
from .backends.executor import SANITIZE_FLAGS

OK = "ok  "
WARN = "warn"
BAD = "FAIL"

PROBE = """#include <stdio.h>
int main(void) { int a = 1, b = 0; printf("%d\\n", a / (b + 1)); return 0; }
"""


def _line(status: str, title: str, detail: str = "") -> None:
    print(f"  [{status}] {title}")
    if detail:
        for part in detail.splitlines():
            print(f"         {part}")


def report() -> int:
    print(f"\n  nlpcc doctor -- {platform.system()} {platform.machine()}, "
          f"Python {sys.version.split()[0]}\n")
    problems = 0

    # -- the compiler ---------------------------------------------------
    cc = find_compiler()
    if cc is None:
        _line(BAD, "No C compiler found",
              "Looked for: " + ", ".join(CC_CANDIDATES) + "\n"
              "Fix: xcode-select --install   (macOS)\n"
              "     sudo apt install build-essential   (Debian/Ubuntu)")
        return 1

    try:
        version = subprocess.run([cc, "--version"], capture_output=True,
                                 text=True, timeout=10).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        version = "(could not read its version)"

    family = "clang" if is_clang(cc) else "gcc"
    _line(OK, f"C compiler: {cc}", f"{version}\nDetected as: {family}")

    if family != "clang":
        _line(WARN, "This is GNU gcc, not clang",
              "Diagnostics are parsed from clang's format and the flags are\n"
              "chosen to match, so clang is the better-tested path. On macOS,\n"
              "GNU gcc also cannot link Apple's sanitizer runtime.\n"
              "Fix: install clang, or let /usr/bin/cc be found first.")
        problems += 1

    # -- can it syntax-check? -------------------------------------------
    with tempfile.TemporaryDirectory(prefix="nlpcc-doctor-") as tmp:
        source = Path(tmp) / "probe.c"
        source.write_text(PROBE)

        check = subprocess.run([cc, *flags_for(cc), str(source)],
                               capture_output=True, text=True, timeout=60)
        if check.returncode == 0:
            _line(OK, "Syntax checking works")
        else:
            _line(BAD, "The compiler rejected our diagnostic flags",
                  "Flags: " + " ".join(flags_for(cc)) + "\n"
                  + (check.stderr.strip().splitlines() or ["no output"])[0][:200])
            problems += 1

        # -- can it build and run? --------------------------------------
        binary = Path(tmp) / "probe"
        plain = subprocess.run([cc, "-g", "-O0", str(source), "-o", str(binary)],
                               capture_output=True, text=True, timeout=120)
        if plain.returncode == 0:
            _line(OK, "Building and running works")
        else:
            _line(BAD, "Cannot build a program at all",
                  (plain.stderr.strip().splitlines() or ["no output"])[-1][:200]
                  + "\nFix: xcode-select --install   (macOS)")
            problems += 1

        # -- the sanitizer ----------------------------------------------
        sanitized = subprocess.run(
            [cc, "-g", "-O0", *SANITIZE_FLAGS, str(source), "-o", str(binary)],
            capture_output=True, text=True, timeout=120)
        if sanitized.returncode == 0:
            _line(OK, "Runtime checks work (-fsanitize=undefined)",
                  "Divide by zero and out-of-bounds access will be reported\n"
                  "with a line number.")
        else:
            last = (sanitized.stderr.strip().splitlines() or ["no output"])[-1]
            _line(WARN, "The sanitizer could not be linked",
                  f"{last[:200]}\n"
                  "The analyzer still works -- it builds without the sanitizer\n"
                  "and says so. What is lost: runtime problems that do not\n"
                  "crash, such as divide by zero on Apple Silicon.\n"
                  "Usual cause: GNU gcc on macOS, or an incomplete Xcode\n"
                  "install. Fix: use clang (xcode-select --install).")
            problems += 1

    # -- the model (optional) -------------------------------------------
    provider = models.available()
    if provider is None:
        _line(WARN, "No model configured",
              models.describe() + "\n"
              "Only three features need one: AI explanations, AI code\n"
              "analysis and code generation. Everything else works without.")
    else:
        _line(OK, f"Model: {provider}")

    # -- the frontend ----------------------------------------------------
    root = Path(__file__).resolve().parent.parent
    if not shutil.which("npm"):
        _line(WARN, "npm not found", "The web interface needs Node.js. The CLI does not.")
    elif not (root / "frontend" / "node_modules").is_dir():
        _line(WARN, "Frontend dependencies not installed", "Fix: ./run.sh setup")
    else:
        _line(OK, "Web interface is installed")

    print()
    if problems == 0:
        print("  Everything needed is working.\n")
    else:
        print(f"  {problems} thing(s) above need attention. The analyzer still\n"
              "  runs unless one is marked FAIL.\n")
    return 0
