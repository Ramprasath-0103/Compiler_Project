"""Phase 1 -- lexical analysis. Source text -> tokens.

STATUS: skeleton. Implement ``Lexer.tokenize`` next; see docs/scope.md for the
accepted subset and tests/corpus/syntax for the cases it must handle.

Design notes for whoever writes it:
  * Track line and column on every token -- the Diagnostic record needs both.
  * Keep comments as COMMENT tokens rather than discarding them; the
    readability metric counts comment density and cannot recover them later.
  * Unterminated string/char literals and stray characters are LEXICAL
    diagnostics, emitted and then recovered from -- never raise.
"""

from __future__ import annotations

from pathlib import Path

from ..diagnostics import Diagnostic, Origin, Phase, Position, Severity
from .tokens import Token, TokenKind


class Lexer:
    def __init__(self, text: str, filename: str = "<input>") -> None:
        self.text = text
        self.filename = filename
        self.diagnostics: list[Diagnostic] = []
        self._pos = 0
        self._line = 1
        self._column = 1

    def tokenize(self) -> list[Token]:
        raise NotImplementedError("step 4: implement the lexer")

    # Helper the whole front end shares, so every phase reports identically.
    def error(self, code: str, message: str, line: int, column: int,
              severity: Severity = Severity.ERROR,
              phase: Phase = Phase.LEXICAL) -> None:
        self.diagnostics.append(
            Diagnostic(
                phase=phase,
                severity=severity,
                message=message,
                file=self.filename,
                position=Position(line, column),
                code=code,
                origin=Origin.FRONTEND,
                snippet=self.text.splitlines()[line - 1] if line - 1 < len(self.text.splitlines()) else "",
            )
        )


def tokenize_file(path: Path) -> tuple[list[Token], list[Diagnostic]]:
    lexer = Lexer(path.read_text(errors="replace"), str(path))
    return lexer.tokenize(), lexer.diagnostics
