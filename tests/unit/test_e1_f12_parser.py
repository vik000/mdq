"""Epic 1 / E1.4 (F12): the calc parser.

`mdq._internal.calc_parse(text, first_line=1)` returns a list of assignments
{name, line, col, expr}; `expr` is a tree of dicts (kind, line, col + fields).
Tests render trees as S-expressions via `oracles.expr_gen.sexpr`:
  12 | [12 km] | x | (neg x) | (% x) | (+ a b) | (to x km)

The parser checks syntax and duplicate names only. Types, units, scopes and
forward references belong to the evaluator (E1.5). Decisions where the spec is
silent: G7 (`x%` parses, the evaluator checks dimensionless), G17 (`10 %` ok),
G18 (nesting limits).
"""
import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi
from oracles.expr_gen import blocks, expressions, sexpr

pytestmark = pytest.mark.unit


def ex(src, first_line=1):
    """S-expression of the single expression in `x = <src>`."""
    block = mi.calc_parse(f"x = {src}", first_line)
    assert len(block) == 1
    return sexpr(block[0]["expr"])


def tree(src):
    return mi.calc_parse(f"x = {src}")[0]["expr"]


# ------------------------------------------------- precedence and shape


@pytest.mark.parametrize("src, expected", [
    ("1", "1"),
    ("x", "x"),
    (".5", ".5"),
    ("1 + 2", "(+ 1 2)"),
    ("1 + 2 * 3", "(+ 1 (* 2 3))"),
    ("2 * 3 + 1", "(+ (* 2 3) 1)"),
    ("(1 + 2) * 3", "(* (+ 1 2) 3)"),
    ("1 - 2 - 3", "(- (- 1 2) 3)"),
    ("8 / 4 / 2", "(/ (/ 8 4) 2)"),
    ("1 * 2 / 3 * 4", "(* (/ (* 1 2) 3) 4)"),
    ("1 + 2 - 3 + 4", "(+ (- (+ 1 2) 3) 4)"),
    ("1 - (2 - 3)", "(- 1 (- 2 3))"),
    ("2 * (3 + 4) * 5", "(* (* 2 (+ 3 4)) 5)"),
    ("((1))", "1"),
    ("(((a)))", "a"),
    ("-5", "(neg 5)"),
    ("+5", "(pos 5)"),
    ("--5", "(neg (neg 5))"),
    ("-+5", "(neg (pos 5))"),
    ("- 5", "(neg 5)"),
    ("-a * b", "(* (neg a) b)"),
    ("a - -b", "(- a (neg b))"),
    ("a * -b", "(* a (neg b))"),
    ("a--b", "(- a (neg b))"),
    ("-(a + b)", "(neg (+ a b))"),
    ("21%", "(% 21)"),
    ("10 %", "(% 10)"),
    ("x%", "(% x)"),
    ("(5 + 5)%", "(% (+ 5 5))"),
    ("-5%", "(neg (% 5))"),              # % binds tighter than unary signs
    ("120 * 21%", "(* 120 (% 21))"),
    ("5 + 10%", "(+ 5 (% 10))"),
    ("(5 kg)%", "(% [5 kg])"),            # parenthesised: the evaluator decides
    ("12 miles", "[12 miles]"),
    ("5 EUR", "[5 EUR]"),
    ("90 km/h", "[90 km/h]"),
    ("12 km / h", "(/ [12 km] h)"),
    ("12 m/2", "(/ [12 m] 2)"),
    ("2 kg * 3", "(* [2 kg] 3)"),
    ("2 kg + 3 m", "(+ [2 kg] [3 m])"),
    ("12 miles to km", "(to [12 miles] km)"),
    ("5 EUR to USD", "(to [5 EUR] USD)"),
    ("32 degF to degC", "(to [32 degF] degC)"),
    ("a to km", "(to a km)"),
    ("12 to km", "(to 12 km)"),
    ("a + b to km", "(to (+ a b) km)"),      # to binds weakest
    ("a * 2 to km", "(to (* a 2) km)"),
    ("-a to km", "(to (neg a) km)"),
    ("5% to km", "(to (% 5) km)"),
    ("(a to km) / 2", "(/ (to a km) 2)"),
    ("(a to km) to m", "(to (to a km) m)"),
    ("-(a to km)", "(neg (to a km))"),
    ("1.5 + .5", "(+ 1.5 .5)"),
])
def test_expression_shape(src, expected):
    assert ex(src) == expected


