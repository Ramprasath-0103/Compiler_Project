"""Phase 2 -- syntax analysis. Tokens -> AST, with recovery.

STATUS: skeleton. Recursive descent; one method per grammar rule, named after
the rule so the code reads like docs/scope.md's grammar.

Recovery matters more than speed here: on an unexpected token, emit a SYNTAX
diagnostic and skip to the next ';' or '}' so one missing semicolon does not
cascade into forty findings.
"""

from __future__ import annotations

from ..diagnostics import Diagnostic
from .ast_nodes import TranslationUnit
from .tokens import Token


class Parser:
    def __init__(self, tokens: list[Token], filename: str = "<input>") -> None:
        self.tokens = tokens
        self.filename = filename
        self.diagnostics: list[Diagnostic] = []
        self._index = 0

    def parse(self) -> TranslationUnit:
        raise NotImplementedError("step 4: implement the parser")
