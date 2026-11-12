"""Epic 1 / E1.3 (F11): the calc lexer.

`mdq._internal.calc_tokenize(text, first_line=1)` returns a list of dicts
{kind, text, line, col} (1-based, columns count characters) or raises
`mdq.MdqError` (MDQ_CALC_ERROR, or MDQ_RESOURCE_LIMIT for over-long numbers).

Lexer decisions where the spec grammar is silent are tagged with gap IDs in
docs/TEST_PLAN.md: G3 (`to` reserved, number glued to letters is an error),
G15 (unit token boundary), G16 (currency-shaped tokens), G17 (space before %).
"""
import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi
from oracles import units_oracle as oracle
from oracles.lexer_gen import programs

pytestmark = pytest.mark.unit

UNIT_SYMBOLS = sorted(s for s in oracle.SYMBOLS if s != "deltaK")


def tok(text, first_line=1):
    """Tokens as (kind, text), without newline tokens."""
    return [(t["kind"], t["text"]) for t in mi.calc_tokenize(text, first_line)
            if t["kind"] != "newline"]


def full(text, first_line=1):
    return [(t["kind"], t["text"], t["line"], t["col"]) for t in mi.calc_tokenize(text, first_line)]


# ---------------------------------------------------------------- general


@pytest.mark.parametrize("src, expected", [
    ("x = 1 + 2", [("ident", "x"), ("equals", "="), ("number", "1"), ("plus", "+"), ("number", "2")]),
    ("1+2*3", [("number", "1"), ("plus", "+"), ("number", "2"), ("star", "*"), ("number", "3")]),
    ("x = (1 + 2) / 3", [("ident", "x"), ("equals", "="), ("lparen", "("), ("number", "1"),
                         ("plus", "+"), ("number", "2"), ("rparen", ")"), ("slash", "/"), ("number", "3")]),
    ("x = -5", [("ident", "x"), ("equals", "="), ("minus", "-"), ("number", "5")]),
    ("x = +5", [("ident", "x"), ("equals", "="), ("plus", "+"), ("number", "5")]),
    ("x = 12 km", [("ident", "x"), ("equals", "="), ("number", "12"), ("unit", "km")]),
    ("x = 5 m2", [("ident", "x"), ("equals", "="), ("number", "5"), ("unit", "m2")]),
    ("x = 5 km to miles", [("ident", "x"), ("equals", "="), ("number", "5"), ("unit", "km"),
                           ("to", "to"), ("unit", "miles")]),
    ("x = 20 degC to degF", [("ident", "x"), ("equals", "="), ("number", "20"), ("unit", "degC"),
                             ("to", "to"), ("unit", "degF")]),
    ("speed = 90 km/h", [("ident", "speed"), ("equals", "="), ("number", "90"), ("unit", "km/h")]),
    ("x = 12 km / h", [("ident", "x"), ("equals", "="), ("number", "12"), ("unit", "km"),
                       ("slash", "/"), ("ident", "h")]),
    ("x = 12 km/hour", [("ident", "x"), ("equals", "="), ("number", "12"), ("unit", "km"),
                        ("slash", "/"), ("ident", "hour")]),
    ("x = 12 m/2", [("ident", "x"), ("equals", "="), ("number", "12"), ("unit", "m"),
                    ("slash", "/"), ("number", "2")]),
    ("x = 10 %", [("ident", "x"), ("equals", "="), ("number", "10"), ("percent", "%")]),
    ("x = 10%", [("ident", "x"), ("equals", "="), ("number", "10"), ("percent", "%")]),
    ("x = 5 EUR", [("ident", "x"), ("equals", "="), ("number", "5"), ("currency", "EUR")]),
    ("x = 5 EUR to USD", [("ident", "x"), ("equals", "="), ("number", "5"), ("currency", "EUR"),
                          ("to", "to"), ("currency", "USD")]),
    ("x = a to km", [("ident", "x"), ("equals", "="), ("ident", "a"), ("to", "to"), ("unit", "km")]),
    ("x = 5 to km", [("ident", "x"), ("equals", "="), ("number", "5"), ("to", "to"), ("unit", "km")]),
])
def test_token_kinds_and_text(src, expected):
    assert tok(src) == expected


