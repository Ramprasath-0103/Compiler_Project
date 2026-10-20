"""Phase 3 -- semantic analysis. AST + symbol table -> meaning checks.

STATUS: skeleton. Must detect, at minimum, the cases in
tests/corpus/semantic: undeclared identifiers, redefinition in the same
scope, type mismatch in initialization and assignment, wrong argument count,
non-void function with no return, and use of an uninitialized local.

The scope stack is a list of dicts; push on block entry, pop on exit, and
record each symbol's declaration site so diagnostics can say "declared here".
"""

from __future__ import annotations

from ..diagnostics import Diagnostic
from .ast_nodes import TranslationUnit


class SemanticAnalyzer:
    def __init__(self, unit: TranslationUnit, filename: str = "<input>") -> None:
        self.unit = unit
        self.filename = filename
        self.diagnostics: list[Diagnostic] = []
        self.scopes: list[dict[str, object]] = [{}]

    def analyze(self) -> list[Diagnostic]:
        raise NotImplementedError("step 4: implement the semantic analyzer")