def test_spec_vectors_parse():  # extended-markdown.md 8.9 (syntax only)
    for src, expected in [
        ("10", "10"),
        ("2 + 3 * 4", "(+ 2 (* 3 4))"),
        ("(2 + 3) * 4", "(* (+ 2 3) 4)"),
        ("120 * 21%", "(* 120 (% 21))"),
        ("12 miles to km", "(to [12 miles] km)"),
        ("32 degF to degC", "(to [32 degF] degC)"),
        ("10 / 0", "(/ 10 0)"),          # division by zero is an evaluation error
        ("2 kg + 3 m", "(+ [2 kg] [3 m])"),  # so are incompatible units
    ]:
        assert ex(src) == expected


def test_quantity_tree_fields():
    node = tree("12 miles")
    assert node["kind"] == "quantity"
    assert (node["text"], node["unit"], node["currency"]) == ("12", "miles", False)
    assert tree("5 EUR")["currency"] is True
    assert tree("a to EUR")["currency"] is True
    assert tree("a to km")["currency"] is False


def test_numbers_keep_their_written_text():
    assert tree("7.50")["text"] == "7.50"
    assert tree(".5")["text"] == ".5"


# ----------------------------------------------------------- statements


def test_empty_blocks_are_valid():
    for src in ["", "\n", "# only a comment", "  \n# c\n\n"]:
        assert mi.calc_parse(src) == [], repr(src)


def test_the_spec_example_block():
    src = "price = 120 EUR\nquantity = 4\nsubtotal = price * quantity\ntax = subtotal * 21%\ntotal = subtotal + tax\n"
    block = mi.calc_parse(src)
    assert [a["name"] for a in block] == ["price", "quantity", "subtotal", "tax", "total"]
    assert [sexpr(a["expr"]) for a in block] == [
        "[120 EUR]", "4", "(* price quantity)", "(* subtotal (% 21))", "(+ subtotal tax)"]
    assert [a["line"] for a in block] == [1, 2, 3, 4, 5]
    assert all(a["col"] == 1 for a in block)


def test_names_are_case_sensitive_and_may_look_like_units():
    block = mi.calc_parse("a = 1\nA = 2\nkm = 3\nEUR = 4\nh = 5")
    assert [a["name"] for a in block] == ["a", "A", "km", "EUR", "h"]


def test_references_are_not_resolved_by_the_parser():
    # forward and unknown references are evaluator errors, not syntax errors
    assert [sexpr(a["expr"]) for a in mi.calc_parse("a = b\nb = 1\nc = nope")] == ["b", "1", "nope"]


def test_blank_and_comment_lines_are_skipped_and_counted():
    block = mi.calc_parse("# c\n\n  x = 1\n\n# d\ny = 2")
    assert [(a["name"], a["line"], a["col"]) for a in block] == [("x", 3, 3), ("y", 6, 1)]


def test_crlf_and_first_line():
    block = mi.calc_parse("a = 1\r\nb = 2\r\n", first_line=10)
    assert [(a["name"], a["line"]) for a in block] == [("a", 10), ("b", 11)]
    assert block[1]["expr"]["line"] == 11


def test_node_positions():
    # x = 1 + 2 * 3
    e = tree("1 + 2 * 3")
    assert (e["kind"], e["col"]) == ("binary", 7)             # the `+`
    assert (e["lhs"]["col"], e["rhs"]["col"]) == (5, 11)      # `1`, the `*`
    assert (e["rhs"]["lhs"]["col"], e["rhs"]["rhs"]["col"]) == (9, 13)


@pytest.mark.parametrize("src, col", [
    ("-a", 5),        # unary: the sign
    ("10 %", 8),      # percent: the `%`
    ("5 km to m", 10),  # convert: the `to`
    ("(1)", 6),       # parentheses add no node: the inner number
    ("5 km", 5),      # quantity: the number
])
def test_node_position_is_the_locating_token(src, col):
    assert tree(src)["col"] == col


def test_positions_inside_parentheses():
    e = tree("(a + b) * c")                                   # x = (a + b) * c
    assert e["col"] == 13
    assert (e["lhs"]["col"], e["lhs"]["lhs"]["col"], e["lhs"]["rhs"]["col"]) == (8, 6, 10)


# -------------------------------------------------------------- errors


