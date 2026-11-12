"""Epic 1 / E1.5 (F15): the calc evaluator and dimension algebra.

`mdq._internal.calc_eval(text, variables=None, first_line=1)` returns one dict
per assignment: name, line, col, type ("decimal" | "quantity" | "money"),
value (canonical string), unit (quantity) and currency (money).
`variables` maps names to calc literals ("120 EUR", "-5 degC", "4").

Decisions where the spec is silent are tagged in docs/TEST_PLAN.md:
G2 (derived result units), G8 (`100 + 10%` is 100.1), G12 (unary minus on every
type), G14 (deltaK), G16 (any 3-capital code is a currency until the host
declares a list), G19 (quantity addition converts, then adds).
"""
import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi
from oracles import calc_oracle
from oracles.expr_gen import blocks, expressions
from oracles.lexer_gen import CURRENCIES, numbers
from oracles.units_oracle import SYMBOLS, same_dimension_pairs

pytestmark = pytest.mark.unit

UNIT_SYMBOLS = [s for s in SYMBOLS if s != "deltaK"]


def last(src, variables=None, first_line=1):
    return mi.calc_eval(src, variables, first_line)[-1]


def text(src, variables=None):
    """Rendered result of the last assignment: `25.2`, `19.312128 km`, `5 EUR`."""
    r = last(src, variables)
    suffix = r["unit"] or r["currency"]
    return r["value"] + (" " + suffix if suffix else "")


# ------------------------------------------------------ spec vectors (8.9)


@pytest.mark.parametrize("src, expected", [
    ("x = 10", "10"),
    ("x = 2 + 3 * 4", "14"),
    ("x = (2 + 3) * 4", "20"),
    ("x = 120 * 21%", "25.2"),
    ("x = 12 miles to km", "19.312128 km"),
    ("x = 32 degF to degC", "0 degC"),
])
def test_spec_vectors(src, expected):
    assert text(src) == expected


def test_spec_example_block():
    src = "price = 120 EUR\nquantity = 4\nsubtotal = price * quantity\ntax = subtotal * 21%\ntotal = subtotal + tax"
    out = mi.calc_eval(src)
    assert [(r["name"], r["type"], r["value"], r["currency"]) for r in out] == [
        ("price", "money", "120", "EUR"), ("quantity", "decimal", "4", None),
        ("subtotal", "money", "480", "EUR"), ("tax", "money", "100.8", "EUR"),
        ("total", "money", "580.8", "EUR"),
    ]


def test_result_fields_and_positions():
    out = mi.calc_eval("# c\n\n  a = 2 km\nb = 3", first_line=5)
    assert out == [
        {"name": "a", "line": 7, "col": 3, "type": "quantity", "value": "2", "unit": "km", "currency": None},
        {"name": "b", "line": 8, "col": 1, "type": "decimal", "value": "3", "unit": None, "currency": None},
    ]


def test_empty_block_has_no_results():
    assert mi.calc_eval("") == []
    assert mi.calc_eval("# nothing\n\n") == []


# ---------------------------------------------------------- decimals


@pytest.mark.parametrize("src, expected", [
    ("1 / 3", "0.3333333333333333333333333333"),
    ("2 / 3", "0.6666666666666666666666666667"),
    ("1 / 7", "0.1428571428571428571428571429"),
    ("0.1 + 0.2", "0.3"),
    ("100 + 10%", "100.1"),                      # G8: a percentage is a factor
    ("10%", "0.1"),
    ("(5 + 5)%", "0.1"),
    ("-5%", "-0.05"),
    ("5 * -2", "-10"),
    ("-0", "0"),
    ("1 - 1", "0"),
    ("8 / 4 / 2", "1"),
    ("2 * 3 / 4", "1.5"),
    ("- - 5", "5"),
    ("+5", "5"),
    ("007.50 + 0", "7.5"),
    ("9999999999999999999999999999 + 1", "10000000000000000000000000000"),
    ("0.0000000000000000000000000001 * 0.1", "0.00000000000000000000000000001"),
])
def test_decimal_arithmetic(src, expected):
    assert text(f"x = {src}") == expected


