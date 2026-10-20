"""Our own C front end: lexer -> parser -> semantic analyzer.

Three phases, one shared output. Each phase takes what the previous one
produced and adds ``Diagnostic`` objects to the pile; none of them raises on
bad input, because a program that cannot be lexed still has to produce a
report. The subset they cover is the grammar in docs/scope.md.

``FRONTEND_CODES`` is the closed inventory of diagnostic codes the three
phases can emit. It exists so two invariants can be tested rather than hoped
for (tests/test_frontend.py):

  * every code here has an explanation rule in nlpcc/explain/rules.py -- the
    project's premise is that nothing reaches the user as raw jargon;
  * every code appearing in this package's source is listed here, so a new
    check cannot be written without being registered and explained.
"""

from __future__ import annotations

# code -> the phase that emits it. Keep in step with the source; the test
# above fails if the two drift apart.
FRONTEND_CODES: dict[str, str] = {
    # phase 1 -- lexical
    "E_UNTERMINATED_STRING": "lexical",
    "E_UNTERMINATED_CHAR": "lexical",
    "E_UNTERMINATED_COMMENT": "lexical",
    "E_STRAY_CHARACTER": "lexical",
    "E_MALFORMED_NUMBER": "lexical",
    # phase 2 -- syntax
    "E_MISSING_SEMICOLON": "syntax",
    "E_MISSING_PAREN": "syntax",
    "E_MISSING_BRACE": "syntax",
    "E_MISSING_BRACKET": "syntax",
    "E_MISSING_COLON": "syntax",
    "E_UNEXPECTED_TOKEN": "syntax",
    "E_EXPECTED_EXPRESSION": "syntax",
    "W_OUTSIDE_SUBSET": "syntax",
    # phase 3 -- semantic
    "E_UNDECLARED_IDENTIFIER": "semantic",
    "E_REDECLARATION": "semantic",
    "E_TYPE_MISMATCH": "semantic",
    "E_WRONG_ARG_COUNT": "semantic",
    "E_NOT_A_FUNCTION": "semantic",
    "E_VOID_VARIABLE": "semantic",
    "E_BREAK_OUTSIDE_LOOP": "semantic",
    "W_MISSING_RETURN": "semantic",
    "W_UNINITIALIZED_READ": "semantic",
}
