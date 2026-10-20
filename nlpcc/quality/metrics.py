"""Measurable properties of a program. No opinions here -- just numbers.

Every number a quality rule or score depends on is computed here, so each one
can be checked by hand against a source file. The rules in rules.py turn these
into findings and score.py turns them into points; keeping the measurement
separate is what makes a score defensible when someone asks where a figure
came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import astjson
from .astjson import BRANCHING, LOOPS, Node

# Short names are fine for these and nothing else.
# Loop counters and stream handles only. Deliberately excludes x/y/z and a/b/c:
# those are conventional in maths and meaningless anywhere else, and the rule
# that uses this needs several of them before it complains.
ACCEPTED_SHORT_NAMES = frozenset({"i", "j", "k", "n", "fp", "ok", "id", "ch", "fd"})

SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")
SCREAMING_CASE = re.compile(r"^[A-Z][A-Z0-9_]*$")


@dataclass
class FunctionMetrics:
    name: str
    line: int
    end_line: int
    length: int                  # lines, brace to brace
    parameters: int
    locals: int
    complexity: int              # cyclomatic
    max_nesting: int
    returns: int
    loops: int
    calls: list[str] = field(default_factory=list)
    allocations: list[int] = field(default_factory=list)   # lines of malloc-family calls
    frees: int = 0
    identifiers: list[tuple[str, int]] = field(default_factory=list)
    null_checked: set[str] = field(default_factory=set)    # names tested against NULL

    @property
    def short_names(self) -> list[tuple[str, int]]:
        """Names too short to carry meaning, conventional ones excluded.

        Judged per function, not per file: two-letter parameters in a
        two-parameter arithmetic helper are idiomatic, while five of them in
        one function means nothing was named at all. The rule that consumes
        this applies a count threshold for exactly that reason.
        """
        return [(n, line) for n, line in self.identifiers
                if len(n) <= 2 and n not in ACCEPTED_SHORT_NAMES]


@dataclass
class FileMetrics:
    path: Path
    total_lines: int
    code_lines: int
    comment_lines: int
    blank_lines: int
    functions: list[FunctionMetrics] = field(default_factory=list)
    identifiers: list[tuple[str, int]] = field(default_factory=list)   # (name, line)
    magic_numbers: list[tuple[str, int]] = field(default_factory=list)
    globals: list[tuple[str, int]] = field(default_factory=list)

    @property
    def comment_density(self) -> float:
        """Comment lines as a share of code lines. 0.1 means one in ten."""
        return (self.comment_lines / self.code_lines) if self.code_lines else 0.0

    @property
    def poorly_named(self) -> list[tuple[str, int]]:
        """Identifiers too short to say anything, excluding conventional ones."""
        return [(name, line) for name, line in self.identifiers
                if len(name) <= 2 and name not in ACCEPTED_SHORT_NAMES]

    @property
    def non_snake_case(self) -> list[tuple[str, int]]:
        return [(name, line) for name, line in self.identifiers
                if not SNAKE_CASE.match(name) and not SCREAMING_CASE.match(name)]


def _count_lines(text: str) -> tuple[int, int, int, int]:
    """Total, code, comment and blank lines, counting comments properly.

    A line counts as a comment line if it is only a comment. A line with code
    and a trailing comment counts as code -- otherwise every `x = 1; // set x`
    would inflate the comment density that the readability score depends on.
    """
    total = code = comment = blank = 0
    in_block = False
    for raw in text.splitlines():
        total += 1
        line = raw.strip()
        if not line:
            blank += 1
            continue
        if in_block:
            comment += 1
            if "*/" in line:
                in_block = False
                after = line.split("*/", 1)[1].strip()
                if after:                       # code resumed on the same line
                    comment -= 1
                    code += 1
            continue
        if line.startswith("//"):
            comment += 1
        elif line.startswith("/*"):
            comment += 1
            if "*/" not in line:
                in_block = True
            else:
                after = line.split("*/", 1)[1].strip()
                if after:
                    comment -= 1
                    code += 1
        else:
            code += 1
            if "/*" in line and "*/" not in line.split("/*", 1)[1]:
                in_block = True
    return total, code, comment, blank


def _nesting_depth(node: Node, depth: int = 0) -> int:
    """Deepest chain of nested control statements below this node."""
    deepest = depth
    for child in node.children:
        # An `else if` is a chain, not a new level: clang nests the second
        # IfStmt inside the first's else branch, which would otherwise read as
        # one level deeper per branch.
        step = 1 if child.kind in BRANCHING and child.kind != "CaseStmt" else 0
        deepest = max(deepest, _nesting_depth(child, depth + step))
    return deepest


def _complexity(body: Node) -> int:
    """Cyclomatic complexity: one path, plus one per branch point."""
    count = 1
    for node in body.walk():
        if node.kind in BRANCHING:
            count += 1
        elif node.kind == "BinaryOperator" and node.opcode in ("&&", "||"):
            count += 1          # a short-circuit is a branch
    return count


ALLOCATORS = frozenset({"malloc", "calloc", "realloc", "strdup", "aligned_alloc"})


def _normalize_call(name: str) -> str:
    """Reduce a fortified or builtin wrapper to the function the author wrote.

    With _FORTIFY_SOURCE on -- the default in Apple's SDK -- the headers
    rewrite strcpy() into __builtin___strcpy_chk(). The AST therefore never
    contains the name that appears in the source, and a rule matching
    "strcpy" silently never fires. Same shape for memcpy, sprintf, strcat.
    """
    if name.startswith("__builtin_"):
        name = name[len("__builtin_"):]
    name = name.strip("_")
    for suffix in ("_chk", "_inline"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def _called_name(call: Node) -> str:
    """The function name a CallExpr targets, if it is a plain named call.

    The callee is the first DeclRefExpr in the call, wrapped in an implicit
    function-to-pointer cast. Later DeclRefExprs are the arguments, so taking
    any match would report `data` instead of `malloc`.
    """
    for node in call.walk():
        if node is call:
            continue
        if node.kind == "DeclRefExpr" and node.name:
            return _normalize_call(node.name)
        if node.kind not in ("ImplicitCastExpr", "ParenExpr"):
            break           # the callee is not a plain name (function pointer, etc.)
    return ""


def function_metrics(function: Node) -> FunctionMetrics:
    body = astjson.body_of(function)
    if body is None:
        body = Node(kind="CompoundStmt")

    calls = [_called_name(c) for c in body.find("CallExpr")]
    calls = [c for c in calls if c]

    identifiers = [(c.name, c.line) for c in function.children
                   if c.kind == "ParmVarDecl" and c.name]
    identifiers += [(v.name, v.line) for v in body.find("VarDecl") if v.name]

    # Any name compared against NULL or 0 inside an if, so the unchecked
    # allocation rule can tell a guarded pointer from an unguarded one.
    null_checked: set[str] = set()
    for branch in body.find("IfStmt", "WhileStmt", "ConditionalOperator"):
        condition = branch.children[0] if branch.children else None
        if condition is None:
            continue
        for ref in condition.find("DeclRefExpr"):
            if ref.name:
                null_checked.add(ref.name)

    return FunctionMetrics(
        identifiers=identifiers,
        null_checked=null_checked,
        name=function.name,
        line=function.line,
        end_line=function.end_line,
        length=max(1, function.end_line - function.line + 1),
        parameters=len([c for c in function.children if c.kind == "ParmVarDecl"]),
        locals=len(body.find("VarDecl")),
        complexity=_complexity(body),
        max_nesting=_nesting_depth(body),
        returns=len(body.find("ReturnStmt")),
        loops=len([n for n in body.walk() if n.kind in LOOPS]),
        calls=calls,
        allocations=[c.line for c in body.find("CallExpr") if _called_name(c) in ALLOCATORS],
        frees=len([c for c in body.find("CallExpr") if _called_name(c) == "free"]),
    )


# Literals that mean something on their own and are never "magic".
BENIGN_LITERALS = frozenset({"0", "1", "2", "-1"})


def measure(source: Path) -> FileMetrics:
    text = source.read_text(errors="replace")
    total, code, comment, blank = _count_lines(text)

    top = astjson.parse(source)
    funcs = [n for n in top
             if n.kind == "FunctionDecl" and any(c.kind == "CompoundStmt" for c in n.children)]

    metrics = FileMetrics(
        path=source, total_lines=total, code_lines=code,
        comment_lines=comment, blank_lines=blank,
        functions=[function_metrics(f) for f in funcs],
    )

    # Identifiers worth judging: things the author named. Parameters and locals
    # included; a function called `f` is as unreadable as a variable called `z`.
    for node in top:
        if node.kind == "VarDecl" and node.name:
            metrics.globals.append((node.name, node.line))
            metrics.identifiers.append((node.name, node.line))
        if node.kind != "FunctionDecl":
            continue
        if node.name:
            metrics.identifiers.append((node.name, node.line))
        for inner in node.walk():
            if inner.kind in ("ParmVarDecl", "VarDecl") and inner.name:
                metrics.identifiers.append((inner.name, inner.line))
            if inner.kind == "IntegerLiteral" and inner.value not in BENIGN_LITERALS:
                metrics.magic_numbers.append((inner.value, inner.line))

    return metrics