def test_each_step_rounds_once_to_28_digits():
    # 1/3*3 is not 1: the quotient is rounded first (spec: 28 digits, half-even)
    assert text("x = 1 / 3 * 3") == "0.9999999999999999999999999999"


def test_percent_of_a_variable():
    assert text("x = r%", {"r": "50"}) == "0.5"


# -------------------------------------------------------- quantities


@pytest.mark.parametrize("src, expected", [
    ("5 km + 300 m", "5.3 km"),                  # converted to the LEFT unit
    ("300 m + 5 km", "5300 m"),
    ("1 h + 30 min", "1.5 h"),
    ("2 km - 500 m", "1.5 km"),
    ("5 kg + 5 kg", "10 kg"),
    ("5 kg * 2", "10 kg"),
    ("2 * 5 kg", "10 kg"),
    ("10 kg / 4", "2.5 kg"),
    ("10 kg / 4 kg", "2.5"),
    ("6 km / 2 km", "3"),
    ("5 km / 2 m", "2500"),                      # ratio after converting to base units
    ("-5 km", "-5 km"),
    ("-(5 km)", "-5 km"),
    ("+5 km", "5 km"),
    ("5 km to m", "5000 m"),
    ("5 km to km", "5 km"),
    ("1 km + 500 m to m", "1500 m"),             # `to` binds weakest
    ("1 mph to km/h", "1.609344 km/h"),
    ("1 h to s", "3600 s"),
    ("(1 km to m) + 1 m", "1001 m"),
])
def test_quantity_arithmetic(src, expected):
    assert text(f"x = {src}") == expected


@pytest.mark.parametrize("src, expected", [   # calculation-units.md section 6
    ("5 m * 3 m", "15 m2"),
    ("5 km * 2 km", "10 km2"),
    ("5 ft * 2 ft", "10 ft2"),
    ("2 m2 * 3 m", "6 m3"),
    ("5 km * 2 m", "10000 m2"),
    ("90 km / 2 h", "45 km/h"),
    ("10 m / 2 s", "5 m/s"),
    ("100 miles / 2 h", "22.352 m/s"),
    ("10 W * 2 h", "72000 J"),
    ("10 km2 / 2 km", "5000 m"),
])
def test_derived_result_units(src, expected):
    assert text(f"x = {src}") == expected


@pytest.mark.parametrize("src, fragment", [
    ("5 m * 3 s", "no catalogue unit"),
    ("2 kg * 3 m", "no catalogue unit"),
    ("1 / 2 s", "inverse dimension"),
    ("5 km to kg", "incompatible dimensions"),
])
def test_unsupported_unit_combinations(assert_mdq_error, src, fragment):
    with assert_mdq_error("MDQ_CALC_ERROR", line=1) as info:
        mi.calc_eval(f"x = {src}")
    assert fragment in str(info.value)


# ------------------------------------------------------- temperatures


@pytest.mark.parametrize("src, expected", [
    ("20 degC to degF", "68 degF"),
    ("-40 degC to degF", "-40 degF"),
    ("0 degC to K", "273.15 K"),
    ("100 degC to degF", "212 degF"),
    ("-5 degC", "-5 degC"),
    ("-(5 degC)", "-5 degC"),                      # G12
    ("20 degC - 5 degC", "15 deltaK"),
    ("68 degF - 32 degF", "20 deltaK"),           # 36 * 5/9, exact
    ("20 degC - 5 K", "288.15 deltaK"),
    ("(20 degC - 5 degC) * 2", "30 deltaK"),
    ("2 * (20 degC - 5 degC)", "30 deltaK"),
    ("(20 degC - 5 degC) / 3", "5 deltaK"),
    ("(20 degC - 5 degC) + (10 degC - 5 degC)", "20 deltaK"),
    ("(20 degC - 5 degC) / (5 degC - 0 degC)", "3"),   # G14: a plain ratio
])
def test_temperatures(src, expected):
    assert text(f"x = {src}") == expected


