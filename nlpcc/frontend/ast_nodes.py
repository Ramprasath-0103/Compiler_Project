"""AST node definitions for the subset.

This is the single most load-bearing file in the project -- the semantic
analyzer, the quality metrics and the optimization recommender all walk these
nodes, so the shape is chosen once here and not negotiated per consumer.

Every node carries ``line``/``column`` so findings discovered three layers up
can still point at real source. Nodes mirror the grammar in docs/scope.md
one-for-one; ``Unsupported`` is the escape hatch for constructs the grammar
deliberately excludes, kept in the tree rather than dropped so that a later
pass can still see "something was here".

Two conventions the walkers rely on:

  * ``children()`` yields every child node, so a visitor never has to know a
    node's field names to traverse it.
  * a type is a ``TypeRef``, never a bare string, because the semantic
    analyzer compares pointer-ness and array-ness and not just the spelling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class Node:
    line: int = 0
    column: int = 0

    def children(self) -> Iterator["Node"]:
        """Every child node, in source order. Depth-first walkers use this."""
        for value in vars(self).values():
            if isinstance(value, Node):
                yield value
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, Node):
                        yield item

    def walk(self) -> Iterator["Node"]:
        """This node, then every descendant, depth first.

        Iterative on purpose: an expression tree from generated source can be
        thousands of nodes deep, and every pass in the project walks it.
        """
        stack: list[Node] = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(list(node.children())))


# -- types ----------------------------------------------------------------


@dataclass
class TypeRef(Node):
    """A type as written: a base name plus how it is wrapped.

    ``pointer_depth`` counts the ``*``s; ``array`` records that the declarator
    had ``[]``, with ``array_size`` holding the expression inside it when the
    program gave one. Keeping these separate from ``name`` is what lets the
    analyzer say "pointer assigned to a float" without re-parsing anything.
    """

    name: str = "int"
    pointer_depth: int = 0
    array: bool = False
    array_size: Node | None = None
    qualifiers: tuple[str, ...] = ()

    @property
    def is_pointer(self) -> bool:
        return self.pointer_depth > 0

    @property
    def is_void(self) -> bool:
        return self.name == "void" and not self.is_pointer and not self.array

    @property
    def is_scalar(self) -> bool:
        """An arithmetic type: neither pointer, nor array, nor void."""
        return not self.is_pointer and not self.array and not self.is_void

    def __str__(self) -> str:
        text = " ".join((*self.qualifiers, self.name)) + "*" * self.pointer_depth
        return text + "[]" if self.array else text


# -- top level ------------------------------------------------------------


@dataclass
class TranslationUnit(Node):
    declarations: list[Node] = field(default_factory=list)


@dataclass
class Include(Node):
    """``#include`` recorded, never expanded -- see docs/scope.md."""

    header: str = ""
    system: bool = True          # <stdio.h> rather than "mine.h"
    text: str = ""               # the directive verbatim


@dataclass
class Param(Node):
    type: TypeRef = field(default_factory=TypeRef)
    name: str = ""


@dataclass
class FunctionDef(Node):
    name: str = ""
    return_type: TypeRef = field(default_factory=TypeRef)
    params: list[Param] = field(default_factory=list)
    body: "Block | None" = None      # None for a prototype
    storage: tuple[str, ...] = ()
    end_line: int = 0                # the closing brace, where "no return" points


@dataclass
class VarDecl(Node):
    name: str = ""
    type: TypeRef = field(default_factory=TypeRef)
    init: Node | None = None
    storage: tuple[str, ...] = ()


@dataclass
class DeclGroup(Node):
    """``int a, b = 2;`` -- one declaration, several names.

    Not a block: these names belong to the scope the declaration is written
    in, so the semantic analyzer walks straight through this node without
    pushing one.
    """

    declarations: list[VarDecl] = field(default_factory=list)


# -- statements -----------------------------------------------------------


@dataclass
class Block(Node):
    statements: list[Node] = field(default_factory=list)
    end_line: int = 0


@dataclass
class If(Node):
    condition: Node | None = None
    then_branch: Node | None = None
    else_branch: Node | None = None


@dataclass
class While(Node):
    condition: Node | None = None
    body: Node | None = None


@dataclass
class For(Node):
    init: Node | None = None         # a VarDecl, an ExprStmt, or None
    condition: Node | None = None
    step: Node | None = None
    body: Node | None = None


@dataclass
class Return(Node):
    value: Node | None = None


@dataclass
class Break(Node):
    pass


@dataclass
class Continue(Node):
    pass


@dataclass
class ExprStmt(Node):
    expression: Node | None = None


@dataclass
class Unsupported(Node):
    """A construct the grammar excludes, kept in the tree as a marker.

    The parser emits one ``W_OUTSIDE_SUBSET`` note per occurrence and skips
    the text; nothing downstream looks inside, but the node records that the
    gap exists at this line.
    """

    construct: str = ""
    text: str = ""


# -- expressions ----------------------------------------------------------


@dataclass
class Assignment(Node):
    target: Node | None = None
    operator: str = "="              # '=' or a compound form such as '+='
    value: Node | None = None


@dataclass
class BinaryOp(Node):
    operator: str = ""
    left: Node | None = None
    right: Node | None = None


@dataclass
class UnaryOp(Node):
    operator: str = ""
    operand: Node | None = None
    postfix: bool = False            # true for i++ as opposed to ++i


@dataclass
class Conditional(Node):
    condition: Node | None = None
    then_value: Node | None = None
    else_value: Node | None = None


@dataclass
class Call(Node):
    callee: Node | None = None
    arguments: list[Node] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.callee.name if isinstance(self.callee, Identifier) else ""


@dataclass
class Index(Node):
    array: Node | None = None
    index: Node | None = None


@dataclass
class Cast(Node):
    type: TypeRef = field(default_factory=TypeRef)
    operand: Node | None = None


@dataclass
class SizeOf(Node):
    type: TypeRef | None = None      # sizeof(int)
    operand: Node | None = None      # sizeof x


@dataclass
class Identifier(Node):
    name: str = ""


@dataclass
class Literal(Node):
    """``kind`` is one of int, float, char, string -- the lexer's own word."""

    value: str = ""
    kind: str = "int"


@dataclass
class InitList(Node):
    """``{1, 2, 3}`` in an initializer."""

    values: list[Node] = field(default_factory=list)
