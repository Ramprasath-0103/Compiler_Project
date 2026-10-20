"""Phase 2 -- syntax analysis. Tokens -> AST, with recovery.

Recursive descent, one method per rule in the grammar in docs/scope.md, named
after the rule, so this file reads as that grammar does. Where a rule shares a
prefix with another -- a declaration and a function definition begin
identically -- the common prefix is parsed once and handed to the method as an
argument rather than backtracked over; the method names still match the rules.

Recovery matters more than speed here. Three rules keep one mistake from
becoming forty findings:

  * At most one syntax diagnostic per token. A missing ')' and the missing ';'
    it drags behind it are the same mistake seen twice, and the user only
    needs to be told once.
  * A missing ';' or ')' is reported and then *assumed present*: parsing
    continues as if it had been typed, which is almost always what the author
    meant and keeps the rest of the function analyzable.
  * Anything else triggers panic-mode recovery -- skip to the next ';' or '}'
    and resume there.

Constructs the grammar deliberately excludes (``struct``, ``switch``,
``do``/``while``, an unknown type name) are not errors. They produce one
``W_OUTSIDE_SUBSET`` note, an ``Unsupported`` node, and a skip; clang covers
those files, which is the whole point of running it alongside us.
"""

from __future__ import annotations

from pathlib import Path

from ..diagnostics import Diagnostic, Origin, Phase, Position, Severity
from .ast_nodes import (
    Assignment, BinaryOp, Block, Break, Call, Cast, Conditional, Continue, DeclGroup,
    ExprStmt, For, FunctionDef, Identifier, If, Include, Index, InitList, Literal, Node,
    Param, Return, SizeOf, TranslationUnit, TypeRef, UnaryOp, Unsupported, VarDecl,
    While,
)
from .tokens import (
    ASSIGNMENT_OPERATORS, STORAGE_KEYWORDS, TYPE_KEYWORDS, UNSUPPORTED_KEYWORDS,
    Token, TokenKind,
)

# Blocks or expressions nested this deep are pathological input, not C -- the
# deepest thing in the corpus is five levels. Stop descending rather than
# exhausting the Python stack, because a crash is not a report. The limit
# counts statement nesting and expression nesting together, and each level of
# the precedence ladder in this file costs one.
_MAX_DEPTH = 120

_LITERAL_KINDS = {
    TokenKind.INT_LITERAL: "int",
    TokenKind.FLOAT_LITERAL: "float",
    TokenKind.CHAR_LITERAL: "char",
    TokenKind.STRING_LITERAL: "string",
}