@pytest.mark.parametrize("src, col, fragment", [
    ("x = 20 degC + 5 degC", 13, "cannot be added"),
    ("x = 20 degC * 2", 13, "cannot be multiplied or divided"),
    ("x = 2 * 20 degC", 7, "cannot be multiplied or divided"),
    ("x = 20 degC / 2", 13, "cannot be multiplied or divided"),
    ("x = 20 degC + 5", 13, "cannot add"),
    ("x = 5 degC * 2 kg", 12, "cannot be multiplied or divided"),
    ("x = (20 degC - 5 degC) + 1 K", 24, "temperature difference"),
    ("x = (20 degC - 5 degC) * (2 degC - 1 degC)", 24, "can only be multiplied or divided"),
    ("x = (20 degC - 5 degC) to K", 24, "incompatible dimensions"),
])
def test_temperature_misuse(assert_mdq_error, src, col, fragment):
    with assert_mdq_error("MDQ_CALC_ERROR", line=1, column=col) as info:
        mi.calc_eval(src)
    assert fragment in str(info.value)


# ------------------------------------------------------------- money


@pytest.mark.parametrize("src, expected", [
    ("120 EUR * 4", "480 EUR"),
    ("4 * 120 EUR", "480 EUR"),
    ("120 EUR / 4", "30 EUR"),
    ("120 EUR + 5 EUR", "125 EUR"),
    ("120 EUR - 5 EUR", "115 EUR"),
    ("120 EUR / 40 EUR", "3"),
    ("-120 EUR", "-120 EUR"),
    ("120 EUR to EUR", "120 EUR"),
    ("120 EUR * 21%", "25.2 EUR"),
    ("120 EUR + 120 EUR * 21%", "145.2 EUR"),
])
def test_money(src, expected):
    assert text(f"x = {src}") == expected


@pytest.mark.parametrize("src, col, fragment", [
    ("x = 120 EUR + 5 USD", 13, "convert one with `to` first"),
    ("x = 120 EUR - 5 USD", 13, "convert one with `to` first"),
    ("x = 120 EUR / 40 USD", 13, "convert one with `to` first"),
    ("x = 120 EUR * 2 EUR", 13, "two money amounts"),
    ("x = 120 EUR * 2 kg", 13, "cannot multiply"),
    ("x = 120 EUR + 5", 13, "cannot add"),
    ("x = 120 EUR + 5 kg", 13, "cannot add"),
    ("x = 5 / 2 EUR", 7, "plain number by money"),
    ("x = 120 EUR to km", 13, "cannot convert money"),
    ("x = 5 km to EUR", 10, "cannot convert"),
    ("x = 5 to EUR", 7, "cannot convert"),
    ("x = (5 EUR)%", 12, "dimensionless"),
])
def test_money_errors(assert_mdq_error, src, col, fragment):
    with assert_mdq_error("MDQ_CALC_ERROR", line=1, column=col) as info:
        mi.calc_eval(src)
    assert fragment in str(info.value)


def test_conversion_to_another_currency_needs_an_observation(assert_mdq_error):
    with assert_mdq_error("MDQ_SOURCE_UNRESOLVED", line=1, column=13) as info:
        mi.calc_eval("x = 120 EUR to USD")
    assert "EUR" in str(info.value) and "USD" in str(info.value)


# ------------------------------------------------------------ errors


