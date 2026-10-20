"""The explanation layer, rules first.

Deterministic, offline, and reviewable: a diagnostic code maps to a plain
English explanation and a concrete fix. No model call, so the same input
always produces the same report and the demo works with the network unplugged.

The LLM layer (step 5b, ``nlpcc/explain/llm.py``) sits *behind* this one: it
is consulted only for diagnostics no rule matched, and whatever it returns is
labelled as model-generated in the report. Never the other way round.

Adding a rule is the main way this project gets better. Each entry needs:
  code         stable id from the Diagnostic (ours, or clang's slug/flag)
  title        four or five words, the headline a beginner reads first
  explanation  what went wrong and why the compiler cares -- no jargon
  fix          what to actually type instead
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..diagnostics import Diagnostic


@dataclass(frozen=True)
class Rule:
    code: str                 # exact code match, or "" when matching by pattern
    title: str
    explanation: str
    fix: str
    pattern: str = ""         # regex against the raw message, used when code is ""

    def matches(self, diag: Diagnostic) -> bool:
        if self.code and diag.code == self.code:
            return True
        if self.pattern and re.search(self.pattern, diag.message, re.I):
            return True
        return False


# Seed set. Target is one rule per corpus case -- see tests/test_corpus.py,
# which fails loudly for any corpus diagnostic that no rule explains.
RULES: tuple[Rule, ...] = (
    Rule(
        code="clang:expected_semicolon_at_end_of_declaration",
        pattern=r"expected ';'",
        title="A statement is missing its semicolon",
        explanation=(
            "In C every declaration and statement ends with a semicolon. The compiler "
            "read to the end of this line, found no ';', and cannot tell where this "
            "statement stops and the next one begins. The line number it reports is "
            "where the semicolon was expected, so the mistake is usually on that line "
            "or the one just above it."
        ),
        fix="Add ';' at the end of the statement the compiler points at.",
    ),
    Rule(
        code="",
        pattern=r"use of undeclared identifier|undeclared \(first use",
        title="You used a name the compiler has never seen",
        explanation=(
            "C requires every variable and function to be declared before it is used. "
            "This name has no declaration that is visible at this point, so the compiler "
            "does not know its type or whether it exists at all. The usual causes are a "
            "typo in the name, a missing declaration, or a variable declared inside a "
            "different pair of braces and therefore out of scope here."
        ),
        fix="Declare the name before this line, or correct the spelling to match the declaration.",
    ),
    Rule(
        code="",
        pattern=r"incompatible type|incompatible pointer|conversion from",
        title="The value's type does not fit the variable",
        explanation=(
            "The variable on the left and the value on the right are different types, "
            "and C has no automatic conversion between them that preserves the meaning. "
            "Assigning a string to a number, or a number to a pointer, falls into this "
            "category: the bytes simply do not represent the same thing."
        ),
        fix="Change the variable's type to match the value, or convert the value explicitly.",
    ),
    Rule(
        code="-Wuninitialized",
        title="This variable is read before it is given a value",
        explanation=(
            "A local variable in C does not start at zero. Until you assign to it, it "
            "holds whatever bytes happened to be at that memory address, so reading it "
            "gives an unpredictable value that may differ between runs and between "
            "machines. This is why a program can appear to work and then fail elsewhere."
        ),
        fix="Give the variable a value at the point you declare it.",
    ),
    Rule(
        code="-Wreturn-type",
        title="A function that promises a value does not return one",
        explanation=(
            "The function's declared return type is not void, so every path through it "
            "must end in a 'return' with a value. At least one path reaches the closing "
            "brace without returning, and the caller then receives a meaningless value."
        ),
        fix="Add a 'return' with a value to every branch, including the final fall-through.",
    ),
    Rule(
        code="-Wformat",
        title="The format string does not match the arguments",
        explanation=(
            "printf-style functions do not check their own arguments; they trust the "
            "format string to describe what follows. When a '%d' is handed a float, or "
            "a '%s' is handed an integer, the function reads the wrong number of bytes "
            "and interprets them as the wrong type, printing nonsense or crashing."
        ),
        fix="Match each conversion to its argument: %d for int, %f for double, %s for char*.",
    ),
    Rule(
        code="R_SEGFAULT",
        title="The program crashed reading or writing bad memory",
        explanation=(
            "A segmentation fault means the program asked the operating system for "
            "memory that is not part of it. In practice this is almost always one of: "
            "an array index outside the array, a pointer that was never assigned, a "
            "pointer used after the memory was freed, or a NULL return from malloc "
            "that was not checked."
        ),
        fix="Check every index against the array length, and every pointer against NULL, before use.",
    ),
    Rule(
        code="R_ARITHMETIC",
        title="The program divided by zero",
        explanation=(
            "Integer division and the remainder operator are undefined when the "
            "right-hand side is zero, and the processor raises a fault rather than "
            "producing a number. Unlike floating point, there is no 'inf' result to "
            "fall back on."
        ),
        fix="Test the divisor for zero before dividing and handle that case explicitly.",
    ),
    Rule(
        code="R_TIMEOUT",
        title="The program never finished",
        explanation=(
            "The program was still running when the time limit expired. Either it is "
            "waiting for input that will never arrive, or a loop's condition never "
            "becomes false -- commonly because the variable the condition tests is "
            "never changed inside the loop."
        ),
        fix="Check that every loop modifies the variable its condition depends on.",
    ),
    Rule(
        code="S_UNSAFE_INPUT",
        title="This function cannot be used safely",
        explanation=(
            "gets() and strcpy() write as many bytes as the input contains, with no "
            "way to tell them how large the destination is. Any input longer than the "
            "buffer overwrites whatever follows it in memory, which is both a crash and "
            "the classic exploitable vulnerability. gets() was removed from C11 for "
            "this reason."
        ),
        fix="Use fgets() with the buffer size, or snprintf()/strncpy() with an explicit limit.",
    ),
    Rule(
        code="R_DIVISION_BY_ZERO",
        title="This line divided by zero",
        explanation=(
            "Integer division and the remainder operator have no defined result when "
            "the right-hand side is zero. On some processors this raises a fault and "
            "kills the program; on others, including Apple Silicon, it quietly produces "
            "a meaningless number and the program carries on with corrupt data. The "
            "second case is worse, because nothing visibly fails."
        ),
        fix="Test the divisor for zero before dividing and handle that case explicitly.",
    ),
    Rule(
        code="R_INDEX_OUT_OF_BOUNDS",
        title="An array index went past the end of the array",
        explanation=(
            "An array of N elements has valid indices 0 through N-1, and C does not "
            "check this for you. Reading past the end returns whatever is in the "
            "adjacent memory; writing past the end overwrites it, corrupting unrelated "
            "variables or the function's own return address. A loop written with '<=' "
            "where it should use '<' is the most common cause."
        ),
        fix="Use 'i < length' as the loop condition, not 'i <= length'.",
    ),
    Rule(
        code="R_NULL_DEREFERENCE",
        title="The program used a pointer that holds NULL",
        explanation=(
            "A NULL pointer deliberately points at no object, and reading or writing "
            "through it is invalid. Either the pointer was never assigned a real "
            "address, or it came from a function such as malloc() or fopen() that "
            "returns NULL on failure and whose result was not checked."
        ),
        fix="Check the pointer against NULL before dereferencing it.",
    ),
    Rule(
        code="R_SIGNED_OVERFLOW",
        title="An integer calculation exceeded the type's range",
        explanation=(
            "A signed int holds roughly -2.1 to +2.1 billion. Going outside that range "
            "is undefined behaviour, not a wrap-around you can rely on: the compiler is "
            "entitled to assume it never happens and optimise accordingly, so the result "
            "can be arbitrary."
        ),
        fix="Use a wider type such as long long, or check the operands before the operation.",
    ),
    Rule(
        code="R_FORTIFY_ABORT",
        title="A safety check stopped a buffer overflow",
        explanation=(
            "The C library detected that a write would run past the end of the "
            "destination buffer and terminated the program on purpose, before the "
            "overflow could corrupt memory. The program was genuinely about to write "
            "more bytes than the destination can hold."
        ),
        fix="Size the destination for the largest possible input, or use a length-limited "
            "function such as snprintf() or strncpy().",
    ),
    Rule(
        code="R_ABORT",
        title="The program aborted itself",
        explanation=(
            "abort() was called, either directly, by a failed assert(), or by a libc "
            "check that detected memory corruption -- a double free, a heap overflow, or "
            "a bad pointer passed to free(). The abort is the symptom; the corruption "
            "happened earlier."
        ),
        fix="Run the program under a debugger or with -fsanitize=address to find the "
            "earlier operation that corrupted memory.",
    ),
    Rule(
        code="R_NONZERO_EXIT",
        title="The program finished with a failure status",
        explanation=(
            "Returning a non-zero status from main() is the convention for reporting "
            "failure to whatever launched the program. This is not necessarily a bug -- "
            "a program may return non-zero on purpose -- but when it is unintended it "
            "usually means a 'return' was reached that the author did not expect, or a "
            "function that promises a value fell off its end."
        ),
        fix="Trace which return produced this status; return 0 from main() on success.",
    ),
    Rule(
        code="clang:expected_rparen",
        title="A closing parenthesis is missing",
        explanation=(
            "Every '(' needs a matching ')'. The compiler reached a point where the "
            "parenthesis had to close -- usually the '{' that starts a block, or the end "
            "of the line -- and did not find one. The 'to match this' note points at the "
            "opening parenthesis the compiler is still waiting on."
        ),
        fix="Add the ')' that closes the condition or argument list.",
    ),
    Rule(
        code="clang:expected_rbrace",
        title="A closing brace is missing",
        explanation=(
            "Every '{' needs a matching '}'. The compiler reached the end of the file "
            "with a block still open, which is why this error points at the last line "
            "rather than the real mistake. The 'to match this' note points at the "
            "unclosed '{', and that is where to look."
        ),
        fix="Close the block the note points at; consistent indentation makes the gap visible.",
    ),
    Rule(
        code="",
        pattern=r"missing terminating|unterminated|invalid-pp-token|invalid pp",
        title="A string or character literal is never closed",
        explanation=(
            "A string literal must open and close with a double quote on the same line. "
            "The compiler read to the end of the line still inside the string, so "
            "everything after it -- including the rest of your program -- is being "
            "treated as text. This is why one unclosed quote produces a cascade of "
            "unrelated-looking errors; fix the quote and the rest usually disappear."
        ),
        fix="Add the closing quote at the end of the literal.",
    ),
    Rule(
        code="",
        pattern=r"too few arguments|too many arguments",
        title="The call passes the wrong number of arguments",
        explanation=(
            "The function's declaration fixes how many arguments it takes, and C "
            "requires the call to match exactly -- there are no optional or default "
            "parameters. The 'declared here' note points at the declaration that sets "
            "the expected count."
        ),
        fix="Pass exactly the arguments the declaration lists, in the same order.",
    ),
    Rule(
        code="-Wunused-variable",
        title="This variable is declared but never used",
        explanation=(
            "Nothing reads this variable after it is declared, so it has no effect on "
            "what the program does. Harmless in itself, but it is often the visible "
            "trace of a real mistake: a value computed and then forgotten, or a "
            "variable the author meant to return."
        ),
        fix="Remove the declaration, or use the value it was meant to hold.",
    ),
    Rule(
        code="clang:expected_expression",
        title="The compiler expected a value here and found none",
        explanation=(
            "At this position the grammar requires an expression -- something that "
            "produces a value. What follows is a token that cannot start one. This is "
            "usually collateral damage from an earlier mistake, such as an unclosed "
            "quote or a missing operand, so fix the first error reported and re-run "
            "before chasing this one."
        ),
        fix="Fix the earliest error in the file first; this one often resolves with it.",
    ),
)

def explain(diag: Diagnostic) -> Diagnostic:
    """Attach an explanation to one diagnostic, in place, and return it."""
    for rule in RULES:
        if rule.matches(diag):
            diag.explanation = rule.explanation
            diag.fix = rule.fix
            diag.meta["title"] = rule.title
            diag.meta["explained_by"] = "rules"
            return diag
    diag.meta["explained_by"] = "none"   # candidate for the LLM layer
    return diag


def explain_all(diags: Iterable[Diagnostic]) -> list[Diagnostic]:
    return [explain(d) for d in diags]


def unexplained(diags: Iterable[Diagnostic]) -> list[Diagnostic]:
    """Diagnostics no rule covered -- the backlog of rules still to write."""
    return [d for d in diags if d.meta.get("explained_by") in (None, "none")]
