"""Show the compilation happening, phase by phase.

The project's claim is that it implements compiler phases rather than calling
one. This is the evidence: the real tokens the lexer produced, the real parse
tree the parser built, and the real symbol table the analyzer filled, taken
from the same objects the analysis runs on -- not a diagram drawn to look
like them.

Everything here is read-only and JSON-able, for the pipeline view in the web
interface.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic
from .frontend import FRONTEND_CODES
from .frontend import lexer as lexer_module
from .frontend import parser as parser_module
from .frontend import semantic as semantic_module
from .frontend.ast_nodes import Node, TypeRef

MAX_TOKENS = 400           # a page of tokens is a demonstration; 10,000 is a dump
MAX_TREE_NODES = 600
MAX_DEPTH = 14

PHASES = ("lexical", "syntax", "semantic")


@dataclass
class PhaseView:
    name: str
    title: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "summary": self.summary,
                "ok": self.ok, "detail": self.detail,
                "diagnostics": self.diagnostics}


def _describe_type(type_ref: TypeRef | None) -> str:
    if type_ref is None:
        return ""
    text = getattr(type_ref, "name", "") or ""
    text += "*" * int(getattr(type_ref, "pointer_depth", 0) or 0)
    if getattr(type_ref, "array", False):
        text += "[]"
    return text


def _node_label(node: Node) -> str:
    """A short, readable tag for a tree node: its name or operator, if it has one."""
    for attribute in ("name", "operator", "opcode", "value", "text"):
        value = getattr(node, attribute, None)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (int, float)):
            return str(value)
    type_ref = getattr(node, "type", None)
    if isinstance(type_ref, TypeRef):
        return _describe_type(type_ref)
    return ""


def _tree(node: Node, budget: list[int], depth: int = 0) -> dict[str, Any]:
    budget[0] -= 1
    entry: dict[str, Any] = {
        "kind": type(node).__name__,
        "label": _node_label(node),
        "line": getattr(node, "line", 0),
    }
    if depth >= MAX_DEPTH or budget[0] <= 0:
        entry["truncated"] = True
        return entry

    children = []
    for child in node.children():
        if budget[0] <= 0:
            entry["truncated"] = True
            break
        children.append(_tree(child, budget, depth + 1))
    if children:
        entry["children"] = children
    return entry


def _diag_dicts(diags: list[Diagnostic], phase: str) -> list[dict[str, Any]]:
    out = []
    for diag in diags:
        if FRONTEND_CODES.get(diag.code) != phase:
            continue
        out.append({
            "code": diag.code,
            "severity": diag.severity.value,
            "line": diag.position.line if diag.position else None,
            "message": diag.message,
            "title": diag.meta.get("title") or diag.message,
        })
    return out


def inspect_source(source: Path) -> dict[str, Any]:
    """Run the three phases and report what each one produced."""
    text = source.read_text(errors="replace")

    # -- phase 1: lexical ------------------------------------------------
    lex = lexer_module.Lexer(text, str(source))
    try:
        tokens = lex.tokenize()
    except Exception:                              # noqa: BLE001
        tokens = []
    lex_diags = list(lex.diagnostics)

    kinds = Counter(token.kind.name for token in tokens)
    lexical = PhaseView(
        name="lexical",
        title="Lexical analysis",
        summary=f"{len(tokens)} tokens in {len(kinds)} categories",
        ok=not any(d.severity.value in ("error", "fatal") for d in lex_diags),
        detail={
            "counts": [{"kind": kind, "count": count}
                       for kind, count in kinds.most_common()],
            "tokens": [{"kind": t.kind.name, "text": t.text,
                        "line": t.line, "column": t.column}
                       for t in tokens[:MAX_TOKENS]],
            "truncated": len(tokens) > MAX_TOKENS,
            "total": len(tokens),
        },
        diagnostics=_diag_dicts(lex_diags, "lexical"),
    )

    # -- phase 2: syntax -------------------------------------------------
    unit = None
    parse_diags: list[Diagnostic] = []
    try:
        unit_parser = parser_module.Parser(tokens, str(source))
        unit = unit_parser.parse()
        parse_diags = list(unit_parser.diagnostics)
    except Exception:                              # noqa: BLE001
        unit = None

    if unit is not None:
        budget = [MAX_TREE_NODES]
        tree = _tree(unit, budget)
        declarations = len(getattr(unit, "declarations", []) or [])
        nodes_shown = MAX_TREE_NODES - budget[0]
        syntax_summary = f"{declarations} top-level declaration(s), {nodes_shown} nodes"
    else:
        tree, syntax_summary = {}, "the parser could not build a tree"

    syntax = PhaseView(
        name="syntax",
        title="Syntax analysis",
        summary=syntax_summary,
        ok=unit is not None and not any(
            d.severity.value in ("error", "fatal") for d in parse_diags),
        detail={"tree": tree},
        diagnostics=_diag_dicts(parse_diags, "syntax"),
    )

    # -- phase 3: semantic -----------------------------------------------
    symbols: list[dict[str, Any]] = []
    sem_diags: list[Diagnostic] = []
    if unit is not None:
        analyzer = semantic_module.SemanticAnalyzer(unit, str(source), source_text=text)
        try:
            sem_diags = list(analyzer.analyze())
        except Exception:                          # noqa: BLE001
            sem_diags = []
        # Scope 0 after analysis holds this file's globals and functions; the
        # standard library names are flagged so they can be filtered out.
        for scope in analyzer.scopes:
            for symbol in scope.values():
                if getattr(symbol, "standard", False):
                    continue
                symbols.append({
                    "name": symbol.name,
                    "kind": symbol.kind,
                    "type": _describe_type(symbol.type),
                    "line": symbol.line,
                    "initialized": bool(symbol.initialized),
                    "parameters": len(symbol.params or []) if symbol.params else None,
                })
    symbols.sort(key=lambda s: (s["line"], s["name"]))

    semantic = PhaseView(
        name="semantic",
        title="Semantic analysis",
        summary=(f"{len(symbols)} name(s) declared in this file"
                 if symbols else "no declarations found"),
        ok=not any(d.severity.value in ("error", "fatal") for d in sem_diags),
        detail={"symbols": symbols},
        diagnostics=_diag_dicts(sem_diags, "semantic"),
    )

    return {
        "source": source.name,
        "phases": [lexical.to_dict(), syntax.to_dict(), semantic.to_dict()],
    }