class Parser:
    def __init__(self, tokens: list[Token], filename: str = "<input>",
                 source_text: str | None = None) -> None:
        self.tokens = tokens
        self.filename = filename
        self.diagnostics: list[Diagnostic] = []
        self._index = 0
        # Comments are tokens so the readability metric can count them, but
        # they are not grammar; the parser walks the stream without them.
        self._stream = [t for t in tokens if t.kind is not TokenKind.COMMENT]
        self._reported: set[int] = set()     # token indices already complained about
        self._said_outside_subset = False     # the note is per file, not per occurrence
        self._depth = 0
        self._lines = (source_text if source_text is not None
                       else _read(filename)).splitlines()

    # -- token-level helpers ---------------------------------------------
    def peek(self, offset: int = 0) -> Token:
        index = min(self._index + offset, len(self._stream) - 1)
        return self._stream[index]

    def previous(self) -> Token:
        return self._stream[max(self._index - 1, 0)]

    def at_end(self) -> bool:
        return self.peek().kind is TokenKind.EOF

    def advance(self) -> Token:
        token = self.peek()
        if not self.at_end():
            self._index += 1
        return token

    def match_punct(self, *texts: str) -> Token | None:
        if self.peek().is_punct(*texts):
            return self.advance()
        return None

    def match_operator(self, *texts: str) -> Token | None:
        if self.peek().is_operator(*texts):
            return self.advance()
        return None

    def match_keyword(self, *names: str) -> Token | None:
        if self.peek().is_keyword(*names):
            return self.advance()
        return None

    # -- diagnostics ------------------------------------------------------
    def error(self, code: str, message: str, line: int, column: int,
              severity: Severity = Severity.ERROR, phase: Phase = Phase.SYNTAX,
              related: list[dict] | None = None) -> None:
        diag = Diagnostic(
            phase=phase,
            severity=severity,
            message=message,
            file=self.filename,
            position=Position(line, column),
            code=code,
            origin=Origin.FRONTEND,
            snippet=self._line_text(line),
        )
        if related:
            diag.meta["related"] = related
        self.diagnostics.append(diag)

    def _line_text(self, line: int) -> str:
        return self._lines[line - 1] if 0 < line <= len(self._lines) else ""

    def _line_note(self, token: Token, message: str) -> dict:
        return {"message": message, "line": token.line, "column": token.column,
                "snippet": self._line_text(token.line)}

    def syntax_error(self, code: str, message: str, *,
                     at_previous_end: bool = False,
                     related: list[dict] | None = None) -> bool:
        """Report once per token position. Returns whether it was reported.

        Everything the parser notices about one unexpected token is the same
        mistake; the second and third observation add noise and no
        information, so they are dropped.
        """
        if self._index in self._reported:
            return False
        self._reported.add(self._index)
        if at_previous_end:
            token = self.previous()
            line, column = token.line, token.end_column
        else:
            token = self.peek()
            line, column = token.line, token.column
        self.error(code, message, line, column, related=related)
        return True

    def outside_subset(self, construct: str, token: Token) -> None:
        """One note per file, at the first construct we do not model.

        Repeating it per occurrence would bury the report: a single struct
        program mentions the struct, the variable and every member access. One
        note says everything the user needs -- this file is clang's -- and the
        ``Unsupported`` nodes left in the tree record the rest for later passes.
        """
        if self._said_outside_subset:
            return
        self._said_outside_subset = True
        self.error(
            "W_OUTSIDE_SUBSET",
            f"{construct} is outside the C subset this analyzer models; "
            "the system compiler's findings for this file still apply",
            token.line, token.column,
            severity=Severity.NOTE,
        )

    # -- recovery ---------------------------------------------------------
    def skip_to_statement_boundary(self) -> None:
        """Panic mode: discard tokens up to the next ';' or '}'.

        The ';' is consumed (the broken statement is over); the '}' is left in
        place so the enclosing block can close itself normally.
        """
        while not self.at_end():
            if self.peek().is_punct(";"):
                self.advance()
                return
            if self.peek().is_punct("}"):
                return
            self.advance()

    # -- translation_unit := { external_declaration } EOF -----------------
    def parse(self) -> TranslationUnit:
        first = self.peek()
        unit = TranslationUnit(line=first.line, column=first.column)
        while not self.at_end():
            before = self._index
            declaration = self.external_declaration()
            if declaration is not None:
                unit.declarations.append(declaration)
            if self._index == before:          # no rule consumed anything
                self.advance()
        return unit

    # -- external_declaration ---------------------------------------------
    def external_declaration(self) -> Node | None:
        token = self.peek()
        if token.kind is TokenKind.PREPROCESSOR:
            return self.preprocessor_directive()
        if token.is_keyword(*UNSUPPORTED_KEYWORDS):
            return self.unsupported_construct(token)
        if self._at_unknown_type_name():
            return self.unsupported_construct(self.peek(), "a type name we do not know")
        if not self._starts_declaration():
            if self.syntax_error(
                    "E_UNEXPECTED_TOKEN",
                    f"expected a declaration at the top level of the file, "
                    f"found {_describe(token)}"):
                pass
            self.skip_to_statement_boundary()
            return None

        specifiers, base = self.declaration_specifiers()
        type_ref, name_token = self.declarator(base)
        if self.peek().is_punct("("):
            return self.function_definition(specifiers, type_ref, name_token)
        return self.declaration(specifiers, type_ref, name_token)

    # -- preprocessor_directive := PREPROCESSOR ---------------------------
    def preprocessor_directive(self) -> Include:
        token = self.advance()
        text = token.text
        header, system = "", True
        if text.lstrip("#").strip().startswith("include"):
            rest = text.split("include", 1)[1].strip()
            if rest.startswith("<") and ">" in rest:
                header, system = rest[1:rest.index(">")], True
            elif rest.startswith('"') and rest.count('"') >= 2:
                header, system = rest[1:rest.index('"', 1)], False
        return Include(line=token.line, column=token.column,
                       header=header, system=system, text=text)

    def unsupported_construct(self, token: Token,
                              described_as: str | None = None) -> Unsupported:
        """One note, then skip the construct. Not an error -- see the docstring."""
        self.outside_subset(described_as or f"'{token.text}'", token)
        start = self._index
        depth = 0
        while not self.at_end():
            current = self.peek()
            if current.is_punct("{", "("):
                depth += 1
                self.advance()
            elif current.is_punct("}", ")"):
                if depth == 0:
                    break                      # this one closes an enclosing block
                depth -= 1
                self.advance()
                if depth == 0 and current.text == "}":
                    self.match_punct(";")      # `struct point { ... };`
                    break
            elif current.is_punct(";") and depth == 0:
                self.advance()
                break
            else:
                self.advance()
        if self._index == start:
            self.advance()
        return Unsupported(line=token.line, column=token.column,
                           construct=token.text,
                           text=" ".join(t.text for t in self._stream[start:self._index]))

    # -- declaration_specifiers := { storage_class } type_specifier -------
    def declaration_specifiers(self) -> tuple[tuple[str, ...], TypeRef]:
        storage: list[str] = []
        while self.peek().kind is TokenKind.KEYWORD and self.peek().text in STORAGE_KEYWORDS:
            storage.append(self.advance().text)
        base = self.type_specifier()
        while self.peek().kind is TokenKind.KEYWORD and self.peek().text in STORAGE_KEYWORDS:
            storage.append(self.advance().text)      # `int const x`
        base.qualifiers = tuple(storage)
        return tuple(storage), base

    # -- type_specifier ----------------------------------------------------
    def type_specifier(self) -> TypeRef:
        token = self.peek()
        names: list[str] = []
        while self.peek().kind is TokenKind.KEYWORD and self.peek().text in TYPE_KEYWORDS:
            names.append(self.advance().text)
        if not names:
            # Only reachable when a caller mis-predicts; report and assume int
            # so the rest of the declaration still parses.
            self.syntax_error("E_UNEXPECTED_TOKEN",
                              f"expected a type name, found {_describe(token)}")
            names = ["int"]
        return TypeRef(line=token.line, column=token.column, name=" ".join(names))

    # -- declarator := { '*' } IDENTIFIER [ '[' [ expression ] ']' ] ------
    def declarator(self, base: TypeRef) -> tuple[TypeRef, Token]:
        pointer_depth = 0
        while self.match_operator("*"):
            pointer_depth += 1
        name_token = self.peek()
        if name_token.kind is TokenKind.IDENTIFIER:
            self.advance()
        else:
            self.syntax_error("E_UNEXPECTED_TOKEN",
                              f"expected the name being declared, found {_describe(name_token)}")
        type_ref = TypeRef(line=base.line, column=base.column, name=base.name,
                           pointer_depth=pointer_depth, qualifiers=base.qualifiers)
        if self.match_punct("["):
            type_ref.array = True
            if not self.peek().is_punct("]"):
                type_ref.array_size = self.expression()
            if not self.match_punct("]"):
                self.syntax_error("E_MISSING_BRACKET",
                                  "expected ']' to close the array size")
            if self.peek().is_punct("["):
                self.outside_subset("a multi-dimensional array", self.peek())
                while self.match_punct("["):
                    if not self.peek().is_punct("]"):
                        self.expression()
                    self.match_punct("]")
        return type_ref, name_token

    # -- function_definition ----------------------------------------------
    def function_definition(self, storage: tuple[str, ...], return_type: TypeRef,
                            name_token: Token) -> FunctionDef:
        function = FunctionDef(line=name_token.line, column=name_token.column,
                               name=name_token.text, return_type=return_type,
                               storage=storage)
        open_paren = self.advance()               # '('
        function.params = self.parameter_list()
        if not self.match_punct(")"):
            self.syntax_error(
                "E_MISSING_PAREN",
                "expected ')' to close the parameter list",
                related=[self._line_note(open_paren, "the '(' it should close is here")])
        if self.peek().is_punct("{"):
            function.body = self.compound_statement()
            function.end_line = function.body.end_line
        elif self.match_punct(";"):
            function.end_line = self.previous().line      # a prototype
        else:
            self.syntax_error("E_UNEXPECTED_TOKEN",
                              f"expected '{{' to open the body of {function.name}, "
                              f"found {_describe(self.peek())}")
            self.skip_to_statement_boundary()
        return function

    # -- parameter_list ----------------------------------------------------
    def parameter_list(self) -> list[Param]:
        params: list[Param] = []
        if self.peek().is_punct(")"):
            return params
        if self.peek().is_keyword("void") and self.peek(1).is_punct(")"):
            self.advance()
            return params
        while True:
            if self.peek().is_operator("..."):
                token = self.advance()
                self.outside_subset("a variadic parameter list ('...')", token)
                # Recorded as a parameter named '...' so the semantic analyzer
                # knows not to count arguments against this function.
                params.append(Param(line=token.line, column=token.column,
                                    name="...", type=TypeRef(name="...")))
            else:
                params.append(self.parameter_declaration())
            if not self.match_punct(","):
                break
        return params

    # -- parameter_declaration := declaration_specifiers declarator -------
    def parameter_declaration(self) -> Param:
        token = self.peek()
        if not self._starts_declaration():
            self.syntax_error("E_UNEXPECTED_TOKEN",
                              f"expected a parameter declaration, found {_describe(token)}")
            return Param(line=token.line, column=token.column)
        _, base = self.declaration_specifiers()
        if self.peek().kind is not TokenKind.IDENTIFIER and not self.peek().is_operator("*"):
            return Param(line=token.line, column=token.column, type=base)  # `int` unnamed
        type_ref, name_token = self.declarator(base)
        return Param(line=token.line, column=token.column, type=type_ref,
                     name=name_token.text if name_token.kind is TokenKind.IDENTIFIER else "")

    # -- declaration := specifiers init_declarator { ',' init_declarator } ';'
    def declaration(self, storage: tuple[str, ...], first_type: TypeRef,
                    first_name: Token) -> Node:
        declarations = [self.init_declarator(storage, first_type, first_name)]
        while self.match_punct(","):
            type_ref, name_token = self.declarator(
                TypeRef(line=first_type.line, column=first_type.column,
                        name=first_type.name, qualifiers=first_type.qualifiers))
            declarations.append(self.init_declarator(storage, type_ref, name_token))
        if not self.match_punct(";"):
            self.syntax_error(
                "E_MISSING_SEMICOLON",
                f"expected ';' after the declaration of '{declarations[0].name}'",
                at_previous_end=True)
        if len(declarations) == 1:
            return declarations[0]
        return DeclGroup(line=declarations[0].line, column=declarations[0].column,
                         declarations=declarations)

    # -- init_declarator := declarator [ '=' initializer ] ----------------
    def init_declarator(self, storage: tuple[str, ...], type_ref: TypeRef,
                        name_token: Token) -> VarDecl:
        decl = VarDecl(line=name_token.line, column=name_token.column,
                       name=name_token.text if name_token.kind is TokenKind.IDENTIFIER else "",
                       type=type_ref, storage=storage)
        if self.match_operator("="):
            decl.init = self.initializer()
        return decl

    # -- initializer := assignment_expression | '{' ... '}' ---------------
    def initializer(self) -> Node:
        brace = self.match_punct("{")
        if brace is None:
            return self.assignment_expression()
        values: list[Node] = []
        if not self.peek().is_punct("}"):
            while True:
                values.append(self.initializer())
                if not self.match_punct(","):
                    break
                if self.peek().is_punct("}"):      # trailing comma
                    break
        if not self.match_punct("}"):
            self.syntax_error(
                "E_MISSING_BRACE", "expected '}' to close this initializer list",
                related=[self._line_note(brace, "the '{' it should close is here")])
        return InitList(line=brace.line, column=brace.column, values=values)

    # -- statement ---------------------------------------------------------
    def statement(self) -> Node | None:
        if self._depth > _MAX_DEPTH:
            return self._too_deep()
        self._depth += 1
        try:
            return self._statement()
        finally:
            self._depth -= 1

    def _statement(self) -> Node | None:
        token = self.peek()
        if token.kind is TokenKind.PREPROCESSOR:
            return self.preprocessor_directive()
        if token.is_punct("{"):
            return self.compound_statement()
        if token.is_keyword("if"):
            return self.if_statement()
        if token.is_keyword("while"):
            return self.while_statement()
        if token.is_keyword("for"):
            return self.for_statement()
        if token.is_keyword("return"):
            return self.return_statement()
        if token.is_keyword("break"):
            return self.break_statement()
        if token.is_keyword("continue"):
            return self.continue_statement()
        if token.is_keyword(*UNSUPPORTED_KEYWORDS):
            return self.unsupported_construct(token)
        if self._at_unknown_type_name():
            return self.unsupported_construct(token, "a type name we do not know")
        if self._starts_declaration():
            specifiers, base = self.declaration_specifiers()
            type_ref, name_token = self.declarator(base)
            return self.declaration(specifiers, type_ref, name_token)
        return self.expression_statement()

    # -- compound_statement := '{' { statement } '}' ----------------------
    def compound_statement(self) -> Block:
        brace = self.advance()                    # '{'
        block = Block(line=brace.line, column=brace.column)
        while not self.at_end() and not self.peek().is_punct("}"):
            before = self._index
            statement = self.statement()
            if statement is not None:
                block.statements.append(statement)
            if self._index == before:
                self.advance()
        closing = self.match_punct("}")
        if closing is None:
            self.syntax_error(
                "E_MISSING_BRACE",
                "this block is never closed: the file ends with a '{' still open",
                related=[self._line_note(brace, "the '{' that is still open is here")])
            block.end_line = self.peek().line
        else:
            block.end_line = closing.line
        return block

    # -- if_statement := 'if' '(' expression ')' statement [ 'else' statement ]
    def if_statement(self) -> If:
        keyword = self.advance()
        node = If(line=keyword.line, column=keyword.column)
        node.condition = self._parenthesized_condition("if")
        node.then_branch = self.statement()
        if self.match_keyword("else"):
            node.else_branch = self.statement()
        return node

    # -- while_statement := 'while' '(' expression ')' statement ----------
    def while_statement(self) -> While:
        keyword = self.advance()
        node = While(line=keyword.line, column=keyword.column)
        node.condition = self._parenthesized_condition("while")
        node.body = self.statement()
        return node

    # -- for_statement -----------------------------------------------------
    def for_statement(self) -> For:
        keyword = self.advance()
        node = For(line=keyword.line, column=keyword.column)
        open_paren = self.match_punct("(")
        if open_paren is None:
            self.syntax_error("E_MISSING_PAREN", "expected '(' after 'for'")
        if self.peek().is_punct(";"):
            self.advance()
        elif self._starts_declaration():
            specifiers, base = self.declaration_specifiers()
            type_ref, name_token = self.declarator(base)
            node.init = self.declaration(specifiers, type_ref, name_token)
        else:
            node.init = self.expression_statement()
        if not self.peek().is_punct(";"):
            node.condition = self.expression()
        if not self.match_punct(";"):
            self.syntax_error("E_MISSING_SEMICOLON",
                              "expected ';' after the loop condition",
                              at_previous_end=True)
        if not self.peek().is_punct(")"):
            node.step = self.expression()
        if not self.match_punct(")"):
            self.syntax_error(
                "E_MISSING_PAREN", "expected ')' to close the 'for' clauses",
                related=[self._line_note(open_paren, "the '(' it should close is here")]
                if open_paren else None)
        node.body = self.statement()
        return node

    # -- return_statement := 'return' [ expression ] ';' ------------------
    def return_statement(self) -> Return:
        keyword = self.advance()
        node = Return(line=keyword.line, column=keyword.column)
        if not self.peek().is_punct(";"):
            node.value = self.expression()
        self._expect_semicolon("return")
        return node

    # -- break_statement / continue_statement -----------------------------
    def break_statement(self) -> Break:
        keyword = self.advance()
        self._expect_semicolon("break")
        return Break(line=keyword.line, column=keyword.column)

    def continue_statement(self) -> Continue:
        keyword = self.advance()
        self._expect_semicolon("continue")
        return Continue(line=keyword.line, column=keyword.column)

    # -- expression_statement := [ expression ] ';' -----------------------
    def expression_statement(self) -> Node:
        token = self.peek()
        if self.match_punct(";"):
            return ExprStmt(line=token.line, column=token.column)
        if not self._can_start_expression(token):
            self.syntax_error("E_UNEXPECTED_TOKEN",
                              f"expected a statement, found {_describe(token)}")
            self.skip_to_statement_boundary()
            return ExprStmt(line=token.line, column=token.column)
        expression = self.expression()
        self._expect_semicolon("statement")
        return ExprStmt(line=token.line, column=token.column, expression=expression)

    def _expect_semicolon(self, what: str) -> None:
        if self.match_punct(";"):
            return
        self.syntax_error("E_MISSING_SEMICOLON",
                          f"expected ';' at the end of this {what}",
                          at_previous_end=True)

    def _parenthesized_condition(self, keyword: str) -> Node | None:
        open_paren = self.match_punct("(")
        if open_paren is None:
            self.syntax_error("E_MISSING_PAREN", f"expected '(' after '{keyword}'")
        condition = self.expression() if not self.peek().is_punct(")") else None
        if not self.match_punct(")"):
            self.syntax_error(
                "E_MISSING_PAREN",
                f"expected ')' to close the '{keyword}' condition",
                related=[self._line_note(open_paren, "the '(' it should close is here")]
                if open_paren else None)
        return condition

    # -- expression := assignment_expression -------------------------------
    def expression(self) -> Node:
        return self.assignment_expression()

    # -- assignment_expression ---------------------------------------------
    def assignment_expression(self) -> Node:
        left = self.conditional_expression()
        if (self.peek().kind is TokenKind.OPERATOR
                and self.peek().text in ASSIGNMENT_OPERATORS):
            operator = self.advance()
            value = self.assignment_expression()
            return Assignment(line=operator.line, column=operator.column,
                              target=left, operator=operator.text, value=value)
        return left

    # -- conditional_expression --------------------------------------------
    def conditional_expression(self) -> Node:
        condition = self.logical_or_expression()
        question = self.match_operator("?")
        if question is None:
            return condition
        then_value = self.expression()
        if not self.match_punct(":"):
            self.syntax_error("E_MISSING_COLON",
                              "expected ':' in this '? :' expression")
        return Conditional(line=question.line, column=question.column,
                           condition=condition, then_value=then_value,
                           else_value=self.conditional_expression())

    # -- the binary operator ladder, one method per precedence level -------
    def logical_or_expression(self) -> Node:
        return self._binary_level(self.logical_and_expression, "||")

    def logical_and_expression(self) -> Node:
        return self._binary_level(self.bitwise_or_expression, "&&")

    def bitwise_or_expression(self) -> Node:
        return self._binary_level(self.bitwise_xor_expression, "|")

    def bitwise_xor_expression(self) -> Node:
        return self._binary_level(self.bitwise_and_expression, "^")

    def bitwise_and_expression(self) -> Node:
        return self._binary_level(self.equality_expression, "&")

    def equality_expression(self) -> Node:
        return self._binary_level(self.relational_expression, "==", "!=")

    def relational_expression(self) -> Node:
        return self._binary_level(self.shift_expression, "<", ">", "<=", ">=")

    def shift_expression(self) -> Node:
        return self._binary_level(self.additive_expression, "<<", ">>")

    def additive_expression(self) -> Node:
        return self._binary_level(self.multiplicative_expression, "+", "-")

    def multiplicative_expression(self) -> Node:
        return self._binary_level(self.cast_expression, "*", "/", "%")

    def _binary_level(self, operand, *operators: str) -> Node:
        if self._depth > _MAX_DEPTH:
            return self._too_deep()
        self._depth += 1
        try:
            left = operand()
            terms = 0
            while self.peek().is_operator(*operators):
                terms += 1
                if terms > _MAX_DEPTH:
                    # The tree this builds is left-deep, so an unbounded chain
                    # of operands is an unbounded tree for every later pass to
                    # walk. Cap it here, where the note can be reported.
                    return self._too_deep()
                token = self.advance()
                left = BinaryOp(line=token.line, column=token.column,
                                operator=token.text, left=left, right=operand())
            return left
        finally:
            self._depth -= 1

    def _too_deep(self) -> Node:
        token = self.peek()
        self.outside_subset("an expression nested beyond this analyzer's limit", token)
        self.skip_to_statement_boundary()
        return Literal(line=token.line, column=token.column, value="0", kind="int")

    # -- cast_expression := '(' type ')' cast_expression | unary_expression
    def cast_expression(self) -> Node:
        # Every unary chain passes through here, so this is where the depth
        # guard belongs: `- - - - ...` 5000 deep would otherwise exhaust the
        # Python stack, and a crash is not a report.
        if self._depth > _MAX_DEPTH:
            return self._too_deep()
        self._depth += 1
        try:
            return self._cast_expression()
        finally:
            self._depth -= 1

    def _cast_expression(self) -> Node:
        if self.peek().is_punct("(") and self._lookahead_is_type_name(1):
            open_paren = self.advance()
            base = self.type_specifier()
            depth = 0
            while self.match_operator("*"):
                depth += 1
            base.pointer_depth = depth
            if not self.match_punct(")"):
                self.syntax_error(
                    "E_MISSING_PAREN", "expected ')' to close this cast",
                    related=[self._line_note(open_paren, "the '(' it should close is here")])
            return Cast(line=open_paren.line, column=open_paren.column,
                        type=base, operand=self.cast_expression())
        return self.unary_expression()

    # -- unary_expression ---------------------------------------------------
    def unary_expression(self) -> Node:
        token = self.peek()
        if token.is_operator("+", "-", "!", "~", "*", "&", "++", "--"):
            self.advance()
            return UnaryOp(line=token.line, column=token.column,
                           operator=token.text, operand=self.cast_expression())
        if token.is_keyword("sizeof"):
            self.advance()
            if self.peek().is_punct("(") and self._lookahead_is_type_name(1):
                self.advance()
                base = self.type_specifier()
                depth = 0
                while self.match_operator("*"):
                    depth += 1
                base.pointer_depth = depth
                if not self.match_punct(")"):
                    self.syntax_error("E_MISSING_PAREN",
                                      "expected ')' to close 'sizeof'")
                return SizeOf(line=token.line, column=token.column, type=base)
            return SizeOf(line=token.line, column=token.column,
                          operand=self.unary_expression())
        return self.postfix_expression()

    # -- postfix_expression -------------------------------------------------
    def postfix_expression(self) -> Node:
        node = self.primary_expression()
        while True:
            if self.peek().is_punct("("):
                open_paren = self.advance()
                arguments = self.argument_list()
                if not self.match_punct(")"):
                    self.syntax_error(
                        "E_MISSING_PAREN",
                        "expected ')' to close this function call's arguments",
                        related=[self._line_note(open_paren,
                                                 "the '(' it should close is here")])
                node = Call(line=node.line, column=node.column,
                            callee=node, arguments=arguments)
            elif self.peek().is_punct("["):
                bracket = self.advance()
                index = self.expression() if not self.peek().is_punct("]") else None
                if not self.match_punct("]"):
                    self.syntax_error("E_MISSING_BRACKET",
                                      "expected ']' to close this array index")
                node = Index(line=bracket.line, column=bracket.column,
                             array=node, index=index)
            elif self.peek().is_operator("++", "--"):
                token = self.advance()
                node = UnaryOp(line=token.line, column=token.column,
                               operator=token.text, operand=node, postfix=True)
            elif self.peek().is_operator("->") or self.peek().is_punct("."):
                token = self.advance()
                self.outside_subset("access to a struct member", token)
                member = (self.advance().text
                          if self.peek().kind is TokenKind.IDENTIFIER else "")
                # Replacing the whole expression tells the semantic analyzer
                # that it does not know what this value is -- which is true.
                node = Unsupported(line=node.line, column=node.column,
                                   construct=token.text, text=f"{token.text}{member}")
            else:
                return node

    # -- argument_list ------------------------------------------------------
    def argument_list(self) -> list[Node]:
        arguments: list[Node] = []
        if self.peek().is_punct(")"):
            return arguments
        while True:
            if not self._can_start_expression(self.peek()):
                self.syntax_error("E_UNEXPECTED_TOKEN",
                                  f"expected an argument, found {_describe(self.peek())}")
                break
            arguments.append(self.assignment_expression())
            if not self.match_punct(","):
                break
        return arguments

    # -- primary_expression -------------------------------------------------
    def primary_expression(self) -> Node:
        token = self.peek()
        if token.kind is TokenKind.IDENTIFIER:
            self.advance()
            return Identifier(line=token.line, column=token.column, name=token.text)
        if token.kind in _LITERAL_KINDS:
            self.advance()
            text = token.text
            if token.kind is TokenKind.STRING_LITERAL:
                while self.peek().kind is TokenKind.STRING_LITERAL:
                    text += self.advance().text   # adjacent literals concatenate
            return Literal(line=token.line, column=token.column, value=text,
                           kind=_LITERAL_KINDS[token.kind])
        if token.is_punct("("):
            open_paren = self.advance()
            inner = self.expression()
            if not self.match_punct(")"):
                self.syntax_error(
                    "E_MISSING_PAREN", "expected ')' to close this parenthesis",
                    related=[self._line_note(open_paren,
                                             "the '(' it should close is here")])
            return inner
        self.syntax_error("E_EXPECTED_EXPRESSION",
                          f"expected a value here, found {_describe(token)}")
        return Literal(line=token.line, column=token.column, value="0", kind="int")

    # -- prediction helpers -------------------------------------------------
    def _starts_declaration(self) -> bool:
        token = self.peek()
        if token.kind is not TokenKind.KEYWORD:
            return False
        if token.text in TYPE_KEYWORDS:
            return True
        if token.text in STORAGE_KEYWORDS:
            return self._lookahead_is_type_name(1, skip_storage=True)
        return False

    def _lookahead_is_type_name(self, offset: int, skip_storage: bool = False) -> bool:
        index = offset
        while skip_storage and self.peek(index).text in STORAGE_KEYWORDS:
            index += 1
        token = self.peek(index)
        return token.kind is TokenKind.KEYWORD and token.text in TYPE_KEYWORDS

    def _at_unknown_type_name(self) -> bool:
        """``size_t n;`` or ``FILE *f;`` -- a declaration we cannot type.

        Two identifiers in a row, or an identifier followed by ``*`` and an
        identifier, is a declaration using a name the subset has no types for
        (typedefs are excluded). Parsing it as an expression would produce a
        bogus "missing ';'", so it is refused as outside the subset instead.
        """
        if self.peek().kind is not TokenKind.IDENTIFIER:
            return False
        offset = 1
        while self.peek(offset).is_operator("*"):
            offset += 1
        if self.peek(offset).kind is not TokenKind.IDENTIFIER:
            return False
        after = self.peek(offset + 1)
        return (after.is_punct(";", ",", "[", ")")
                or after.is_operator("=")
                or (offset > 1 and after.is_punct("(")))

    @staticmethod
    def _can_start_expression(token: Token) -> bool:
        if token.kind in (TokenKind.IDENTIFIER, TokenKind.EOF):
            return token.kind is TokenKind.IDENTIFIER
        if token.kind in _LITERAL_KINDS:
            return True
        if token.is_keyword("sizeof"):
            return True
        if token.is_punct("("):
            return True
        return token.is_operator("+", "-", "!", "~", "*", "&", "++", "--")


def _describe(token: Token) -> str:
    """How a token is named to a beginner: the text, or 'the end of the file'."""
    if token.kind is TokenKind.EOF:
        return "the end of the file"
    if token.kind is TokenKind.STRING_LITERAL:
        return "a string"
    return f"'{token.text}'"


def _read(filename: str) -> str:
    try:
        return Path(filename).read_text(errors="replace")
    except OSError:
        return ""