@pytest.mark.parametrize("number", ["0", "7", "12", "0.5", "12.375", ".5", "100000"])
def test_decimal_literal_forms(number):
    assert tok(f"x = {number}")[-1] == ("number", number)  # text kept exactly as written


@pytest.mark.parametrize("sym", UNIT_SYMBOLS)
def test_every_catalogue_symbol_is_a_unit_after_a_number(sym):
    assert tok(f"x = 1 {sym}")[-1] == ("unit", sym)
    assert tok(f"x = 1 {sym} to {sym}")[-2:] == [("to", "to"), ("unit", sym)]


@pytest.mark.parametrize("word", ["h", "min", "m", "km", "EUR", "USD", "s", "day", "degC", "to5", "today", "tomato", "m2"])
def test_unit_like_words_are_identifiers_outside_unit_position(word):
    assert tok(f"{word} = 1") == [("ident", word), ("equals", "="), ("number", "1")]
    assert tok(f"x = {word} + 1")[2] == ("ident", word)


def test_to_is_reserved_not_an_identifier():
    assert tok("to") == [("to", "to")]
    assert tok("to = 5")[0] == ("to", "to")  # the parser rejects it, the lexer only tags it


@pytest.mark.parametrize("src, kinds", [
    ("x = 1 + 2 * 3", ["ident", "equals", "number", "plus", "number", "star", "number"]),
    ("a=(b)", ["ident", "equals", "lparen", "ident", "rparen"]),
])
def test_no_whitespace_needed_around_operators(src, kinds):
    assert [k for k, _ in tok(src)] == kinds


def test_deltaK_is_not_a_token():
    with pytest.raises(mdq.MdqError, match="unknown unit"):
        mi.calc_tokenize("x = 1 deltaK")


# ------------------------------------------------------- layout and edges


def test_empty_and_blank_inputs_give_no_tokens():
    for src in ["", "\n", "\n\n", "   \t\n", "\r\n", "# comment", "  # c\n# d\n", "\t#x"]:
        assert mi.calc_tokenize(src) == [], repr(src)


def test_every_statement_line_ends_with_one_newline_token():
    assert [t["kind"] for t in mi.calc_tokenize("a = 1\nb = 2")].count("newline") == 2
    assert [t["kind"] for t in mi.calc_tokenize("a = 1\nb = 2\n")].count("newline") == 2
    assert [t["kind"] for t in mi.calc_tokenize("a = 1\n\n\nb = 2\n\n")].count("newline") == 2


def test_exact_positions():
    assert full("x = 1 + 2") == [
        ("ident", "x", 1, 1), ("equals", "=", 1, 3), ("number", "1", 1, 5),
        ("plus", "+", 1, 7), ("number", "2", 1, 9), ("newline", "\n", 1, 10),
    ]


def test_newline_column_is_line_length_plus_one_including_trailing_whitespace():
    assert full("x = 1")[-1] == ("newline", "\n", 1, 6)
    assert full("x = 1  \n")[-1] == ("newline", "\n", 1, 8)
    assert full("x = 1\t")[-1] == ("newline", "\n", 1, 7)


def test_crlf_gives_same_tokens_and_columns_as_lf():
    lf = full("a = 1\nb = 2 km\n")
    assert full("a = 1\r\nb = 2 km\r\n") == lf
    assert full("a = 1\r\nb = 2 km") == lf


def test_line_numbers_count_blank_and_comment_lines():
    out = full("\n# c\n\nx = 1\n\ny = 2")
    assert [(t[2]) for t in out if t[0] == "ident"] == [4, 6]


def test_leading_indentation_shifts_columns():
    assert full("  x = 1")[0] == ("ident", "x", 1, 3)
    assert full("\tx = 1")[0] == ("ident", "x", 1, 2)


def test_tabs_are_whitespace():
    assert tok("x\t=\t5\tkm") == [("ident", "x"), ("equals", "="), ("number", "5"), ("unit", "km")]