@pytest.mark.parametrize("src, col, fragment", [
    ("x = 1 +", 8, "expected an expression, found end of line"),
    ("x =", 4, "expected an expression"),
    ("x = * 2", 5, "found `*`"),
    ("x = 1 // 2", 8, "found `/`"),
    ("x = 1 ** 2", 8, "found `*`"),
    ("x = 1 == 2", 7, "unexpected `=`"),
    ("x == 1", 4, "`=` is not allowed in an expression"),
    ("x = y = 3", 7, "unexpected `=`"),
    ("x = (1 + 2", 11, "expected `)`"),
    ("x = 1 + 2)", 10, "unexpected `)`"),
    ("x = ()", 6, "found `)`"),
    ("x = (", 6, "found end of line"),
    ("x = 2(3)", 6, "implicit multiplication"),
    ("x = a b", 7, "implicit multiplication"),
    ("x = 1 2", 7, "implicit multiplication"),
    ("x = a (b)", 7, "implicit multiplication"),
    ("x = stock(5)", 10, "implicit multiplication"),   # no function calls
    ("x = 2 kg%", 9, "quantity with a unit"),
    ("x = 5 EUR %", 11, "quantity with a unit"),
    ("x = 5%%", 7, "applied twice"),
    ("x = 5 % %", 9, "applied twice"),
    ("x = a to km to m", 13, "chained conversions"),
    ("x = 5 km to m to ft", 15, "chained conversions"),
    ("x = a to 5", 10, "expected a unit after `to`"),
    ("x = a to", 9, "found end of line"),
    ("x = a to (km)", 10, "expected a unit after `to`"),
    ("x = to", 5, "reserved word"),
    ("x = 1 + to", 9, "reserved word"),
    ("to = 5", 1, "reserved word and cannot be used as a name"),
    ("5 = x", 1, "expected a name"),
    ("= 5", 1, "expected a name"),
    ("(a) = 5", 1, "expected a name"),
    ("-a = 5", 1, "expected a name"),
    ("x", 2, "expected `=` after `x`"),
    ("x 5", 3, "expected `=` after `x`"),
    ("x - 5", 3, "expected `=` after `x`"),
    ("x = 1 + 2; y = 3", 10, ";"),               # spec vector: lexer error surfaces
    ("x = 2^2", 6, "exponent"),
])
def test_syntax_errors(assert_mdq_error, src, col, fragment):
    with assert_mdq_error("MDQ_CALC_ERROR", line=1, column=col) as info:
        mi.calc_parse(src)
    assert fragment in str(info.value)


def test_errors_report_the_right_line_and_honour_first_line(assert_mdq_error):
    with assert_mdq_error("MDQ_CALC_ERROR", line=2, column=7):
        mi.calc_parse("a = 1\nb = (2")
    with assert_mdq_error("MDQ_CALC_ERROR", line=12, column=7):
        mi.calc_parse("a = 1\nb = (2", first_line=11)


def test_a_statement_ends_at_the_newline(assert_mdq_error):
    with assert_mdq_error("MDQ_CALC_ERROR", line=1, column=8):
        mi.calc_parse("a = 1 +\n2")


def test_first_syntax_error_wins(assert_mdq_error):
    with assert_mdq_error("MDQ_CALC_ERROR", line=1, column=6):
        mi.calc_parse("x = (\ny = (")


def test_lexical_errors_are_reported_before_syntax_errors(assert_mdq_error):
    # the whole block is tokenised first, so the `;` on line 2 wins over the
    # unclosed parenthesis on line 1 (documented behaviour, not a spec rule)
    with assert_mdq_error("MDQ_CALC_ERROR", line=2, column=5):
        mi.calc_parse("x = (\ny = ;")


def test_duplicate_reports_line_and_first_line(assert_mdq_error):
    with assert_mdq_error("MDQ_CALC_ERROR", line=3, column=1) as info:
        mi.calc_parse("a = 1\nb = 2\na = 3")
    assert "`a`" in str(info.value) and "line 1" in str(info.value)
    with assert_mdq_error("MDQ_CALC_ERROR", line=12, column=1) as info:
        mi.calc_parse("a = 1\nb = 2\na = 3", first_line=10)
    assert "line 10" in str(info.value)


def test_duplicate_detection_ignores_blank_lines_and_respects_case(assert_mdq_error):
    assert len(mi.calc_parse("a = 1\nA = 2")) == 2
    with assert_mdq_error("MDQ_CALC_ERROR", line=5, column=3):
        mi.calc_parse("a = 1\n\n# c\n\n  a = 2")


