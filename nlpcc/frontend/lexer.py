"""Phase 1 -- lexical analysis. Source text -> tokens.

Hand-written scanner, one character of lookahead, no regular expressions: the
token classes in tokens.py are simple enough that a table-driven scanner would
hide more than it saves, and the point of this file is that the lexical phase
is visibly ours.

Design notes, kept because they are decisions and not description:
  * Line and column are tracked on every token -- the Diagnostic record needs
    both, and a report that can only say "line 7" is half a report.
  * Comments become COMMENT tokens rather than being discarded; the
    readability metric counts comment density and cannot recover them later.
    The parser skips them, so nothing downstream has to care.
  * Unterminated string/char literals, unterminated block comments and stray
    characters are LEXICAL diagnostics, emitted and then recovered from. This
    function never raises: a file that cannot be lexed still has to produce a
    report, because "it is broken" is exactly what the user came to hear.
"""

from __future__ import annotations

from pathlib import Path

from ..diagnostics import Diagnostic, Origin, Phase, Position, Severity
from .tokens import KEYWORDS, OPERATORS, PUNCTUATORS, Token, TokenKind

_IDENT_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENT_BODY = _IDENT_START | set("0123456789")
_DIGITS = set("0123456789")
_HEX_DIGITS = set("0123456789abcdefABCDEF")
# Trailing type suffixes: 10u, 3.5f, 100LL.
_NUMBER_SUFFIX = set("uUlLfF")


