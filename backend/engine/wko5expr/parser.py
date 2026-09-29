"""
Parser for the WKO5 expression language (as used in .wko5chart series).

Grammar (lowest → highest precedence), derived from the user's charts and the
WKO5 Expression Reference:

    program    := stmt (',' stmt)*            result = last statement
    stmt       := '@var' ':=' stmt | or
    or         := and (('or' | '||') and)*
    and        := cmp (('and' | '&&') cmp)*
    cmp        := add (('=' | '==' | '<>' | '!=' | '<' | '>' | '<=' | '>=') add)*
    add        := mul (('+' | '-') mul)*
    mul        := unary (('*' | '/') unary)*
    unary      := ('-' | '+' | '!' | 'not') unary | pow
    pow        := postfix ('^' unary)?
    postfix    := primary ('.' ident '(' args ')')*     e.g. sport(sport).athleterange(...)
    primary    := number | string | ident | ident '(' args ')' | '@var'
                | '(' [stmt] [',' stmt] ')' [string]  group / (x, y) pair / (,y) line;
                                                      trailing string is a type hint
                | '{' item (',' item)* '}'            list; item may be `a:b` (range)

Identifiers are case-insensitive. `if` followed by '(' is the function, bare
`if` / `IF` is the intensity-factor variable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------

@dataclass
class Node:
    pass


@dataclass
class Num(Node):
    value: float


@dataclass
class Str(Node):
    value: str


@dataclass
class Ident(Node):
    name: str  # lower-cased


@dataclass
class Var(Node):
    name: str  # "@name", lower-cased


@dataclass
class Assign(Node):
    name: str
    value: Node


@dataclass
class Seq(Node):
    items: list[Node]


@dataclass
class Call(Node):
    name: str
    args: list[Node]
    receiver: Optional[Node] = None  # for `a.f(...)`


@dataclass
class BinOp(Node):
    op: str
    left: Node
    right: Node


@dataclass
class Unary(Node):
    op: str
    operand: Node


@dataclass
class Pair(Node):
    x: Optional[Node]
    y: Optional[Node]


@dataclass
class ListLit(Node):
    items: list[Node]


@dataclass
class RangeLit(Node):
    lo: Node
    hi: Node
    step: Optional[Node] = None


@dataclass
class Unit(Node):
    """`expr"unit"` — a display/unit hint such as "W", "s", "min/km", "hms"."""
    value: Node
    unit: str


@dataclass
class Empty(Node):
    """Blank series expression — nothing to plot."""


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
  | (?P<str>"[^"]*")
  | (?P<var>@[A-Za-z_][A-Za-z0-9_]*)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>:=|<=|>=|<>|!=|==|&&|\|\||[-+*/^=<>!(),{}.:])
""", re.VERBOSE)


@dataclass
class Tok:
    kind: str
    text: str
    pos: int


class ParseError(ValueError):
    pass


