"""Generative oracle for the calc parser (tests only).

Draws a random expression TREE, renders it to source text with the minimal
parentheses its shape needs (plus random redundant ones), and returns the
S-expression the parser must produce. The tree is the ground truth, so this
checks precedence and associativity independently of the Rust parser.

S-expression format (shared with `sexpr()` below, used on the parser output):
  number 12 -> 12          quantity -> [12 km]        ident -> x
  unary     -> (neg x) / (pos x)      percent -> (% x)
  binary    -> (+ a b)                convert -> (to x km)
"""
from hypothesis import strategies as st

from oracles.lexer_gen import CURRENCIES, UNIT_SYMBOLS, idents, numbers

# Levels: to=0, sum=1, product=2, unary=3, postfix=4, atom=5
_ws = st.text(" \t", min_size=1, max_size=2)
_opt_ws = st.text(" \t", min_size=0, max_size=2)
_units = st.sampled_from(UNIT_SYMBOLS + CURRENCIES)


def sexpr(node):
    """S-expression of a parser tree (the dicts returned by `calc_parse`)."""
    k = node["kind"]
    if k == "number":
        return node["text"]
    if k == "quantity":
        return f"[{node['text']} {node['unit']}]"
    if k == "ident":
        return node["name"]
    if k == "unary":
        return f"({'neg' if node['op'] == '-' else 'pos'} {sexpr(node['operand'])})"
    if k == "percent":
        return f"(% {sexpr(node['operand'])})"
    if k == "binary":
        return f"({node['op']} {sexpr(node['lhs'])} {sexpr(node['rhs'])})"
    if k == "convert":
        return f"(to {sexpr(node['operand'])} {node['unit']})"
    raise AssertionError(k)


def ast_sexpr(a):
    t = a[0]
    if t == "num":
        return a[1]
    if t == "q":
        return f"[{a[1]} {a[2]}]"
    if t == "id":
        return a[1]
    if t == "un":
        return f"({'neg' if a[1] == '-' else 'pos'} {ast_sexpr(a[2])})"
    if t == "pct":
        return f"(% {ast_sexpr(a[1])})"
    if t == "bin":
        return f"({a[1]} {ast_sexpr(a[2])} {ast_sexpr(a[3])})"
    if t == "to":
        return f"(to {ast_sexpr(a[1])} {a[2]})"
    raise AssertionError(t)


def _tree(decimal_only=False):
    if decimal_only:
        leaves = st.builds(lambda n: ("num", n), numbers)
    else:
        leaves = st.one_of(
            st.builds(lambda n: ("num", n), numbers),
            st.builds(lambda n, u: ("q", n, u), numbers, _units),
            st.builds(lambda n: ("id", n), idents),
        )

    def extend(children):
        options = [
            st.builds(lambda o, c: ("un", o, c), st.sampled_from("+-"), children),
            st.builds(lambda c: ("pct", c), children),
            st.builds(lambda o, l, r: ("bin", o, l, r), st.sampled_from("+-*/"), children, children),
        ]
        if not decimal_only:
            options.append(st.builds(lambda c, u: ("to", c, u), children, _units))
        return st.one_of(*options)

    return st.recursive(leaves, extend, max_leaves=10)


@st.composite
def _render(draw, a):
    """-> (source, level) for AST `a`."""

    def child(sub, need):
        text, level = draw(_render(sub))
        if level < need or draw(st.integers(0, 9)) == 0:  # needed, or random redundant parens
            return "(" + draw(_opt_ws) + text + draw(_opt_ws) + ")", 5
        return text, level

    t = a[0]
    if t == "num":
        return a[1], 5
    if t == "q":
        return a[1] + draw(_ws) + a[2], 5
    if t == "id":
        return a[1], 5
    if t == "un":
        c, _ = child(a[2], 3)
        return a[1] + draw(_opt_ws) + c, 3
    if t == "pct":
        sub = a[1]
        # `%` needs an atom; a bare quantity may not take `%` at all, so wrap it
        c, lvl = child(sub, 5)
        if sub[0] == "q":
            c = "(" + c + ")"
        return c + draw(_opt_ws) + "%", 4
    if t == "bin":
        op = a[1]
        lneed, rneed, level = (1, 2, 1) if op in "+-" else (2, 3, 2)
        l, _ = child(a[2], lneed)
        r, _ = child(a[3], rneed)
        return l + draw(_ws) + op + draw(_ws) + r, level
    if t == "to":
        c, _ = child(a[1], 1)
        return c + draw(_ws) + "to" + draw(_ws) + a[2], 0
    raise AssertionError(t)


@st.composite
def expressions(draw, decimal_only=False):
    """-> (source, expected_sexpr)"""
    ast = draw(_tree(decimal_only))
    text, _ = draw(_render(ast))
    return text, ast_sexpr(ast)


@st.composite
def blocks(draw, decimal_only=False):
    """A whole calc block -> (text, [(name, line, col, sexpr)]). Names are unique."""
    n = draw(st.integers(0, 5))
    lines, expected = [], []
    for i in range(n):
        for _ in range(draw(st.integers(0, 2))):  # blank/comment filler
            lines.append(draw(st.sampled_from(["", "   ", "# note", "\t# note ; ^ \"x\""])))
        src, sx = draw(expressions(decimal_only))
        name = f"v{i}"
        indent = draw(_opt_ws)
        lines.append(f"{indent}{name}{draw(_opt_ws)}={draw(_opt_ws)}{src}{draw(_opt_ws)}")
        expected.append((name, len(lines), len(indent) + 1, sx))
    sep = draw(st.sampled_from(["\n", "\r\n"]))
    return sep.join(lines) + draw(st.sampled_from(["", sep])), expected
