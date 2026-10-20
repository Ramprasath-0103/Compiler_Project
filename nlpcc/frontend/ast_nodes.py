"""AST node definitions for the subset.

STATUS: skeleton. This is the single most load-bearing file in the project --
the semantic analyzer, the quality metrics and the optimization recommender
all walk these nodes. Define it once, carefully, before writing the parser.

Every node carries ``line``/``column`` so findings discovered three layers up
can still point at real source.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    line: int = 0
    column: int = 0


@dataclass
class TranslationUnit(Node):
    declarations: list[Node] = field(default_factory=list)


# TODO(step 4): FunctionDef, VarDecl, Param, Block, If, While, For, Return,
# Break, Continue, ExprStmt, BinaryOp, UnaryOp, Call, Index, Identifier,
# Literal, Assignment, Cast.