class Lexer:
    def __init__(self, text: str, filename: str = "<input>") -> None:
        self.text = text
        self.filename = filename
        self.diagnostics: list[Diagnostic] = []
        self._pos = 0
        self._line = 1
        self._column = 1
        self._lines = text.splitlines()

    # -- character-level helpers -----------------------------------------
    def _at_end(self) -> bool:
        return self._pos >= len(self.text)

    def _peek(self, offset: int = 0) -> str:
        index = self._pos + offset
        return self.text[index] if index < len(self.text) else ""

    def _advance(self) -> str:
        char = self.text[self._pos]
        self._pos += 1
        if char == "\n":
            self._line += 1
            self._column = 1
        else:
            self._column += 1
        return char

    def _starts_with(self, text: str) -> bool:
        return self.text.startswith(text, self._pos)

    # -- the scanner ------------------------------------------------------
    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while not self._at_end():
            char = self._peek()
            if char in " \t\r\n\f\v":
                self._advance()
                continue
            line, column = self._line, self._column

            if self._starts_with("//") or self._starts_with("/*"):
                tokens.append(self._comment(line, column))
            elif char == "#" and self._only_blanks_before():
                tokens.append(self._preprocessor_directive(line, column))
            elif char in _IDENT_START:
                tokens.append(self._identifier_or_keyword(line, column))
            elif char in _DIGITS or (char == "." and self._peek(1) in _DIGITS):
                tokens.append(self._number(line, column))
            elif char == '"':
                tokens.append(self._string_literal(line, column))
            elif char == "'":
                tokens.append(self._char_literal(line, column))
            else:
                token = self._operator_or_punctuator(line, column)
                if token is not None:
                    tokens.append(token)

        tokens.append(Token(TokenKind.EOF, "", *self._eof_position()))
        return tokens

    def _only_blanks_before(self) -> bool:
        """True when nothing but whitespace precedes this '#' on its line.

        A '#' anywhere else is a stray character, not a directive.
        """
        start = self.text.rfind("\n", 0, self._pos) + 1
        return self.text[start:self._pos].strip() == ""

    def _eof_position(self) -> tuple[int, int]:
        """Where EOF is, for "this file ended with a block still open".

        Reported on the last line that has content rather than on the blank
        line after it, which is where a trailing newline would otherwise put
        it -- pointing a user at an empty line explains nothing.
        """
        for index in range(len(self._lines) - 1, -1, -1):
            if self._lines[index].strip():
                return index + 1, len(self._lines[index]) + 1
        return max(self._line, 1), max(self._column, 1)

    # -- token scanners ---------------------------------------------------
    def _comment(self, line: int, column: int) -> Token:
        start = self._pos
        if self._starts_with("//"):
            while not self._at_end() and self._peek() != "\n":
                self._advance()
        else:
            self._advance()                       # '/'
            self._advance()                       # '*'
            while not self._at_end() and not self._starts_with("*/"):
                self._advance()
            if self._at_end():
                self.error("E_UNTERMINATED_COMMENT",
                           "block comment is never closed before the end of the file",
                           line, column)
            else:
                self._advance()                   # '*'
                self._advance()                   # '/'
        return Token(TokenKind.COMMENT, self.text[start:self._pos], line, column)

    def _preprocessor_directive(self, line: int, column: int) -> Token:
        start = self._pos
        while not self._at_end() and self._peek() != "\n":
            # A '\' at end of line continues the directive onto the next one.
            if self._peek() == "\\" and self._peek(1) in ("\n", "\r"):
                self._advance()
                self._advance()
                continue
            self._advance()
        return Token(TokenKind.PREPROCESSOR, self.text[start:self._pos], line, column)

    def _identifier_or_keyword(self, line: int, column: int) -> Token:
        start = self._pos
        while not self._at_end() and self._peek() in _IDENT_BODY:
            self._advance()
        text = self.text[start:self._pos]
        kind = TokenKind.KEYWORD if text in KEYWORDS else TokenKind.IDENTIFIER
        return Token(kind, text, line, column)

    def _number(self, line: int, column: int) -> Token:
        start = self._pos
        is_float = False
        if self._peek() == "0" and self._peek(1) in ("x", "X"):
            self._advance()
            self._advance()
            while not self._at_end() and self._peek() in _HEX_DIGITS:
                self._advance()
        else:
            while not self._at_end() and self._peek() in _DIGITS:
                self._advance()
            if self._peek() == "." and self._peek(1) != ".":
                is_float = True
                self._advance()
                while not self._at_end() and self._peek() in _DIGITS:
                    self._advance()
            if self._peek() in ("e", "E") and (
                    self._peek(1) in _DIGITS
                    or (self._peek(1) in "+-" and self._peek(2) in _DIGITS)):
                is_float = True
                self._advance()
                if self._peek() in "+-":
                    self._advance()
                while not self._at_end() and self._peek() in _DIGITS:
                    self._advance()
        while not self._at_end() and self._peek() in _NUMBER_SUFFIX:
            if self._peek() in ("f", "F"):
                is_float = True
            self._advance()
        text = self.text[start:self._pos]
        if not self._at_end() and self._peek() in _IDENT_START:
            # 123abc: lex the whole run so the parser sees one bad token
            # rather than a number followed by a mystery identifier.
            while not self._at_end() and self._peek() in _IDENT_BODY:
                self._advance()
            text = self.text[start:self._pos]
            self.error("E_MALFORMED_NUMBER",
                       f"'{text}' is not a valid number: a digit is followed by letters",
                       line, column)
        kind = TokenKind.FLOAT_LITERAL if is_float else TokenKind.INT_LITERAL
        return Token(kind, text, line, column)

    def _string_literal(self, line: int, column: int) -> Token:
        start = self._pos
        self._advance()                           # opening quote
        while not self._at_end() and self._peek() not in ('"', "\n"):
            if self._peek() == "\\" and self._peek(1):
                self._advance()
            self._advance()
        if self._peek() == '"':
            self._advance()
        else:
            # Recovery: take the rest of the line as the literal's contents and
            # resume on the next line. Treating the remainder of the *file* as
            # string, which is what C formally does, turns one missing quote
            # into a report about nothing else.
            self.error("E_UNTERMINATED_STRING",
                       "string literal is missing its closing '\"'", line, column)
        return Token(TokenKind.STRING_LITERAL, self.text[start:self._pos], line, column)

    def _char_literal(self, line: int, column: int) -> Token:
        start = self._pos
        self._advance()                           # opening quote
        while not self._at_end() and self._peek() not in ("'", "\n"):
            if self._peek() == "\\" and self._peek(1):
                self._advance()
            self._advance()
        if self._peek() == "'":
            self._advance()
        else:
            self.error("E_UNTERMINATED_CHAR",
                       "character literal is missing its closing \"'\"", line, column)
        return Token(TokenKind.CHAR_LITERAL, self.text[start:self._pos], line, column)

    def _operator_or_punctuator(self, line: int, column: int) -> Token | None:
        for operator in OPERATORS:                # longest-first by construction
            if self._starts_with(operator):
                for _ in operator:
                    self._advance()
                return Token(TokenKind.OPERATOR, operator, line, column)
        char = self._peek()
        if char in PUNCTUATORS:
            self._advance()
            return Token(TokenKind.PUNCTUATOR, char, line, column)
        self._advance()
        self.error("E_STRAY_CHARACTER",
                   f"{char!r} is not a character C uses outside a string or comment",
                   line, column)
        return None

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
