"""The quality and security rule engine -- step 6.

Each rule reads the numbers from metrics.py and emits a Diagnostic when a
threshold is crossed. Three properties are deliberate:

  * Every rule has a published threshold and a weight. Nothing is scored by
    asking a model for a number, so every point of the final score can be
    traced to a named rule and defended.
  * Every rule carries its own explanation and fix, like the compiler rules
    in nlpcc/explain/rules.py. These findings skip that table -- they arrive
    already explained.
  * A rule fires on real code, not on style. Correct code must produce
    nothing: tests/corpus/clean.c failing silent is a test, not an aspiration.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..diagnostics import Diagnostic, Origin, Phase, Position, Severity
from . import metrics
from .metrics import FileMetrics, FunctionMetrics

# The five dimensions from the project's objectives.
PERFORMANCE = "performance"
READABILITY = "readability"
MAINTAINABILITY = "maintainability"
SECURITY = "security"
PRACTICES = "practices"


@dataclass(frozen=True)
class QualityRule:
    code: str
    dimension: str
    penalty: int              # points off that dimension, per occurrence
    severity: Severity
    title: str
    explanation: str
    fix: str
    critical: bool = False    # caps the overall score regardless of other dimensions


RULES: dict[str, QualityRule] = {r.code: r for r in (
    QualityRule(
        code="S_UNSAFE_FUNCTION", dimension=SECURITY, penalty=40,
        severity=Severity.ERROR, critical=True,
        title="This function cannot be used safely",
        explanation=(
            "gets(), strcpy(), strcat() and sprintf() write as many bytes as their "
            "input contains and have no parameter for the size of the destination. "
            "Any input longer than the buffer overwrites whatever follows it in "
            "memory, which is both a crash and the classic exploitable "
            "vulnerability. gets() was removed from the C standard in C11 for "
            "precisely this reason."
        ),
        fix="Use the length-limited form: fgets() instead of gets(), snprintf() "
            "instead of sprintf(), strncat() instead of strcat().",
    ),
    QualityRule(
        code="S_UNCHECKED_ALLOCATION", dimension=SECURITY, penalty=25,
        severity=Severity.WARNING, critical=True,
        title="An allocation is used without checking it succeeded",
        explanation=(
            "malloc() returns NULL when it cannot satisfy the request. Nothing in "
            "this function compares the result against NULL before using it, so on "
            "a failed allocation the very next line writes through a null pointer "
            "and the program crashes. The failure is rare on a desktop and routine "
            "under memory pressure, which is what makes it dangerous."
        ),
        fix="Test the pointer against NULL immediately after allocating, and handle "
            "the failure before using it.",
    ),
    QualityRule(
        code="S_LEAKED_ALLOCATION", dimension=SECURITY, penalty=12,
        severity=Severity.WARNING,
        title="Memory is allocated and never released",
        explanation=(
            "This function allocates memory and contains no matching free(). C has "
            "no garbage collector, so the block stays reserved until the process "
            "exits. In a short program this is harmless; in anything that runs in a "
            "loop or as a service, it grows without limit until the machine is out "
            "of memory."
        ),
        fix="free() the block when you are finished with it, on every path out of "
            "the function, including the error paths.",
    ),
    QualityRule(
        code="M_HIGH_COMPLEXITY", dimension=MAINTAINABILITY, penalty=20,
        severity=Severity.WARNING,
        title="This function has too many independent paths",
        explanation=(
            "Cyclomatic complexity counts the independent routes through a "
            "function: one, plus one for every if, loop, case and short-circuit "
            "operator. It is also the minimum number of test cases needed to cover "
            "the function. Past about ten, nobody can hold all the paths in their "
            "head at once, and the untested ones are where bugs live."
        ),
        fix="Extract the independent decisions into separate named functions.",
    ),
    QualityRule(
        code="M_TOO_MANY_PARAMETERS", dimension=MAINTAINABILITY, penalty=12,
        severity=Severity.WARNING,
        title="This function takes too many parameters",
        explanation=(
            "A long parameter list is hard to call correctly, because C checks only "
            "the types and not the order. Two adjacent parameters of the same type "
            "can be swapped at the call site and the program still compiles."
        ),
        fix="Group the related parameters into a struct, or split the function.",
    ),
    QualityRule(
        code="M_GLOBAL_STATE", dimension=MAINTAINABILITY, penalty=10,
        severity=Severity.WARNING,
        title="A mutable global variable",
        explanation=(
            "Any function in the program can read or change this variable, so its "
            "value at any moment depends on everything that ran before. That makes "
            "the functions touching it impossible to test in isolation and the bugs "
            "it causes impossible to localise."
        ),
        fix="Pass the value as a parameter, or mark it const if it never changes.",
    ),
    QualityRule(
        code="R_DEEP_NESTING", dimension=READABILITY, penalty=18,
        severity=Severity.WARNING,
        title="The code is nested too deeply",
        explanation=(
            "Each level of nesting is another condition the reader has to keep true "
            "in their head to understand the innermost line. Past three levels, "
            "that line's actual precondition is a conjunction nobody reconstructs "
            "correctly on a first read."
        ),
        fix="Return or continue early to handle the exceptional cases first, which "
            "flattens the main path to the left margin.",
    ),
    QualityRule(
        code="R_LONG_FUNCTION", dimension=READABILITY, penalty=14,
        severity=Severity.WARNING,
        title="This function is too long to read at once",
        explanation=(
            "A function longer than a screen cannot be checked by reading it: the "
            "reader has to scroll, and loses the beginning before reaching the end. "
            "Length is also a symptom -- a function this size is usually doing "
            "several separable jobs."
        ),
        fix="Split it into named functions, one per job it currently does.",
    ),
    QualityRule(
        code="R_OPAQUE_NAMES", dimension=READABILITY, penalty=16,
        severity=Severity.WARNING,
        title="The names in this function say nothing",
        explanation=(
            "Several variables here are one or two characters long. A name is the "
            "cheapest documentation available and the only one that cannot go out "
            "of date; single letters spend that for nothing. Loop counters like i "
            "and j are the accepted exception because their scope is two lines."
        ),
        fix="Rename each variable after the value it holds.",
    ),
    QualityRule(
        code="R_NO_COMMENTS", dimension=READABILITY, penalty=10,
        severity=Severity.NOTE,
        title="A file of this size with almost no comments",
        explanation=(
            "Comments are not needed to restate what the code does, but a file this "
            "long usually contains at least one decision whose reason is not visible "
            "in the code -- why a bound is what it is, why a case is handled "
            "specially. That reasoning is lost the moment the author forgets it."
        ),
        fix="Comment the decisions whose reason the code cannot show: why, not what.",
    ),
    QualityRule(
        code="R_MAGIC_NUMBER", dimension=READABILITY, penalty=8,
        severity=Severity.NOTE,
        title="The same unexplained number appears repeatedly",
        explanation=(
            "A literal repeated across a file is a constant that was never named. "
            "Nothing links the occurrences, so when the value has to change, every "
            "site must be found by eye, and the one that is missed becomes a bug "
            "that only shows up for certain inputs."
        ),
        fix="Give it a name once with #define or a const, and use that everywhere.",
    ),
    QualityRule(
        code="P_ALLOCATION_IN_LOOP", dimension=PERFORMANCE, penalty=20,
        severity=Severity.WARNING,
        title="Memory is allocated inside a loop",
        explanation=(
            "Each iteration asks the allocator for memory again. Allocation is one "
            "of the most expensive operations available, often hundreds of times the "
            "cost of the arithmetic around it, and if the block is not freed in the "
            "same iteration the program's memory use grows with the loop count."
        ),
        fix="Allocate once before the loop and reuse the block inside it.",
    ),
    QualityRule(
        code="P_CALL_IN_LOOP_CONDITION", dimension=PERFORMANCE, penalty=18,
        severity=Severity.WARNING,
        title="A function is called on every iteration of this loop",
        explanation=(
            "A call in the loop's condition runs once per iteration, not once. For "
            "strlen() this is the textbook case: the condition walks the whole "
            "string to find its length each time round, turning a loop over n "
            "characters into n-squared work."
        ),
        fix="Compute the value once into a variable before the loop and test that.",
    ),
    QualityRule(
        code="C_TOO_MANY_RETURNS", dimension=PRACTICES, penalty=8,
        severity=Severity.NOTE,
        title="This function returns from many different places",
        explanation=(
            "Numerous exit points make it hard to be sure that cleanup happens on "
            "every one of them. In C, where the cleanup is manual, this is how a "
            "free() or a fclose() comes to be missing on exactly one path."
        ),
        fix="Consolidate the exits, or make sure every one runs the same cleanup.",
    ),
)}

# Thresholds, gathered here so they can be reviewed in one place.
MAX_COMPLEXITY = 10
MAX_PARAMETERS = 5
MAX_NESTING = 3
MAX_FUNCTION_LINES = 50
MAX_RETURNS = 4
MIN_SHORT_NAMES = 4              # before R_OPAQUE_NAMES fires
MIN_LINES_FOR_COMMENTS = 30      # a short file needs no comments
MIN_COMMENT_DENSITY = 0.05
MAGIC_NUMBER_REPEATS = 3         # the same literal this many times

UNSAFE_FUNCTIONS = {
    "gets": "gets", "strcpy": "strcpy", "strcat": "strcat",
    "sprintf": "sprintf", "vsprintf": "vsprintf",
}
COSTLY_IN_CONDITION = frozenset({"strlen", "strnlen", "wcslen"})


def _diagnostic(rule: QualityRule, source: Path, line: int, detail: str,
                snippet: str = "", **meta) -> Diagnostic:
    return Diagnostic(
        phase=Phase.QUALITY,
        severity=rule.severity,
        message=detail,
        file=str(source),
        position=Position(line) if line else None,
        code=rule.code,
        origin=Origin.RULES,
        snippet=snippet,
        explanation=rule.explanation,
        fix=rule.fix,
        meta={"title": rule.title, "explained_by": "quality-rules",
              "dimension": rule.dimension, "penalty": rule.penalty,
              "critical": rule.critical, **meta},
    )


def _source_line(source: Path, line: int) -> str:
    try:
        return source.read_text(errors="replace").splitlines()[line - 1].strip()
    except (OSError, IndexError):
        return ""


def _check_function(fn: FunctionMetrics, source: Path, loop_lines: set[int],
                    condition_calls: list[tuple[str, int]]) -> list[Diagnostic]:
    out: list[Diagnostic] = []

    if fn.complexity > MAX_COMPLEXITY:
        out.append(_diagnostic(
            RULES["M_HIGH_COMPLEXITY"], source, fn.line,
            f"'{fn.name}' has a cyclomatic complexity of {fn.complexity} "
            f"(threshold {MAX_COMPLEXITY})",
            _source_line(source, fn.line), measured=fn.complexity))

    if fn.parameters > MAX_PARAMETERS:
        out.append(_diagnostic(
            RULES["M_TOO_MANY_PARAMETERS"], source, fn.line,
            f"'{fn.name}' takes {fn.parameters} parameters (threshold {MAX_PARAMETERS})",
            _source_line(source, fn.line), measured=fn.parameters))

    if fn.max_nesting > MAX_NESTING:
        out.append(_diagnostic(
            RULES["R_DEEP_NESTING"], source, fn.line,
            f"'{fn.name}' nests {fn.max_nesting} levels deep (threshold {MAX_NESTING})",
            _source_line(source, fn.line), measured=fn.max_nesting))

    if fn.length > MAX_FUNCTION_LINES:
        out.append(_diagnostic(
            RULES["R_LONG_FUNCTION"], source, fn.line,
            f"'{fn.name}' is {fn.length} lines long (threshold {MAX_FUNCTION_LINES})",
            _source_line(source, fn.line), measured=fn.length))

    short = fn.short_names
    if len(short) >= MIN_SHORT_NAMES:
        names = ", ".join(sorted({name for name, _ in short}))
        out.append(_diagnostic(
            RULES["R_OPAQUE_NAMES"], source, fn.line,
            f"'{fn.name}' names {len(short)} variables with one or two letters: {names}",
            _source_line(source, fn.line), measured=len(short), names=names))

    if fn.returns > MAX_RETURNS:
        out.append(_diagnostic(
            RULES["C_TOO_MANY_RETURNS"], source, fn.line,
            f"'{fn.name}' has {fn.returns} return statements (threshold {MAX_RETURNS})",
            _source_line(source, fn.line), measured=fn.returns))

    for name in fn.calls:
        if name in UNSAFE_FUNCTIONS:
            out.append(_diagnostic(
                RULES["S_UNSAFE_FUNCTION"], source, fn.line,
                f"'{fn.name}' calls {name}()", _source_line(source, fn.line),
                function=name))

    for line in fn.allocations:
        if line in loop_lines:
            out.append(_diagnostic(
                RULES["P_ALLOCATION_IN_LOOP"], source, line,
                "memory is allocated inside a loop", _source_line(source, line)))

    # An allocation is unchecked when nothing in the function tests any pointer
    # against NULL. Coarse on purpose: a function that checks one pointer and
    # not another is not worth a false positive here.
    if fn.allocations and not fn.null_checked:
        out.append(_diagnostic(
            RULES["S_UNCHECKED_ALLOCATION"], source, fn.allocations[0],
            f"'{fn.name}' allocates memory and never tests the result",
            _source_line(source, fn.allocations[0])))

    if fn.allocations and fn.frees == 0 and fn.returns <= 1:
        out.append(_diagnostic(
            RULES["S_LEAKED_ALLOCATION"], source, fn.allocations[0],
            f"'{fn.name}' allocates memory and never frees it",
            _source_line(source, fn.allocations[0])))

    for name, line in condition_calls:
        if name in COSTLY_IN_CONDITION and fn.line <= line <= fn.end_line:
            out.append(_diagnostic(
                RULES["P_CALL_IN_LOOP_CONDITION"], source, line,
                f"{name}() is called in a loop condition", _source_line(source, line),
                function=name))

    return out


def analyze(source: Path, measured: FileMetrics | None = None) -> list[Diagnostic]:
    """Every quality and security finding for one file."""
    from . import astjson

    fm = measured if measured is not None else metrics.measure(source)
    out: list[Diagnostic] = []

    # Lines that sit inside a loop body, and calls that sit in a loop condition.
    loop_lines: set[int] = set()
    condition_calls: list[tuple[str, int]] = []
    for top in astjson.parse(source):
        for node in top.walk():
            if node.kind not in astjson.LOOPS:
                continue
            for inner in node.walk():
                loop_lines.add(inner.line)
            for child in node.children:
                for call in child.find("CallExpr"):
                    name = metrics._called_name(call)
                    if name:
                        condition_calls.append((name, call.line))

    for fn in fm.functions:
        out.extend(_check_function(fn, source, loop_lines, condition_calls))

    for name, line in fm.globals:
        out.append(_diagnostic(
            RULES["M_GLOBAL_STATE"], source, line,
            f"'{name}' is a mutable global", _source_line(source, line), name=name))

    if fm.code_lines >= MIN_LINES_FOR_COMMENTS and fm.comment_density < MIN_COMMENT_DENSITY:
        out.append(_diagnostic(
            RULES["R_NO_COMMENTS"], source, 0,
            f"{fm.code_lines} lines of code and {fm.comment_lines} comment lines",
            measured=round(fm.comment_density, 3)))

    repeats = Counter(value for value, _ in fm.magic_numbers)
    for value, count in sorted(repeats.items()):
        if count >= MAGIC_NUMBER_REPEATS:
            line = next(ln for val, ln in fm.magic_numbers if val == value)
            out.append(_diagnostic(
                RULES["R_MAGIC_NUMBER"], source, line,
                f"the literal {value} appears {count} times",
                _source_line(source, line), measured=count, value=value))

    return out
