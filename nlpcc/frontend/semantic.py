"""Phase 3 -- semantic analysis. AST + symbol table -> meaning checks.

What this phase can say that the parser cannot: this name was never declared,
this one was declared twice, the value does not fit the variable, the call
passes the wrong number of arguments, this function promises an int and does
not return one, this local is read before anything is written to it.

The symbol table is a stack of scopes -- one per block, pushed on entry and
popped on exit -- and every symbol records where it was declared. That record
is the point: a diagnostic that says "undeclared" is a compiler talking, while
one that also shows the line where the name *was* declared explains the
mistake. Declaration sites travel to the report through
``Diagnostic.meta["related"]``, the same channel clang_runner.py uses for
clang's own "declared here" notes, so the CLI renders both identically.

Two deliberate limits, both chosen so correct code stays silent:

  * **No headers.** ``#include`` is recorded and never expanded (docs/scope.md),
    so library names have no declaration to find. ``STANDARD_NAMES`` lists the
    ones we know; a file that includes a header outside ``KNOWN_HEADERS``, or
    uses ``#define``, has undeclared-identifier reporting switched off
    entirely. A false "undeclared" on working code would make the tool worse
    than nothing, and clang still reports the real ones.
  * **Uninitialized reads are judged conservatively.** A local is only flagged
    when nothing in the whole function ever assigns to it and its address is
    never taken. That misses cases clang catches (tests/test_frontend.py
    records the gap) and, more importantly, never invents one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..diagnostics import Diagnostic, Origin, Phase, Position, Severity
from .ast_nodes import (
    Assignment, BinaryOp, Block, Break, Call, Cast, Conditional, Continue, DeclGroup,
    ExprStmt, For, FunctionDef, Identifier, If, Include, Index, InitList, Literal,
    Node, Param, Return, SizeOf, TranslationUnit, TypeRef, UnaryOp, Unsupported,
    VarDecl, While,
)

# Headers whose names we know. Anything else means the file declares things we
# cannot see, and undeclared-identifier reporting switches off for that file.
KNOWN_HEADERS: frozenset[str] = frozenset({
    "stdio.h", "stdlib.h", "string.h", "math.h", "ctype.h", "limits.h", "float.h",
    "stdbool.h", "stddef.h", "stdint.h", "time.h", "assert.h", "errno.h",
})

# The library surface a first-year program actually touches. Declared with no
# type, on purpose: we know the name exists, we do not claim to know its
# signature, and an unknown type silences the checks that would guess.
STANDARD_NAMES: frozenset[str] = frozenset({
    # stdio
    "printf", "scanf", "sprintf", "snprintf", "fprintf", "fscanf", "puts", "putchar",
    "gets", "getchar", "fgets", "fputs", "fopen", "fclose", "fread", "fwrite",
    "fseek", "ftell", "feof", "fflush", "perror", "remove", "rename",
    "stdin", "stdout", "stderr", "EOF", "FILE", "SEEK_SET", "SEEK_CUR", "SEEK_END",
    # stdlib
    "malloc", "calloc", "realloc", "free", "exit", "abort", "atexit", "system",
    "atoi", "atof", "atol", "strtol", "strtod", "rand", "srand", "qsort", "bsearch",
    "abs", "labs", "div", "NULL", "EXIT_SUCCESS", "EXIT_FAILURE", "RAND_MAX",
    "size_t", "ptrdiff_t",
    # string
    "strcpy", "strncpy", "strcat", "strncat", "strcmp", "strncmp", "strlen",
    "strchr", "strrchr", "strstr", "strtok", "strdup", "memcpy", "memmove",
    "memset", "memcmp",
    # math
    "sqrt", "pow", "fabs", "floor", "ceil", "round", "fmod", "sin", "cos", "tan",
    "asin", "acos", "atan", "atan2", "log", "log10", "exp", "M_PI",
    # ctype
    "isalpha", "isdigit", "isalnum", "isspace", "isupper", "islower", "ispunct",
    "toupper", "tolower",
    # limits / float / stdbool / time / assert
    "INT_MAX", "INT_MIN", "UINT_MAX", "LONG_MAX", "LONG_MIN", "CHAR_BIT",
    "CHAR_MAX", "CHAR_MIN", "SHRT_MAX", "SHRT_MIN", "FLT_MAX", "FLT_MIN",
    "DBL_MAX", "DBL_MIN", "bool", "true", "false", "time", "clock", "time_t",
    "clock_t", "difftime", "assert", "errno",
})

_VARIADIC = "..."


@dataclass
class Symbol:
    """One declared name, and where it was declared."""

    name: str
    kind: str                       # "variable" | "parameter" | "function"
    type: TypeRef | None            # None when we know the name but not its type
    line: int = 0
    column: int = 0
    params: list[Param] | None = None
    variadic: bool = False
    initialized: bool = False       # had an initializer at its declaration
    standard: bool = False          # from STANDARD_NAMES, not from this file

    @property
    def described(self) -> str:
        return {"function": "function", "parameter": "parameter"}.get(self.kind, "variable")


class SemanticAnalyzer:
    def __init__(self, unit: TranslationUnit, filename: str = "<input>",
                 source_text: str | None = None) -> None:
        self.unit = unit
        self.filename = filename
        self.diagnostics: list[Diagnostic] = []
        self.scopes: list[dict[str, Symbol]] = [{}]
        self._lines = (source_text if source_text is not None
                       else _read(filename)).splitlines()
        self._function: FunctionDef | None = None
        self._loop_depth = 0
        self._assigned_in_function: set[str] = set()
        self._escaped_in_function: set[str] = set()
        self._reported_names: set[str] = set()      # one complaint per name per function
        self._report_undeclared = True

    # -- entry point -------------------------------------------------------
    def analyze(self) -> list[Diagnostic]:
        self._report_undeclared = self._all_declarations_are_visible()
        for name in STANDARD_NAMES:
            self.scopes[0][name] = Symbol(name=name, kind="function", type=None,
                                          standard=True)
        # Functions are collected before any body is walked: C programs call
        # downwards as often as upwards, and mutual recursion is not an error.
        for declaration in self.unit.declarations:
            if isinstance(declaration, FunctionDef):
                self._declare_function(declaration)
        for declaration in self.unit.declarations:
            self.visit(declaration)
        return self.diagnostics

    def _all_declarations_are_visible(self) -> bool:
        """Whether we have seen every declaration this file relies on.

        We have not if it includes a header we do not know, if it uses
        ``#define`` (a macro can introduce any name), or if the parser skipped
        a construct outside the subset -- a skipped ``struct`` declares names
        we never saw. In any of those cases undeclared-identifier reporting
        switches off for the whole file: clang reports the real ones, and we
        say nothing rather than something wrong.
        """
        for node in self.unit.walk():
            if isinstance(node, Unsupported):
                return False
            if not isinstance(node, Include):
                continue
            directive = node.text.lstrip("#").strip()
            if directive.startswith("define") or directive.startswith("import"):
                return False
            if directive.startswith("include") and (
                    not node.system or node.header not in KNOWN_HEADERS):
                return False
        return True

    # -- diagnostics -------------------------------------------------------
    def error(self, code: str, message: str, node: Node,
              severity: Severity = Severity.ERROR,
              declared: Symbol | None = None,
              phase: Phase = Phase.SEMANTIC) -> None:
        diag = Diagnostic(
            phase=phase,
            severity=severity,
            message=message,
            file=self.filename,
            position=Position(node.line, node.column),
            code=code,
            origin=Origin.FRONTEND,
            snippet=self._line_text(node.line),
        )
        if declared is not None and declared.line:
            diag.meta["related"] = [{
                "message": f"'{declared.name}' is declared here, as a {declared.described}",
                "line": declared.line,
                "column": declared.column,
                "snippet": self._line_text(declared.line),
            }]
        self.diagnostics.append(diag)

    def _line_text(self, line: int) -> str:
        return self._lines[line - 1] if 0 < line <= len(self._lines) else ""

    # -- scopes ------------------------------------------------------------
    def push_scope(self) -> None:
        self.scopes.append({})

    def pop_scope(self) -> None:
        self.scopes.pop()

    def lookup(self, name: str) -> Symbol | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def declare(self, symbol: Symbol, node: Node) -> Symbol:
        scope = self.scopes[-1]
        existing = scope.get(name := symbol.name)
        if existing is not None and not existing.standard:
            self.error("E_REDECLARATION",
                       f"'{name}' is declared twice in the same scope",
                       node, declared=existing)
            return existing
        scope[name] = symbol
        return symbol

    def _declare_function(self, function: FunctionDef) -> None:
        """Record one function in the global scope.

        A prototype followed by its definition is ordinary C, so only a second
        *body* for the same name is a redefinition.
        """
        variadic = any(p.name == _VARIADIC for p in function.params)
        params = [p for p in function.params if p.name != _VARIADIC]
        existing = self.scopes[0].get(function.name)
        if (existing is not None and not existing.standard and existing.initialized
                and function.body is not None):
            self.error("E_REDECLARATION",
                       f"the function '{function.name}' is defined twice",
                       function, declared=existing)
            return
        symbol = Symbol(name=function.name, kind="function",
                        type=function.return_type, line=function.line,
                        column=function.column, params=params, variadic=variadic,
                        initialized=function.body is not None)
        self.scopes[0][function.name] = symbol

    # -- dispatch ----------------------------------------------------------
    def visit(self, node: Node | None) -> None:
        if node is None:
            return
        handler = getattr(self, f"visit_{type(node).__name__.lower()}", None)
        if handler is not None:
            handler(node)
            return
        for child in node.children():
            self.visit(child)

    # -- declarations ------------------------------------------------------
    def visit_include(self, node: Include) -> None:
        return

    def visit_unsupported(self, node: Unsupported) -> None:
        return          # the parser already said so; nothing in here is modelled

    def visit_declgroup(self, node: DeclGroup) -> None:
        for declaration in node.declarations:
            self.visit(declaration)

    def visit_vardecl(self, node: VarDecl) -> None:
        if node.init is not None:
            self.visit(node.init)
            self._check_assignable(node.type, node.init, node, "initialize")
        if node.type.is_void:
            self.error("E_VOID_VARIABLE",
                       f"'{node.name}' is declared void, which holds no value", node)
        if node.name:
            self.declare(Symbol(name=node.name, kind="variable", type=node.type,
                                line=node.line, column=node.column,
                                initialized=node.init is not None), node)

    def visit_functiondef(self, node: FunctionDef) -> None:
        if node.body is None:
            return
        previous = (self._function, self._assigned_in_function,
                    self._escaped_in_function, self._reported_names, self._loop_depth)
        self._function = node
        self._assigned_in_function = _assigned_names(node.body)
        self._escaped_in_function = _escaped_names(node.body)
        self._reported_names = set()
        self._loop_depth = 0

        self.push_scope()
        for param in node.params:
            if param.name and param.name != _VARIADIC:
                self.declare(Symbol(name=param.name, kind="parameter",
                                    type=param.type, line=param.line,
                                    column=param.column, initialized=True), param)
        for statement in node.body.statements:
            self.visit(statement)
        self.pop_scope()

        self._check_returns(node)
        (self._function, self._assigned_in_function, self._escaped_in_function,
         self._reported_names, self._loop_depth) = previous

    def _check_returns(self, node: FunctionDef) -> None:
        """A non-void function must return on every path.

        ``main`` is exempt: C99 defines falling off the end of main as
        returning 0, and warning about it would be wrong rather than strict.
        """
        if node.return_type.is_void or node.name == "main" or node.body is None:
            return
        if _always_returns(node.body):
            return
        end = Node(line=node.body.end_line or node.line, column=1)
        self.error(
            "W_MISSING_RETURN",
            f"'{node.name}' returns {node.return_type} but can reach its closing "
            "brace without a 'return'",
            end, severity=Severity.WARNING,
            declared=Symbol(name=node.name, kind="function", type=node.return_type,
                            line=node.line, column=node.column),
        )

    # -- statements --------------------------------------------------------
    def visit_block(self, node: Block) -> None:
        self.push_scope()
        for statement in node.statements:
            self.visit(statement)
        self.pop_scope()

    def visit_if(self, node: If) -> None:
        self.visit(node.condition)
        self.visit(node.then_branch)
        self.visit(node.else_branch)

    def visit_while(self, node: While) -> None:
        self.visit(node.condition)
        self._loop_depth += 1
        self.visit(node.body)
        self._loop_depth -= 1

    def visit_for(self, node: For) -> None:
        # The init clause declares into a scope of its own: `for (int i = ...)`
        # must not leak i into the enclosing block.
        self.push_scope()
        self.visit(node.init)
        self.visit(node.condition)
        self.visit(node.step)
        self._loop_depth += 1
        self.visit(node.body)
        self._loop_depth -= 1
        self.pop_scope()

    def visit_return(self, node: Return) -> None:
        self.visit(node.value)
        if self._function is None:
            return
        expected = self._function.return_type
        if node.value is not None and expected.is_void:
            self.error("E_TYPE_MISMATCH",
                       f"'{self._function.name}' is declared void but returns a value",
                       node)
        elif node.value is not None:
            self._check_assignable(expected, node.value, node, "return")

    def visit_break(self, node: Break) -> None:
        if self._loop_depth == 0:
            self.error("E_BREAK_OUTSIDE_LOOP",
                       "'break' is outside any loop, so there is nothing to break out of",
                       node)

    def visit_continue(self, node: Continue) -> None:
        if self._loop_depth == 0:
            self.error("E_BREAK_OUTSIDE_LOOP",
                       "'continue' is outside any loop, so there is no loop to continue",
                       node)

    # -- expressions -------------------------------------------------------
    def visit_assignment(self, node: Assignment) -> None:
        self.visit(node.value)
        target = node.target
        if isinstance(target, Identifier):
            symbol = self._resolve(target)          # a write, so not an uninitialized read
            if symbol is not None:
                symbol.initialized = True
                if symbol.kind == "function":
                    self.error("E_TYPE_MISMATCH",
                               f"'{symbol.name}' is a function and cannot be assigned to",
                               target, declared=symbol)
                elif node.operator == "=":
                    self._check_assignable(symbol.type, node.value, node, "assign")
        else:
            self.visit(target)

    def visit_call(self, node: Call) -> None:
        for argument in node.arguments:
            self.visit(argument)
        callee = node.callee
        if not isinstance(callee, Identifier):
            self.visit(callee)
            return
        symbol = self._resolve(callee)
        if symbol is None or symbol.standard:
            return
        if symbol.kind != "function":
            self.error("E_NOT_A_FUNCTION",
                       f"'{symbol.name}' is a {symbol.described}, not a function, "
                       "so it cannot be called",
                       node, declared=symbol)
            return
        if symbol.params is None or symbol.variadic:
            return                                   # unknown signature: say nothing
            return
        given, wanted = len(node.arguments), len(symbol.params)
        if given != wanted:
            word = "few" if given < wanted else "many"
            self.error(
                "E_WRONG_ARG_COUNT",
                f"too {word} arguments to '{symbol.name}': it takes {wanted}, "
                f"but {given} {'was' if given == 1 else 'were'} passed",
                node, declared=symbol)

    def visit_unaryop(self, node: UnaryOp) -> None:
        if node.operator == "&" and isinstance(node.operand, Identifier):
            self._resolve(node.operand)              # taking an address is not a read
            return
        if node.operator in ("++", "--") and isinstance(node.operand, Identifier):
            symbol = self._resolve(node.operand)
            if symbol is not None:
                symbol.initialized = True
            return
        self.visit(node.operand)

    def visit_identifier(self, node: Identifier) -> None:
        symbol = self._resolve(node)
        if symbol is None:
            return
        self._check_initialized(node, symbol)

    # -- name resolution ---------------------------------------------------
    def _resolve(self, node: Identifier) -> Symbol | None:
        symbol = self.lookup(node.name)
        if symbol is not None:
            return symbol
        if self._report_undeclared and node.name not in self._reported_names:
            self._reported_names.add(node.name)
            self.error("E_UNDECLARED_IDENTIFIER",
                       f"'{node.name}' has no declaration visible at this point",
                       node)
        return None

    def _check_initialized(self, node: Identifier, symbol: Symbol) -> None:
        if symbol.kind != "variable" or symbol.initialized or symbol.standard:
            return
        if symbol.type is None or symbol.type.array:
            return
        if symbol.name in self._assigned_in_function or symbol.name in self._escaped_in_function:
            return
        if symbol.name in self._reported_names:
            return
        self._reported_names.add(symbol.name)
        self.error("W_UNINITIALIZED_READ",
                   f"'{symbol.name}' is read here but nothing has been written to it",
                   node, severity=Severity.WARNING, declared=symbol)

    # -- types -------------------------------------------------------------
    def _check_assignable(self, target: TypeRef | None, value: Node, at: Node,
                          verb: str) -> None:
        """Report only mismatches we are certain about -- see the module docstring."""
        if target is None:
            return
        value_type = self.type_of(value)
        if value_type is None:
            return
        if isinstance(value, InitList):
            return
        target_indirect = target.is_pointer or target.array
        value_indirect = value_type.is_pointer or value_type.array

        if value_type.is_void:
            self.error("E_TYPE_MISMATCH",
                       f"cannot {verb} '{target}' from an expression of type void, "
                       "which produces no value", at)
            return
        if value_indirect and not target_indirect and not target.is_void:
            what = "a string" if _is_string(value) else f"a value of type {value_type}"
            self.error("E_TYPE_MISMATCH",
                       f"cannot {verb} '{target}' from {what}: an address does not "
                       "convert to a number", at)
            return
        if target_indirect and not value_indirect:
            if isinstance(value, Literal) and value.value.strip("uUlL") in ("0", "0x0"):
                return                               # the null pointer constant
            self.error("E_TYPE_MISMATCH",
                       f"cannot {verb} '{target}', which holds an address, from a "
                       f"value of type {value_type}", at)

    def type_of(self, node: Node | None) -> TypeRef | None:
        """The type of an expression, or None when we cannot tell.

        None is the common case for anything touching the standard library,
        and it is deliberately contagious: a check that cannot see a type does
        not run, rather than guessing.
        """
        if node is None:
            return None
        if isinstance(node, Literal):
            if node.kind == "string":
                return TypeRef(name="char", pointer_depth=1)
            return TypeRef(name={"int": "int", "float": "double", "char": "char"}[node.kind])
        if isinstance(node, Identifier):
            symbol = self.lookup(node.name)
            return symbol.type if symbol and not symbol.standard else None
        if isinstance(node, Cast):
            return node.type
        if isinstance(node, SizeOf):
            return TypeRef(name="unsigned long")
        if isinstance(node, Assignment):
            return self.type_of(node.target)
        if isinstance(node, Conditional):
            return self.type_of(node.then_value) or self.type_of(node.else_value)
        if isinstance(node, Call):
            callee = node.callee
            if isinstance(callee, Identifier):
                symbol = self.lookup(callee.name)
                if symbol and symbol.kind == "function" and not symbol.standard:
                    return symbol.type
            return None
        if isinstance(node, Index):
            return _element_of(self.type_of(node.array))
        if isinstance(node, UnaryOp):
            if node.operator == "&":
                inner = self.type_of(node.operand)
                return (TypeRef(name=inner.name, pointer_depth=inner.pointer_depth + 1)
                        if inner else None)
            if node.operator == "*":
                return _element_of(self.type_of(node.operand))
            if node.operator == "!":
                return TypeRef(name="int")
            return self.type_of(node.operand)
        if isinstance(node, BinaryOp):
            if node.operator in ("==", "!=", "<", ">", "<=", ">=", "&&", "||"):
                return TypeRef(name="int")
            left, right = self.type_of(node.left), self.type_of(node.right)
            if left is None or right is None:
                return None
            for side in (left, right):
                if side.is_pointer or side.array:
                    return side
            return left if _rank(left) >= _rank(right) else right
        return None


# -- pure helpers, shared with the later quality passes -------------------


def _is_string(node: Node) -> bool:
    return isinstance(node, Literal) and node.kind == "string"


def _element_of(type_ref: TypeRef | None) -> TypeRef | None:
    """What ``p[0]`` or ``*p`` produces."""
    if type_ref is None:
        return None
    if type_ref.array:
        return TypeRef(name=type_ref.name, pointer_depth=type_ref.pointer_depth)
    if type_ref.is_pointer:
        return TypeRef(name=type_ref.name, pointer_depth=type_ref.pointer_depth - 1)
    return None


_RANKS = {"char": 1, "short": 2, "int": 3, "long": 4, "unsigned long": 4,
          "float": 5, "double": 6, "long double": 7}


def _rank(type_ref: TypeRef) -> int:
    return _RANKS.get(type_ref.name, 3)


def _assigned_names(body: Node) -> set[str]:
    """Every name the function writes to, anywhere in it.

    Flow-insensitive on purpose: a variable assigned in one branch and read in
    another is not reported, because deciding whether that path is reachable
    needs dataflow analysis this phase does not do.
    """
    names: set[str] = set()
    for node in body.walk():
        if isinstance(node, Assignment) and isinstance(node.target, Identifier):
            names.add(node.target.name)
        elif (isinstance(node, UnaryOp) and node.operator in ("++", "--")
              and isinstance(node.operand, Identifier)):
            names.add(node.operand.name)
    return names


def _escaped_names(body: Node) -> set[str]:
    """Names whose address is taken -- ``scanf("%d", &n)`` initializes n."""
    return {node.operand.name for node in body.walk()
            if isinstance(node, UnaryOp) and node.operator == "&"
            and isinstance(node.operand, Identifier)}


def _always_returns(node: Node | None) -> bool:
    """Whether control leaving this statement must have gone through a return."""
    if node is None:
        return False
    if isinstance(node, Return):
        return True
    if isinstance(node, Block):
        return any(_always_returns(statement) for statement in node.statements)
    if isinstance(node, If):
        return (node.else_branch is not None
                and _always_returns(node.then_branch)
                and _always_returns(node.else_branch))
    if isinstance(node, While):
        return _is_always_true(node.condition) and not _contains_break(node)
    if isinstance(node, For):
        return node.condition is None and not _contains_break(node)
    if isinstance(node, Unsupported):
        # We cannot see inside a construct outside the subset, so we do not
        # get to claim the function falls off its end.
        return True
    return False


def _is_always_true(condition: Node | None) -> bool:
    return (isinstance(condition, Literal) and condition.kind == "int"
            and condition.value.strip("uUlL") not in ("0", "0x0"))


def _contains_break(node: Node) -> bool:
    return any(isinstance(child, Break) for child in node.walk())


def _read(filename: str) -> str:
    try:
        return Path(filename).read_text(errors="replace")
    except OSError:
        return ""