@pytest.mark.parametrize("src, line, col, fragment", [
    ("x = 10 / 0", 1, 8, "division by zero"),
    ("x = 1 / (2 - 2)", 1, 7, "division by zero"),
    ("x = 0 / 0", 1, 7, "division by zero"),
    ("x = 5 km / 0", 1, 10, "division by zero"),
    ("x = 5 km / 0 m", 1, 10, "division by zero"),
    ("x = 5 EUR / 0", 1, 11, "division by zero"),
    ("x = 5 EUR / 0 EUR", 1, 11, "division by zero"),
    ("x = 2 kg + 3 m", 1, 10, "incompatible dimensions"),
    ("x = 2 kg * 3 m", 1, 10, "no catalogue unit"),
    ("x = 5 to km", 1, 7, "plain number"),
    ("x = (5 kg)%", 1, 11, "dimensionless"),
    ("x = a", 1, 5, "unknown name `a`"),
    ("x = 1 + b", 1, 9, "unknown name `b`"),
    ("a = b\nb = 1", 1, 5, "used before its assignment on line 2"),
    ("x = x + 1", 1, 5, "used before its assignment on line 1"),
    ("a = 1\nb = a / 0", 2, 7, "division by zero"),
])
def test_evaluation_errors(assert_mdq_error, src, line, col, fragment):
    with assert_mdq_error("MDQ_CALC_ERROR", line=line, column=col) as info:
        mi.calc_eval(src)
    assert fragment in str(info.value)


def test_errors_honour_first_line(assert_mdq_error):
    with assert_mdq_error("MDQ_CALC_ERROR", line=11, column=7):
        mi.calc_eval("a = 1\nb = a / 0", first_line=10)


def test_numeric_overflow_is_a_calc_error(assert_mdq_error):
    big = "1" + "0" * 5000
    src = f"x = {big} * {big}"
    with assert_mdq_error("MDQ_CALC_ERROR", line=1, column=src.index("*") + 1) as info:
        mi.calc_eval(src)
    assert "overflow" in str(info.value)


def test_syntax_and_lexer_errors_still_surface(assert_mdq_error):
    with assert_mdq_error("MDQ_CALC_ERROR", line=1, column=10):
        mi.calc_eval("x = 1 + 2; y = 3")
    with assert_mdq_error("MDQ_CALC_ERROR", line=3, column=1):
        mi.calc_eval("a = 1\nb = 2\na = 3")
    with assert_mdq_error("MDQ_RESOURCE_LIMIT", line=1):
        mi.calc_eval("x = " + "(" * 200 + "1" + ")" * 200)


def test_first_error_aborts_the_block(assert_mdq_error):
    with assert_mdq_error("MDQ_CALC_ERROR", line=2):
        mi.calc_eval("a = 1\nb = 1 / 0\nc = nope")


# ------------------------------------------------- scopes and variables


def test_local_assignments_chain():
    out = mi.calc_eval("a = 2\nb = a * 3\nc = b + a")
    assert [r["value"] for r in out] == ["2", "6", "8"]


def test_host_variables_are_typed_inputs():
    v = {"price": "120 EUR", "n": "4", "t": "-5 degC", "d": "12 miles"}
    assert text("total = price * n", v) == "480 EUR"
    assert text("x = t", v) == "-5 degC"
    assert text("x = d to km", v) == "19.312128 km"


def test_local_assignment_shadows_a_host_variable_for_the_rest_of_the_block():
    out = mi.calc_eval("a = a + 1\nb = a", {"a": "1"})
    assert [r["value"] for r in out] == ["2", "2"]


def test_a_host_variable_wins_over_a_later_local_assignment():
    out = mi.calc_eval("b = a\na = 5", {"a": "1"})
    assert [r["value"] for r in out] == ["1", "5"]


def test_host_variables_do_not_appear_in_results():
    assert [r["name"] for r in mi.calc_eval("x = n", {"n": "1"})] == ["x"]


@pytest.mark.parametrize("name", ["1x", "to", "a-b", "a b", "", "_x", "é"])
def test_bad_host_variable_names(assert_mdq_error, name):
    with assert_mdq_error("MDQ_CALC_ERROR") as info:
        mi.calc_eval("x = 1", {name: "1"})
    assert "invalid variable name" in str(info.value)


@pytest.mark.parametrize("value", ["", "abc", "1 + 2", "5 foo", "1\n2", "a", "5 # c", "(1) + 1", "1 to km", "2 kg%"])
def test_bad_host_variable_values(assert_mdq_error, value):
    with assert_mdq_error("MDQ_CALC_ERROR") as info:
        mi.calc_eval("x = 1", {"n": value})
    assert "variable `n`" in str(info.value)


