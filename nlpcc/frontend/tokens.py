"""Token vocabulary for the C subset defined in docs/scope.md.

Keep this list closed. Every keyword here must be handled by the parser and
the semantic analyzer; anything outside it is a deliberate "outside our
subset" diagnostic, not a crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class TokenKind(Enum):
    # literals and names
    IDENTIFIER = auto()
    INT_LITERAL = auto()
    FLOAT_LITERAL = auto()
    CHAR_LITERAL = auto()
    STRING_LITERAL = auto()
    # structure
    KEYWORD = auto()
    PUNCTUATOR = auto()
    OPERATOR = auto()
    # control
    PREPROCESSOR = auto()   # #include / #define -- recorded, not expanded
    COMMENT = auto()        # kept for comment-density metrics
    EOF = auto()


KEYWORDS: frozenset[str] = frozenset({
    "int", "float", "char", "double", "void", "long", "short", "unsigned", "signed",
    "if", "else", "while", "for", "do", "switch", "case", "default",
    "break", "continue", "return",
    "struct", "typedef", "const", "sizeof",
})

# Longest-first: the lexer must try '<<=' before '<<' before '<'.
OPERATORS: tuple[str, ...] = (
    "<<=", ">>=", "...",
    "++", "--", "->", "<<", ">>", "<=", ">=", "==", "!=", "&&", "||",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
    "+", "-", "*", "/", "%", "=", "<", ">", "!", "&", "|", "^", "~", "?",
)

PUNCTUATORS: tuple[str, ...] = ("(", ")", "{", "}", "[", "]", ";", ",", ":", ".")


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.line}:{self.column}\t{self.kind.name}\t{self.text!r}"
