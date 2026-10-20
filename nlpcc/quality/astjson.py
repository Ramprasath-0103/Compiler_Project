"""Load clang's JSON AST into a small tree we can walk.

Why clang's AST and not our own: the quality metrics need a real syntax tree
for every C program, including the ones outside the subset our front end
handles. ``from_own_ast`` is the seam where nlpcc.frontend's tree feeds the
same metrics once it lands -- the metric code never learns which one it got.

THE TRAP: clang's JSON omits ``file``, ``line`` and ``col`` whenever they are
unchanged from the previous node *in document order*, and that order is
``range.begin`` -> ``loc`` -> children -> ``range.end``. Most statement nodes
carry an empty ``loc`` and get their line only from ``range.begin``, so
reading ``loc`` alone pins every statement in a function to the function's own
line. We replay the document order and carry the location forward.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..backends.clang_runner import find_compiler

# Statements that add a branch, for complexity and nesting.
BRANCHING = frozenset({
    "IfStmt", "WhileStmt", "ForStmt", "DoStmt", "SwitchStmt", "CaseStmt",
    "ConditionalOperator", "CXXForRangeStmt",
})
LOOPS = frozenset({"WhileStmt", "ForStmt", "DoStmt"})


@dataclass
class Node:
    """One AST node, with its location resolved."""

    kind: str
    line: int = 0
    column: int = 0
    end_line: int = 0
    name: str = ""
    type: str = ""
    opcode: str = ""          # BinaryOperator / UnaryOperator
    value: str = ""           # literals
    children: list["Node"] = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, *kinds: str):
        """Every descendant (and self) of the given kinds."""
        wanted = frozenset(kinds)
        return [n for n in self.walk() if n.kind in wanted]

    def __repr__(self) -> str:
        label = f" {self.name}" if self.name else ""
        return f"<{self.kind}{label} line {self.line}>"


class _Cursor:
    """Carries file and line forward across the walk, as clang expects."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.file = ""
        self.line = 0
        self.column = 0

    def resolve(self, loc: dict | None) -> tuple[str, int, int]:
        if isinstance(loc, dict):
            # expansionLoc wins: a node from a macro reports where it was used.
            inner = loc.get("expansionLoc") or loc
            if "file" in inner:
                self.file = inner["file"]
            if "line" in inner:
                self.line = inner["line"]
            if "col" in inner:
                self.column = inner["col"]
        return self.file, self.line, self.column


def _convert(raw: dict, cursor: _Cursor) -> Node:
    rng = raw.get("range") or {}
    # Document order, exactly as clang emits it. Each step may fill in a field
    # the next one omits, and the cursor must pass through all of them so the
    # following sibling starts from the right place.
    file, line, column = cursor.resolve(rng.get("begin"))
    file, line, column = cursor.resolve(raw.get("loc"))

    # A DeclRefExpr names what it points at, not itself.
    referenced = raw.get("referencedDecl") or {}
    name = raw.get("name") or referenced.get("name") or ""

    node = Node(
        kind=raw.get("kind", "Unknown"),
        line=line,
        column=column,
        name=name,
        type=(raw.get("type") or {}).get("qualType", ""),
        opcode=raw.get("opcode", "") or "",
        value=str(raw.get("value", "")) if "value" in raw else "",
    )
    for child in raw.get("inner", []) or []:
        node.children.append(_convert(child, cursor))
    # Resolved last, which both gives us the node's extent and leaves the
    # cursor where the next sibling expects it.
    _, end_line, _ = cursor.resolve(rng.get("end"))
    node.end_line = max(end_line, node.line)
    node._file = file                                       # type: ignore[attr-defined]
    return node


def parse(source: Path, timeout: float = 30.0) -> list[Node]:
    """Top-level declarations defined in ``source`` itself.

    Everything pulled in by #include is dropped -- a header's 2000 functions
    are not the user's code and would drown every metric.
    """
    cc = find_compiler()
    if cc is None:
        return []
    proc = subprocess.run(
        [cc, "-Xclang", "-ast-dump=json", "-fsyntax-only", str(source)],
        capture_output=True, text=True, timeout=timeout)
    if not proc.stdout.strip():
        return []                       # the file does not parse; errors are reported elsewhere
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []

    cursor = _Cursor(str(source))
    wanted = {str(source), source.name}
    top: list[Node] = []
    for child in raw.get("inner", []) or []:
        node = _convert(child, cursor)
        origin = getattr(node, "_file", "")
        if origin in wanted or Path(origin or "").name == source.name:
            top.append(node)
    return top


def functions(source: Path) -> list[Node]:
    """Just the functions with a body, which is what the metrics measure."""
    return [n for n in parse(source)
            if n.kind == "FunctionDecl" and any(c.kind == "CompoundStmt" for c in n.children)]


def body_of(function: Node) -> Node | None:
    for child in function.children:
        if child.kind == "CompoundStmt":
            return child
    return None


def from_own_ast(unit) -> list[Node]:
    """Adapter seam for nlpcc.frontend's AST (step 4).

    Once the front end produces a TranslationUnit, translate it into these
    Node objects here and the whole metrics layer works on it unchanged.
    """
    raise NotImplementedError("wire up once nlpcc.frontend.ast_nodes is implemented")
