"""Token vocabulary for the C subset defined in docs/scope.md.

Keep this list closed. Every keyword here must be handled by the parser and
the semantic analyzer; anything outside it is a deliberate "outside our
subset" diagnostic, not a crash.

The keyword set is deliberately wider than the grammar: ``struct``, ``switch``
and friends are recognised as keywords precisely so the parser can say "this
is outside the analyzed subset" instead of mistaking them for identifiers and
producing nonsense about an undeclared name.
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
    # storage classes: clean.c declares `static int add(...)`
    "static", "extern",
    # recognised only to be refused clearly -- see Unsupported in ast_nodes.py
    "union", "enum", "goto", "register", "volatile",
})

# Types the grammar accepts, and the specifiers that may precede one.
TYPE_KEYWORDS: frozenset[str] = frozenset({
    "void", "char", "int", "float", "double", "long", "short", "unsigned", "signed",
})
STORAGE_KEYWORDS: frozenset[str] = frozenset({"static", "extern", "const", "volatile", "register"})

# Keywords that are real C but outside the analyzed subset. Hitting one is a
# W_OUTSIDE_SUBSET note, never a syntax error: the program is not wrong, we
# simply do not model it. clang covers these files.
UNSUPPORTED_KEYWORDS: frozenset[str] = frozenset({
    "struct", "union", "enum", "typedef", "switch", "case", "default", "do", "goto",
})

# Longest-first: the lexer must try '<<=' before '<<' before '<'.
OPERATORS: tuple[str, ...] = (
    "<<=", ">>=", "...",
    "++", "--", "->", "<<", ">>", "<=", ">=", "==", "!=", "&&", "||",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
    "+", "-", "*", "/", "%", "=", "<", ">", "!", "&", "|", "^", "~", "?",
)

PUNCTUATORS: tuple[str, ...] = ("(", ")", "{", "}", "[", "]", ";", ",", ":", ".")

ASSIGNMENT_OPERATORS: frozenset[str] = frozenset({
    "=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=",
})


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.line}:{self.column}\t{self.kind.name}\t{self.text!r}"

    # -- predicates the parser reads like prose --------------------------
    def is_keyword(self, *names: str) -> bool:
        return self.kind is TokenKind.KEYWORD and self.text in names

    def is_punct(self, *texts: str) -> bool:
        return self.kind is TokenKind.PUNCTUATOR and self.text in texts

    def is_operator(self, *texts: str) -> bool:
        return self.kind is TokenKind.OPERATOR and self.text in texts

    @property
    def end_column(self) -> int:
        """One past the token's last character -- where a missing ';' belongs."""
        return self.column + max(len(self.text), 1)