def test_parenthesised_literals_are_accepted_as_host_values():
    assert text("x = a", {"a": "(5 km)"}) == "5 km"


def test_signed_and_unit_host_values():
    assert text("x = a + b", {"a": "-2 km", "b": "+500 m"}) == "-1.5 km"


def test_the_first_bad_host_variable_by_name_is_reported(assert_mdq_error):
    with assert_mdq_error("MDQ_CALC_ERROR") as info:
        mi.calc_eval("x = 1", {"z": "bad", "b": "also bad"})
    assert "variable `b`" in str(info.value)


def test_variables_argument_type_errors():
    with pytest.raises(TypeError):
        mi.calc_eval("x = 1", {"a": 1})
    with pytest.raises(TypeError):
        mi.calc_eval("x = 1", {1: "1"})
    with pytest.raises(TypeError):
        mi.calc_eval("x = 1", ["a"])
    with pytest.raises(TypeError):
        mi.calc_eval(None)
    with pytest.raises(TypeError):
        mi.calc_eval("x = 1", None, "1")
    with pytest.raises(ValueError):
        mi.calc_eval("x = \ud800")


# ------------------------------------------------- dimension algebra


@pytest.mark.parametrize("a, b, divide, expected", [
    ("L1", "L1", False, "L2"),
    ("L2", "L1", True, "L1"),
    ("L1", "L1", True, "1"),
    ("L1*T-1", "T1", False, "L1"),
    ("M1*L2*T-3", "T1", False, "M1*L2*T-2"),
    ("T-1*L1", "1", False, "L1*T-1"),           # canonical order, "1" is dimensionless
    ("", "L1", False, "L1"),
    ("Th1", "Th1", True, "1"),
    ("dTh1", "dTh1", False, "dTh2"),
    ("A1*I1", "I1", True, "A1"),
])
def test_dim_combine(a, b, divide, expected):
    assert mi.dim_combine(a, b, divide) == expected


@pytest.mark.parametrize("src, expected", [
    ("L1*T-1", "L1*T-1"), ("T-1*L1", "L1*T-1"), ("L1*L1", "L2"), ("L1*L-1", "1"),
    ("", "1"), ("1", "1"),
])
def test_dim_canonical(src, expected):
    assert mi.dim_canonical(src) == expected


@pytest.mark.parametrize("bad", ["X1", "L", "L1**T1", "1L", "L1*", "*L1", "L1.5", "l1", " M1 * L2 ", "L+1"])
def test_bad_dimensions(assert_mdq_error, bad):
    with assert_mdq_error("MDQ_CALC_ERROR"):
        mi.dim_canonical(bad)


def test_dim_hooks_type_errors():
    with pytest.raises(TypeError):
        mi.dim_canonical(None)
    with pytest.raises(TypeError):
        mi.dim_combine("L1", 1, False)


def test_every_catalogue_dimension_round_trips():
    for row in mi.unit_table():
        assert mi.dim_canonical(row["dimension"]) == row["dimension"], row["symbol"]


_DIMS = sorted({row["dimension"] for row in mi.unit_table()})


@given(st.sampled_from(_DIMS), st.sampled_from(_DIMS))
def test_dim_combine_is_invertible(a, b):
    assert mi.dim_combine(mi.dim_combine(a, b, False), b, True) == a
    assert mi.dim_combine(mi.dim_combine(a, b, True), b, False) == a
    assert mi.dim_combine(a, b, False) == mi.dim_combine(b, a, False)


# ------------------------------------------------ differential vs oracle

_amounts = st.one_of(
    numbers,
    st.tuples(st.text("0123456789", min_size=14, max_size=16), st.text("0123456789", min_size=10, max_size=18))
    .map(lambda t: f"{t[0]}.{t[1]}"),
)
_money_codes = st.sampled_from(CURRENCIES[:3])


