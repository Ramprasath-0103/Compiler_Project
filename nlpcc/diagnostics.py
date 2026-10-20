"""The normalized diagnostic record.

Every analysis source in this project -- our own front end, clang, the runtime
executor, the quality rule engine -- emits ``Diagnostic`` objects and nothing
else. The explanation layer, the scorer and the report renderer all consume
this one shape, so adding a source never touches anything downstream.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterable


class Severity(str, Enum):
    FATAL = "fatal"
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"

    @property
    def rank(self) -> int:
        return {"fatal": 0, "error": 1, "warning": 2, "note": 3}[self.value]


class Phase(str, Enum):
    """Which compiler phase produced this. Maps 1:1 to slides 8-10."""

    LEXICAL = "lexical"
    SYNTAX = "syntax"
    SEMANTIC = "semantic"
    OPTIMIZATION = "optimization"
    RUNTIME = "runtime"
    QUALITY = "quality"


class Origin(str, Enum):
    """Who found it. Lets us diff our front end against clang."""

    FRONTEND = "frontend"   # our lexer / parser / semantic analyzer
    CLANG = "clang"         # ground truth
    EXECUTOR = "executor"   # crash, signal, timeout
    RULES = "rules"         # quality / security rule engine
    AI = "ai"               # the AI code analysis pass


@dataclass(frozen=True)
class Position:
    line: int
    column: int = 0

    def __str__(self) -> str:
        return f"{self.line}:{self.column}" if self.column else str(self.line)


@dataclass
class Diagnostic:
    """One finding about one program.

    ``code`` is the stable identifier the explanation layer keys on. Our own
    front end uses codes like ``E_MISSING_SEMICOLON``; clang findings carry
    their warning flag (``-Wuninitialized``) or a slug derived from the
    message when clang gives no flag.
    """

    phase: Phase
    severity: Severity
    message: str                      # raw text, as the tool produced it
    file: str = ""
    position: Position | None = None
    code: str = ""
    origin: Origin = Origin.FRONTEND
    snippet: str = ""                 # the offending source line, verbatim
    explanation: str = ""             # plain English -- filled by nlpcc.explain
    fix: str = ""                     # suggested repair -- filled by nlpcc.explain
    meta: dict[str, Any] = field(default_factory=dict)

    # -- ordering ---------------------------------------------------------
    # Findings with no line (how the program died, exit status) belong at the
    # end of the report, after everything that points at real source.
    _NO_POSITION = 1 << 30

    @property
    def sort_key(self) -> tuple:
        line = self.position.line if self.position else self._NO_POSITION
        column = self.position.column if self.position else 0
        return (self.file, line, column, self.severity.rank, self.code)

    # -- identity for tests -----------------------------------------------
    @property
    def key(self) -> tuple[int, str, str]:
        """What the corpus asserts on: line, severity, code."""
        return ((self.position.line if self.position else 0),
                self.severity.value, self.code)

    # -- serialization ----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["phase"] = self.phase.value
        d["severity"] = self.severity.value
        d["origin"] = self.origin.value
        d["position"] = ({"line": self.position.line, "column": self.position.column}
                         if self.position else None)
        return d

    def __str__(self) -> str:
        loc = f"{self.file}:{self.position}" if self.position else self.file
        head = f"{loc}: {self.severity.value}: {self.message}"
        return f"{head} [{self.code}]" if self.code else head


def sort_diagnostics(diags: Iterable[Diagnostic]) -> list[Diagnostic]:
    return sorted(diags, key=lambda d: d.sort_key)


def to_json(diags: Iterable[Diagnostic], **kwargs: Any) -> str:
    return json.dumps([d.to_dict() for d in sort_diagnostics(diags)], indent=2, **kwargs)