def tokenize(src: str) -> list[Tok]:
    toks, pos = [], 0
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m:
            raise ParseError(f"unexpected character {src[pos]!r} at {pos}")
        kind = m.lastgroup
        if kind != "ws":
            toks.append(Tok(kind, m.group(), pos))
        pos = m.end()
    toks.append(Tok("eof", "", pos))
    return toks


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class Parser:
    def __init__(self, src: str):
        self.src = src
        self.toks = tokenize(src)
        self.i = 0
        self.brace_depth = 0

    # helpers
    def peek(self, k: int = 0) -> Tok:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def at(self, *texts: str) -> bool:
        t = self.peek()
        return t.kind in ("op", "ident") and t.text.lower() in texts

    def take(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, text: str) -> Tok:
        t = self.take()
        if t.text != text:
            raise ParseError(f"expected {text!r} at {t.pos}, got {t.text!r} in {self.src!r}")
        return t

    # grammar
    def program(self) -> Node:
        if self.peek().kind == "eof":
            return Empty()
        items = [self.stmt()]
        while self.at(","):
            self.take()
            if self.peek().kind == "eof":
                break
            items.append(self.stmt())
        if self.peek().kind != "eof":
            t = self.peek()
            raise ParseError(f"unexpected {t.text!r} at {t.pos} in {self.src!r}")
        return items[0] if len(items) == 1 else Seq(items)

    def stmt(self) -> Node:
        if self.peek().kind == "var" and self.peek(1).text == ":=":
            name = self.take().text.lower()
            self.take()
            return Assign(name, self.stmt())
        return self.or_()

    def or_(self) -> Node:
        n = self.and_()
        while self.at("or", "||"):
            self.take()
            n = BinOp("or", n, self.and_())
        return n

    def and_(self) -> Node:
        n = self.cmp()
        while self.at("and", "&&"):
            self.take()
            n = BinOp("and", n, self.cmp())
        return n

    def cmp(self) -> Node:
        n = self.add()
        while self.at("=", "==", "<>", "!=", "<", ">", "<=", ">="):
            op = self.take().text
            op = {"==": "=", "!=": "<>"}.get(op, op)
            n = BinOp(op, n, self.add())
        return n

    def add(self) -> Node:
        n = self.mul()
        while self.at("+", "-"):
            op = self.take().text
            n = BinOp(op, n, self.mul())
        return n

    def mul(self) -> Node:
        n = self.unary()
        while self.at("*", "/"):
            op = self.take().text
            n = BinOp(op, n, self.unary())
        return n

    def unary(self) -> Node:
        if self.at("-", "+"):
            op = self.take().text
            return Unary(op, self.unary())
        if self.at("!", "not") and not (self.peek().text.lower() == "not" and self.peek(1).text == "("):
            self.take()
            return Unary("!", self.unary())
        return self.pow_()

    def pow_(self) -> Node:
        n = self.postfix()
        if self.at("^"):
            self.take()
            return BinOp("^", n, self.unary())
        return n

    def postfix(self) -> Node:
        n = self.primary()
        if self.peek().kind == "str":  # unit / type hint suffix: 300"s", (expr)"W"
            n = Unit(n, self.take().text[1:-1])
        while self.at(".") and self.peek(1).kind == "ident" and self.peek(2).text == "(":
            self.take()
            name = self.take().text.lower()
            self.expect("(")
            args = self.args()
            n = Call(name, args, receiver=n)
        return n

    def args(self) -> list[Node]:
        args: list[Node] = []
        if self.at(")"):
            self.take()
            return args
        while True:
            args.append(self.stmt())
            if self.at(","):
                self.take()
                continue
            self.expect(")")
            return args

    def primary(self) -> Node:
        t = self.peek()
        if t.kind == "num":
            # time literal h:m[:s] followed by a unit string, e.g. 10:00:00"hms"
            j, parts = 0, [t.text]
            while self.peek(j + 1).text == ":" and self.peek(j + 2).kind == "num":
                parts.append(self.peek(j + 2).text)
                j += 2
            is_time = len(parts) > 1 and (
                self.peek(j + 1).kind == "str"
                or (len(parts) == 3 and self.brace_depth == 0))  # 0:1:45 outside {}
            if is_time:
                self.i += j + 1
                secs = 0.0
                for p in parts:
                    secs = secs * 60 + float(p)
                return Num(secs)
            self.take()
            return Num(float(t.text))
        if t.kind == "str":
            self.take()
            return Str(t.text[1:-1])
        if t.kind == "var":
            self.take()
            return Var(t.text.lower())
        if t.kind == "ident":
            self.take()
            name = t.text.lower()
            if self.at("("):
                self.take()
                return Call(name, self.args())
            return Ident(name)
        if t.text == "(":
            self.take()
            x = None if self.at(",") else self.stmt()
            if self.at(","):
                self.take()
                y = None if self.at(")") else self.stmt()
                self.expect(")")
                node: Node = Pair(x, y)
            else:
                self.expect(")")
                node = x if x is not None else Empty()
            return node
        if t.text == "{":
            self.take()
            self.brace_depth += 1
            items: list[Node] = []
            while not self.at("}"):
                a = self.stmt()
                if self.at(":"):
                    self.take()
                    hi = self.stmt()
                    step = None
                    if self.at(":"):
                        self.take()
                        step = self.stmt()
                    a = RangeLit(a, hi, step)
                items.append(a)
                if self.at(","):
                    self.take()
            self.expect("}")
            self.brace_depth -= 1
            return ListLit(items)
        raise ParseError(f"unexpected {t.text!r} at {t.pos} in {self.src!r}")


def parse(src: Optional[str]) -> Node:
    return Parser(src or "").program()


def walk(node: Node):
    """Yield every node in the tree."""
    yield node
    for v in vars(node).values():
        if isinstance(v, Node):
            yield from walk(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, Node):
                    yield from walk(x)
