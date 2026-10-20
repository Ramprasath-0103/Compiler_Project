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


# Every diagnostic the project can produce needs an entry here:
# tests/test_corpus.py fails loudly for any corpus diagnostic no rule explains,
# and tests/test_frontend.py fails for any code our own front end can emit and
# has not explained.
#
# -- our own front end ----------------------------------------------------
# These come first on purpose. Matching is first-rule-wins, and an exact code
# match from our own analyzer is always a better explanation than a regex over
# a compiler's message text -- our message is written for the rule that
# explains it. One rule per code in nlpcc.frontend.FRONTEND_CODES; the test
# tests/test_frontend.py::test_every_frontend_code_has_a_rule enforces that.
RULES: tuple[Rule, ...] = (
    # phase 1 -- lexical
    Rule(
        code="E_UNTERMINATED_STRING",
        title="A string is never closed",
        explanation=(
            "A string literal starts at a double quote and has to end at another one "
            "on the same line. This one reaches the end of the line still open, so "
            "everything written after it -- the closing bracket, the semicolon, even "
            "the next few lines -- is being read as text inside the string rather than "
            "as program. That is why a single missing quote usually produces several "
            "errors that look unrelated: fix the quote and the others normally go with it."
        ),
        fix="Add the closing '\\\"' at the end of the text, before the ')' or ';'.",
    ),
    Rule(
        code="E_UNTERMINATED_CHAR",
        title="A character literal is never closed",
        explanation=(
            "Single quotes hold exactly one character, as in 'a' or '\\\\n', and both "
            "quotes have to be on the same line. The opening quote here has no partner, "
            "so the rest of the line is being read as the inside of a character. A "
            "common cause is using single quotes where a string was meant: 'hello' is "
            "not a string in C, \\\"hello\\\" is."
        ),
        fix="Close the literal with \\\"'\\\", or switch to double quotes if you meant a string.",
    ),
    Rule(
        code="E_UNTERMINATED_COMMENT",
        title="A block comment is never closed",
        explanation=(
            "A comment opened with '/*' runs until the matching '*/', across as many "
            "lines as it takes. This one never finds its '*/', so the whole rest of the "
            "file -- every function below this point -- has been swallowed into the "
            "comment and is not being compiled at all."
        ),
        fix="Add '*/' where the comment was meant to end.",
    ),
    Rule(
        code="E_STRAY_CHARACTER",
        title="A character C does not use",
        explanation=(
            "This character has no meaning in C outside a string, a character literal "
            "or a comment, so the compiler cannot turn it into anything. It is usually "
            "a typo, a stray key press, or a character that came in from a word "
            "processor or a web page -- a curly quote instead of a straight one, or a "
            "non-breaking space that looks exactly like an ordinary space."
        ),
        fix="Delete the character, or retype the line in a plain-text editor.",
    ),
    Rule(
        code="E_MALFORMED_NUMBER",
        title="A number with letters in it",
        explanation=(
            "A number literal may end in a type suffix such as 'u', 'L' or 'f', but "
            "nothing else: digits followed by letters are not a value C can read. This "
            "is most often a missing operator ('2x' where '2 * x' was meant) or a "
            "missing space."
        ),
        fix="Put an operator or a space between the number and the name after it.",
    ),

    # phase 2 -- syntax
    Rule(
        code="E_MISSING_SEMICOLON",
        title="A statement is missing its semicolon",
        explanation=(
            "Every statement and declaration in C ends with a semicolon; it is how the "
            "compiler knows where one statement stops and the next begins. Reading on "
            "from here it found the start of something new with no ';' in between. The "
            "position reported is the end of the unfinished statement, which is where "
            "the semicolon belongs -- so look at that line, not the one the next error "
            "mentions."
        ),
        fix="Add ';' at the end of the statement this points at.",
    ),
    Rule(
        code="E_MISSING_PAREN",
        title="A parenthesis is never closed",
        explanation=(
            "Parentheses have to balance: every '(' needs its ')'. The compiler reached "
            "a point where the parenthesis had to close -- the '{' that opens a block, "
            "or the end of the statement -- and did not find one, so it cannot tell "
            "where the condition or the argument list ends. The attached note points at "
            "the '(' that is still waiting."
        ),
        fix="Add the ')' that closes the condition or the argument list the note points at.",
    ),
    Rule(
        code="E_MISSING_BRACE",
        title="A block is never closed",
        explanation=(
            "Braces have to balance too: each '{' opens a block that some '}' must "
            "close. The file ended with one still open, which is why this is reported "
            "at the last line rather than where the mistake is. The note points at the "
            "'{' that was left open, and that is the place to look. Indenting "
            "consistently makes this kind of gap visible before the compiler finds it."
        ),
        fix="Add the '}' that closes the block the note points at.",
    ),
    Rule(
        code="E_MISSING_BRACKET",
        title="A square bracket is never closed",
        explanation=(
            "An array size and an array index both sit inside '[' and ']', and the "
            "closing bracket is missing here. Without it the compiler cannot tell which "
            "part of the line is the index and which is the rest of the expression."
        ),
        fix="Add the ']' after the index or the array size.",
    ),
    Rule(
        code="E_MISSING_COLON",
        title="A '? :' expression is incomplete",
        explanation=(
            "The conditional operator always comes in two halves: 'condition ? value if "
            "true : value if false'. The '?' is here but the ':' is not, so the second "
            "value is missing and the expression has no result when the condition is "
            "false."
        ),
        fix="Add ':' and the value to use when the condition is false.",
    ),
    Rule(
        code="E_UNEXPECTED_TOKEN",
        title="This is not allowed here",
        explanation=(
            "The grammar of C allows only certain things at this position -- a "
            "declaration at the top level of a file, a statement inside a function -- "
            "and what is written here is none of them. Very often the real mistake is "
            "slightly earlier: a missing semicolon, brace or parenthesis above this "
            "line leaves the compiler in the middle of the previous construct, and this "
            "is where it finally gives up."
        ),
        fix="Check the lines just above for a missing ';', ')' or '}' before changing this one.",
    ),
    Rule(
        code="E_EXPECTED_EXPRESSION",
        title="A value was expected here",
        explanation=(
            "At this position C needs an expression: something that produces a value, "
            "such as a number, a variable, or a call. What is written here cannot start "
            "one -- usually because an operator is missing its right-hand side, or "
            "because an earlier mistake (an unclosed quote is the classic one) has left "
            "the compiler reading the line as something other than what you typed."
        ),
        fix="Fix the first error reported in the file, then re-run; this one often goes with it.",
    ),
    Rule(
        code="W_OUTSIDE_SUBSET",
        title="Outside the part of C we analyze",
        explanation=(
            "This is valid C, but it uses a construct this project's own analyzer does "
            "not model -- the subset it covers is listed in docs/scope.md, and struct, "
            "union, switch, do/while, typedef and goto are deliberately outside it. "
            "Nothing is wrong with your program because of this note. The file is still "
            "checked by the system compiler, so real errors in it are still reported; "
            "only our own extra checks skip this construct."
        ),
        fix="Nothing to fix. Rewrite it with if/while/for if you want our own analyzer to see inside.",
    ),

    # phase 3 -- semantic
    Rule(
        code="E_UNDECLARED_IDENTIFIER",
        title="A name that was never declared",
        explanation=(
            "C insists that every variable and function is declared before it is used, "
            "because the declaration is what gives the name a type. No declaration of "
            "this name is visible at this point, so the compiler has nothing to work "
            "with. The three usual causes, in order of how often they happen: the name "
            "is spelled differently from the declaration; the declaration is missing "
            "altogether; or the name was declared inside a different pair of braces -- "
            "a loop body, an if-block -- and stopped existing at the closing brace."
        ),
        fix="Correct the spelling, or declare the name before this line and outside any inner block.",
    ),
    Rule(
        code="E_REDECLARATION",
        title="This name is declared twice",
        explanation=(
            "Two declarations of the same name in the same scope leave the compiler with "
            "no way to decide which one a use of the name refers to, so it refuses both. "
            "The note points at the first declaration. Declaring the same name in two "
            "different blocks is legal and does not cause this; the two here are in the "
            "same one."
        ),
        fix="Remove the second declaration, or rename one of the two variables.",
    ),
    Rule(
        code="E_TYPE_MISMATCH",
        title="The value's type does not fit",
        explanation=(
            "Every variable in C has a type that fixes what its bytes mean, and the "
            "value here means something different. The common pair is a text literal "
            "and a number: \\\"5\\\" is an address pointing at the characters '5' and a "
            "terminator, while 5 is the quantity five. Neither converts to the other by "
            "itself, so the compiler will not guess. The same applies to a pointer and "
            "an int: one is a location, the other is a number."
        ),
        fix="Use a value of the variable's own type, or declare the variable with the type the value has.",
    ),
    Rule(
        code="E_WRONG_ARG_COUNT",
        title="The call passes the wrong number of arguments",
        explanation=(
            "A function's declaration fixes exactly how many arguments it takes. C has "
            "no optional parameters and no default values, so a call with a different "
            "number is an error rather than something filled in for you -- the function "
            "would otherwise read a parameter that was never given a value. The note "
            "points at the declaration that sets the count."
        ),
        fix="Pass one argument for each parameter in the declaration, in the same order.",
    ),
    Rule(
        code="E_NOT_A_FUNCTION",
        title="This name is not a function",
        explanation=(
            "Writing a name followed by '(' asks the compiler to call that name, but the "
            "note shows it declared as a variable. Either the call was meant for a "
            "different name, or the parentheses are accidental -- in C they mean 'call "
            "this', not 'group this', when they follow a name directly."
        ),
        fix="Call the function you meant, or remove the parentheses after the variable.",
    ),
    Rule(
        code="E_VOID_VARIABLE",
        title="A variable cannot have type void",
        explanation=(
            "'void' means 'no value at all'. It is useful as a return type, for a "
            "function that computes nothing to hand back, but a variable has to hold "
            "something, so there is no such thing as a void variable."
        ),
        fix="Give the variable the type of the value it is meant to hold, such as int or double.",
    ),
    Rule(
        code="E_BREAK_OUTSIDE_LOOP",
        title="'break' with no loop to break out of",
        explanation=(
            "'break' leaves the nearest enclosing loop, and 'continue' jumps to that "
            "loop's next iteration. Written outside any loop there is nothing for them "
            "to act on, so the compiler cannot give them a meaning. This usually means a "
            "closing brace sits above the statement where it was meant to sit below it, "
            "which has put the statement outside the loop body."
        ),
        fix="Move the statement inside the loop body, or check where the loop's '}' is.",
    ),
    Rule(
        code="W_MISSING_RETURN",
        title="A function that promises a value does not return one",
        explanation=(
            "This function's return type is not void, which is a promise to hand a value "
            "back to whoever calls it. There is a path through the function that reaches "
            "the closing brace without a 'return', and on that path the caller receives "
            "whatever happened to be left in the place a return value is passed -- a "
            "different number on different runs. The note points at the function's "
            "declaration, where the promised type is written."
        ),
        fix="End every path with 'return' and a value, including the fall-through at the closing brace.",
    ),
    Rule(
        code="W_UNINITIALIZED_READ",
        title="A variable is read before it is set",
        explanation=(
            "Declaring a local variable in C reserves space for it and nothing more: it "
            "does not start at zero. Until something is written to it, it holds whatever "
            "bytes were already at that address, so reading it produces an unpredictable "
            "value that can differ between runs, between machines, and between a debug "
            "and a release build. That is why a program with this bug can appear to work "
            "and then fail somewhere else entirely."
        ),
        fix="Give the variable a value where you declare it, for example 'int n = 0;'.",
    ),

    # -- the system compiler, as ground truth ------------------------------
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
    """Attach an explanation to one diagnostic, in place, and return it.

    Idempotent, and it has to be: quality findings arrive from
    nlpcc/quality/rules.py already carrying their own explanation, and the
    pipeline runs this pass over every diagnostic regardless of source.
    Without the early return, no rule here matches a quality code and the
    finding gets stamped "none" -- marking an explained finding unexplained.
    """
    if diag.explanation:
        return diag

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