# ------------------------------------------------------------- limits


def test_nesting_limit_parentheses(assert_mdq_error):
    assert ex("(" * 199 + "1" + ")" * 199) == "1"
    with assert_mdq_error("MDQ_RESOURCE_LIMIT", line=1):
        mi.calc_parse("x = " + "(" * 200 + "1" + ")" * 200)


def test_nesting_limit_unary_signs(assert_mdq_error):
    assert len(mi.calc_parse("x = " + "-" * 199 + "1")) == 1
    with assert_mdq_error("MDQ_RESOURCE_LIMIT", line=1):
        mi.calc_parse("x = " + "-" * 200 + "1")


def test_hostile_nesting_does_not_crash(assert_mdq_error):
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        mi.calc_parse("x = " + "(" * 100_000)
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        mi.calc_parse("x = " + "-" * 100_000 + "1")


def test_tree_depth_limit_on_long_chains(assert_mdq_error):
    assert len(mi.calc_parse("x = " + "+".join(["1"] * 500))) == 1
    with assert_mdq_error("MDQ_RESOURCE_LIMIT", line=1):
        mi.calc_parse("x = " + "+".join(["1"] * 501))
    with assert_mdq_error("MDQ_RESOURCE_LIMIT", line=1):
        mi.calc_parse("x = " + "*".join(["a"] * 501))


def test_long_but_shallow_blocks_are_fine():
    block = mi.calc_parse("\n".join(f"v{i} = {i} + 1" for i in range(2000)))
    assert len(block) == 2000


# --------------------------------------------------------- type errors


@pytest.mark.parametrize("bad", [None, 5, b"x = 1"])
def test_non_str_text_is_a_type_error(bad):
    with pytest.raises(TypeError):
        mi.calc_parse(bad)


def test_bad_first_line():
    with pytest.raises(TypeError):
        mi.calc_parse("x = 1", "1")
    with pytest.raises(OverflowError):
        mi.calc_parse("x = 1", -1)


def test_lone_surrogate_is_a_value_error():
    with pytest.raises(ValueError):
        mi.calc_parse("x = \ud800")


# ----------------------------------------------------------- properties


@given(expressions())
def test_generated_expressions_have_the_constructed_shape(case):
    src, expected = case
    assert ex(src) == expected


@given(blocks())
def test_generated_blocks(case):
    text, expected = case
    got = [(a["name"], a["line"], a["col"], sexpr(a["expr"])) for a in mi.calc_parse(text)]
    assert got == expected


@given(blocks(), st.integers(0, 500))
def test_first_line_shifts_only_lines(case, offset):
    text, _ = case
    base = mi.calc_parse(text, 1)
    moved = mi.calc_parse(text, 1 + offset)
    assert [sexpr(a["expr"]) for a in base] == [sexpr(a["expr"]) for a in moved]
    assert [a["line"] + offset for a in base] == [a["line"] for a in moved]


@given(expressions())
def test_extra_parentheses_never_change_the_tree(case):
    src, expected = case
    assert ex("(" + src + ")") == expected
    assert ex("( " + src + " )") == expected


_FUZZ = list("ab to km EUR 12.%+-*/()=#;\t\r\n \"é") + ["to", "km/h", "1.5", "x =", "\n"]


@given(st.lists(st.sampled_from(_FUZZ), max_size=50).map("".join))
def test_fuzz_never_panics_and_keeps_invariants(src):
    lines = src.split("\n")
    try:
        block = mi.calc_parse(src)
    except mdq.MdqError as err:
        assert err.code in ("MDQ_CALC_ERROR", "MDQ_RESOURCE_LIMIT")
        assert err.line is not None and 1 <= err.line <= len(lines)
        assert err.column is not None and err.column >= 1
        return

    def effective(i, ln):
        return ln[:-1] if (i < len(lines) - 1 and ln.endswith("\r")) else ln

    statements = sum(
        1 for i, ln in enumerate(lines)
        if effective(i, ln).lstrip(" \t") != "" and not effective(i, ln).lstrip(" \t").startswith("#")
    )
    assert len(block) == statements
    names = [a["name"] for a in block]
    assert len(set(names)) == len(names)
    assert [a["line"] for a in block] == sorted(a["line"] for a in block)
    assert mi.calc_parse(src) == block  # deterministic