def test_first_line_offsets_reported_lines():
    out = full("a = 1\nb = 2", first_line=10)
    assert {t[2] for t in out} == {10, 11}
    assert mi.calc_tokenize("x = 1", 1) == mi.calc_tokenize("x = 1")


def test_first_line_saturates_at_u32_max():
    top = 2**32 - 1
    assert {t[2] for t in full("x\ny", first_line=top)} == {top}


def test_comment_may_contain_any_characters():
    assert full("# héllo ✓ ; \" ^  \nx = 1")[0][2] == 2


def test_long_number_up_to_limit_is_accepted():
    n = "1" * 10000
    assert tok(f"x = {n}")[-1] == ("number", n)


def test_result_is_a_plain_list_of_dicts_with_fixed_keys():
    out = mi.calc_tokenize("x = 1")
    assert isinstance(out, list)
    assert all(set(t) == {"kind", "text", "line", "col"} for t in out)


# ----------------------------------------------------------------- errors


@pytest.mark.parametrize("src, col, fragment", [
    ("x = 1 + 2; y = 3", 10, ";"),
    ('x = stock("AAPL")', 11, "strings"),
    ("x = 'a'", 5, "strings"),
    ("x = 2^2", 6, "exponent"),
    ("x = 1 # c", 7, "inline comments"),
    ("x = 1e3", 5, "invalid number"),
    ("x = 12miles", 5, "invalid number"),
    ("x = 2x", 5, "invalid number"),
    ("x = 1_000", 5, "invalid number"),
    ("x = 1.", 5, "invalid number"),
    ("x = 1.2.3", 5, "invalid number"),
    ("x = 1,000", 6, "unexpected character"),
    ("x = 1 mile", 7, "unknown unit"),
    ("x = 1 KB", 7, "unknown unit"),
    ("x = 1 C", 7, "unknown unit"),
    ("x = 1 foo", 7, "unknown unit"),
    ("x = 1 GBPX", 7, "unknown unit"),
    ("x = 1 mins", 7, "unknown unit"),
    ("x = 1 eur", 7, "unknown unit"),
    ("x = 1 to foo", 10, "unknown unit"),
    ("x = a to mile", 10, "unknown unit"),
    ("x = a to to", 10, "unknown unit"),
    ("x = 1 to to", 10, "unknown unit"),
    ("x = @", 5, "unexpected character"),
    ("x = $5", 5, "unexpected character"),
    ("x = 5 & 3", 7, "unexpected character"),
    ("x = [1]", 5, "unexpected character"),
    ("x = a.b", 6, "unexpected character"),
    ("_x = 1", 1, "unexpected character"),
    ("x = 1 !", 7, "unexpected character"),
    ("x = \0", 5, "U+0000"),
    ("x = é", 5, "unexpected character"),
    ("x = ١", 5, "unexpected character"),         # Arabic-Indic digit is not a digit here
    ("x = 1 km", 6, "U+00A0"),                # no-break space is not whitespace
    ("﻿x = 1", 1, "U+FEFF"),                  # BOM (G5)
    ("x = 1\r2", 6, "carriage return"),            # lone CR
    ("x = 1\r", 6, "carriage return"),             # CR on an unterminated last line
    ("x = 1\r\r\n", 6, "carriage return"),         # only one CR belongs to CRLF
])
def test_lexer_errors_have_code_line_and_column(assert_mdq_error, src, col, fragment):
    with assert_mdq_error("MDQ_CALC_ERROR", line=1, column=col) as info:
        mi.calc_tokenize(src)
    assert fragment in str(info.value)  # note: the position is carried in .line/.column


def test_error_on_a_later_line_reports_that_line_and_first_line_offset(assert_mdq_error):
    src = "a = 1\nb = 2 # c\nc = 3"
    with assert_mdq_error("MDQ_CALC_ERROR", line=2, column=7):
        mi.calc_tokenize(src)
    with assert_mdq_error("MDQ_CALC_ERROR", line=11, column=7):
        mi.calc_tokenize(src, 10)