def _qty(unit):
    return st.builds(lambda n: f"{n} {unit}", _amounts)


_diff = st.builds(lambda a, b: f"({a} degC - {b} degF)", _amounts, _amounts)
_pair = st.sampled_from(same_dimension_pairs() + [(u, u) for u in UNIT_SYMBOLS])


@st.composite
def operand_pairs(draw):
    mode = draw(st.integers(0, 3))
    if mode == 0:  # same-dimension quantities: the interesting arithmetic
        a, b = draw(_pair)
        return draw(_qty(a)), draw(_qty(b))
    atoms = st.one_of(
        _amounts,
        st.builds(lambda n, u: f"{n} {u}", _amounts, st.sampled_from(UNIT_SYMBOLS)),
        st.builds(lambda n, c: f"{n} {c}", _amounts, _money_codes),
        _diff,
    )
    return draw(atoms), draw(atoms)


def outcome_actual(src):
    try:
        r = mi.calc_eval(src)[-1]
    except mdq.MdqError as e:
        return ("error", e.code)
    return (r["type"], r["value"], r["unit"] or r["currency"])


def outcome_oracle(src):
    tree = mi.calc_parse(src)[-1]["expr"]
    try:
        return calc_oracle.run(tree)
    except calc_oracle.CalcError as e:
        return ("error", e.code)
    except Exception as e:  # a Decimal trap (overflow, 0/0, ...) is a calc error
        if type(e).__module__ == "decimal":
            return ("error", "MDQ_CALC_ERROR")
        raise


@given(operand_pairs(), st.sampled_from(["+", "-", "*", "/"]))
def test_binary_operations_match_the_oracle(pair, op):
    a, b = pair
    src = f"x = ({a}) {op} ({b})"
    assert outcome_actual(src) == outcome_oracle(src), src


@given(operand_pairs(), st.sampled_from(UNIT_SYMBOLS + CURRENCIES))
def test_conversions_match_the_oracle(pair, target):
    src = f"x = ({pair[0]}) to {target}"
    assert outcome_actual(src) == outcome_oracle(src), src


@given(st.sampled_from(same_dimension_pairs()), _amounts)
def test_conversion_to_a_same_dimension_unit_matches_the_oracle(pair, n):
    a, b = pair
    src = f"x = {n} {a} to {b}"
    assert outcome_actual(src) == outcome_oracle(src), src


@given(expressions(decimal_only=True))
def test_decimal_expressions_match_the_oracle(case):
    src = f"x = {case[0]}"
    assert outcome_actual(src) == outcome_oracle(src), src


@given(blocks(decimal_only=True))
def test_decimal_blocks_match_the_oracle(case):
    text_, _ = case
    expected, failure = [], None
    for a in mi.calc_parse(text_):
        try:
            expected.append(calc_oracle.run(a["expr"]))
        except calc_oracle.CalcError as e:
            failure = (e.code, a["line"])
            break
        except Exception as e:
            if type(e).__module__ != "decimal":
                raise
            failure = ("MDQ_CALC_ERROR", a["line"])
            break
    if failure:
        with pytest.raises(mdq.MdqError) as info:
            mi.calc_eval(text_)
        assert (info.value.code, info.value.line) == failure
    else:
        got = [(r["type"], r["value"], r["unit"] or r["currency"]) for r in mi.calc_eval(text_)]
        assert got == expected


@given(blocks(decimal_only=True), st.integers(0, 500))
def test_first_line_shifts_only_lines(case, offset):
    text_, _ = case
    try:
        base = mi.calc_eval(text_, None, 1)
    except mdq.MdqError as e:
        with pytest.raises(mdq.MdqError) as info:
            mi.calc_eval(text_, None, 1 + offset)
        assert (info.value.code, info.value.line) == (e.code, e.line + offset)
        return
    moved = mi.calc_eval(text_, None, 1 + offset)
    assert [(r["name"], r["value"]) for r in base] == [(r["name"], r["value"]) for r in moved]
    assert [r["line"] + offset for r in base] == [r["line"] for r in moved]