def test_first_error_wins(assert_mdq_error):
    with assert_mdq_error("MDQ_CALC_ERROR", line=1, column=5):
        mi.calc_tokenize("x = @ ;")


def test_over_long_number_is_a_resource_limit(assert_mdq_error):
    with assert_mdq_error("MDQ_RESOURCE_LIMIT", line=1, column=5):
        mi.calc_tokenize("x = " + "1" * 10001)
    with assert_mdq_error("MDQ_RESOURCE_LIMIT", line=2, column=1):
        mi.calc_tokenize("a = 1\n" + "9" * 10001)


@pytest.mark.parametrize("bad", [None, 5, b"x = 1", ["x"]])
def test_non_str_text_is_a_type_error(bad):
    with pytest.raises(TypeError):
        mi.calc_tokenize(bad)


def test_bad_first_line_arguments():
    with pytest.raises(TypeError):
        mi.calc_tokenize("x = 1", "1")
    with pytest.raises(OverflowError):
        mi.calc_tokenize("x = 1", -1)
    with pytest.raises(OverflowError):
        mi.calc_tokenize("x = 1", 2**32)


def test_lone_surrogate_is_a_value_error_not_a_panic():
    with pytest.raises(ValueError):
        mi.calc_tokenize("x = \ud800")


# --------------------------------------------------------------- properties


@given(programs())
def test_generated_programs_match_the_constructed_tokens(prog):
    text, expected = prog
    got = [(t["kind"], t["text"], t["line"] - 1, t["col"]) for t in mi.calc_tokenize(text)]
    assert got == expected


@given(programs(), st.integers(0, 1000))
def test_first_line_shifts_only_line_numbers(prog, offset):
    text, _ = prog
    base = mi.calc_tokenize(text, 1)
    shifted = mi.calc_tokenize(text, 1 + offset)
    assert [(t["kind"], t["text"], t["col"]) for t in base] == [(t["kind"], t["text"], t["col"]) for t in shifted]
    assert [t["line"] + offset for t in base] == [t["line"] for t in shifted]


_CORPUS = [
    "x = 1 + 2 * 3", "x = 5 km to miles", "speed = 90 km/h", "x = (a + b) / 2 m",
    "x = 20 degC to degF", "x = 5 EUR to USD", "x = 10 % + 5 %", "x = a to km",
]


@pytest.mark.parametrize("line", _CORPUS)
@given(seps=st.lists(st.text(" \t", min_size=1, max_size=4), min_size=12, max_size=12))
def test_whitespace_runs_do_not_change_kinds_or_text(line, seps):
    pieces = line.split(" ")
    rebuilt = pieces[0]
    for i, piece in enumerate(pieces[1:]):
        rebuilt += seps[i % len(seps)] + piece
    assert tok(rebuilt) == tok(line)


_FUZZ_CHARS = list("ab to km EUR 12.%+-*/()=#;\t\r\n \"é _^") + ["to", "km/h", "1.5", "\r\n"]


@given(st.lists(st.sampled_from(_FUZZ_CHARS), max_size=60).map("".join))
def test_fuzz_never_panics_and_keeps_invariants(src):
    lines = src.split("\n")
    try:
        out = mi.calc_tokenize(src)
    except mdq.MdqError as err:
        assert err.code in ("MDQ_CALC_ERROR", "MDQ_RESOURCE_LIMIT")
        assert err.line is not None and 1 <= err.line <= len(lines)
        assert err.column is not None and err.column >= 1
        return
    keys = [(t["line"], t["col"]) for t in out]
    assert keys == sorted(keys)
    assert all(t["line"] >= 1 and t["col"] >= 1 for t in out)
    def effective(i, ln):
        return ln[:-1] if (i < len(lines) - 1 and ln.endswith("\r")) else ln
    expect_lines = sum(
        1 for i, ln in enumerate(lines)
        if effective(i, ln).lstrip(" \t") != "" and not effective(i, ln).lstrip(" \t").startswith("#")
    )
    assert sum(t["kind"] == "newline" for t in out) == expect_lines
    assert mi.calc_tokenize(src) == out  # deterministic
