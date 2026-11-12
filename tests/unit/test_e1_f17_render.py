"""Epic 1 / E1.7 (F17): rendering calc results (spec 8.6).

Every `calc_eval` result carries a `display` string next to the typed fields
(type / value / unit / currency); `calc_render(result)` produces the same text
from such a dict. Display forms: dimensionless `25.2`; quantity
`19.312128 km`; money `580.8 EUR`. No exponents, no thousands separators, no
`-0`, no trailing zeros. Rendering never rounds. Decisions: G21 (see
docs/TEST_PLAN.md).
"""
import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi
from oracles.lexer_gen import CURRENCIES, numbers
from oracles.units_oracle import SYMBOLS

pytestmark = pytest.mark.unit


def display(src, variables=None):
    return mi.calc_eval(src, variables)[-1]["display"]


# ---------------------------------------------------------------- general


@pytest.mark.parametrize("src, expected", [
    ("x = 10", "10"),
    ("x = 120 * 21%", "25.2"),
    ("x = 12 miles to km", "19.312128 km"),
    ("x = 32 degF to degC", "0 degC"),
    ("x = 480 EUR + 100.8 EUR", "580.8 EUR"),
    ("x = 2 km", "2 km"),
    ("x = 1 / 3", "0.3333333333333333333333333333"),
    ("x = 100 / 8", "12.5"),
    ("x = 1.50 EUR", "1.5 EUR"),
    ("x = 007 kg", "7 kg"),
    ("x = 0.000 m", "0 m"),
    ("x = 2.5 * 2 kg", "5 kg"),
])
def test_display_text(src, expected):
    assert display(src) == expected


def test_spec_block_display():
    src = "price = 120 EUR\nquantity = 4\nsubtotal = price * quantity\ntax = subtotal * 21%\ntotal = subtotal + tax"
    assert [r["display"] for r in mi.calc_eval(src)] == ["120 EUR", "4", "480 EUR", "100.8 EUR", "580.8 EUR"]


def test_typed_fields_are_preserved_next_to_the_display():
    r = mi.calc_eval("x = 12 miles to km")[0]
    assert (r["type"], r["value"], r["unit"], r["currency"], r["display"]) == (
        "quantity", "19.312128", "km", None, "19.312128 km")


def test_no_exponents_and_no_separators():
    assert display("x = 1000000 * 1000000 * 1000000") == "1000000000000000000"
    assert display("x = 1 / 1000000000") == "0.000000001"
    assert display("x = 1234567.5 EUR") == "1234567.5 EUR"
    big = display("x = 99999999999999999999999999999999 + 1")     # beyond 28 digits: rounded once
    assert "e" not in big.lower() and "," not in big and " " not in big and big.startswith("1000000000000000000000000000")


@pytest.mark.parametrize("src", ["x = 0", "x = -0", "x = 0 - 0", "x = 5 - 5", "x = -(0)", "x = 0 * -1", "x = -0.000"])
def test_zero_never_renders_negative(src):
    assert display(src) == "0"


def test_negative_zero_with_a_unit_or_currency():
    assert display("x = -0 km") == "0 km"
    assert display("x = 0 * -1 EUR") == "0 EUR"
    assert display("x = -0 degC") == "0 degC"


def test_derived_and_temperature_units():
    assert display("x = 10 km / 2 h") == "5 km/h"
    assert display("x = 3 m * 4 m") == "12 m2"
    assert display("x = 30 degC - 10 degC") == "20 deltaK"
    assert display("x = -5 degC") == "-5 degC"


def test_display_matches_value_plus_suffix_for_every_result():
    for src in ["x = 1", "x = 2 kg", "x = 3 EUR", "x = 12 miles to km", "x = -1.50 USD", "x = 7 deg to rad"]:
        r = mi.calc_eval(src)[0]
        suffix = r["unit"] or r["currency"]
        assert r["display"] == r["value"] + (" " + suffix if suffix else "")


# ------------------------------------------------------------ calc_render


@pytest.mark.parametrize("result, expected", [
    ({"type": "decimal", "value": "25.2", "unit": None, "currency": None}, "25.2"),
    ({"type": "decimal", "value": "25.200"}, "25.2"),               # unit/currency may be omitted
    ({"type": "decimal", "value": "-0"}, "0"),
    ({"type": "decimal", "value": "05"}, "5"),
    ({"type": "quantity", "value": "19.312128", "unit": "km", "currency": None}, "19.312128 km"),
    ({"type": "quantity", "value": "1.0", "unit": "deltaK"}, "1 deltaK"),
    ({"type": "money", "value": "580.80", "unit": None, "currency": "EUR"}, "580.8 EUR"),
    ({"type": "money", "value": "-0.0", "currency": "USD"}, "0 USD"),
])
def test_calc_render(result, expected):
    assert mi.calc_render(result) == expected


def test_calc_render_accepts_a_full_calc_eval_result():
    for src in ["x = 2 km", "x = 3 EUR", "x = 4", "x = 1 EUR to EUR"]:
        r = mi.calc_eval(src)[0]
        assert mi.calc_render(r) == r["display"]


@pytest.mark.parametrize("result", [
    {"type": "decimal", "value": "1", "unit": "km"},
    {"type": "decimal", "value": "1", "currency": "EUR"},
    {"type": "quantity", "value": "1"},
    {"type": "quantity", "value": "1", "unit": None, "currency": "EUR"},
    {"type": "quantity", "value": "1", "unit": "furlong"},
    {"type": "quantity", "value": "1", "unit": "KM"},
    {"type": "quantity", "value": "1", "unit": "km", "currency": "EUR"},
    {"type": "money", "value": "1"},
    {"type": "money", "value": "1", "currency": "eur"},
    {"type": "money", "value": "1", "currency": "EURO"},
    {"type": "money", "value": "1", "currency": ""},
    {"type": "money", "value": "1", "currency": "EUR", "unit": "km"},
    {"type": "bool", "value": "1"},
    {"type": "", "value": "1"},
    {"type": "decimal", "value": "abc"},
    {"type": "decimal", "value": "1e3"},
    {"type": "decimal", "value": ""},
    {"type": "decimal", "value": "NaN"},
])
def test_calc_render_rejects_invalid_results(assert_mdq_error, result):
    with assert_mdq_error("MDQ_CALC_ERROR"):
        mi.calc_render(result)


@pytest.mark.parametrize("bad", [
    None, "x", 5, [], [("type", "decimal")],
    {"type": "decimal", "value": 1},
    {"type": "decimal", "value": 1.5},
    {"type": "decimal", "value": "1", "unit": 5},
    {"type": 1, "value": "1"},
])
def test_calc_render_type_errors(bad):
    with pytest.raises(TypeError):
        mi.calc_render(bad)


@pytest.mark.parametrize("missing", ["type", "value"])
def test_calc_render_missing_required_keys(missing):
    result = {"type": "decimal", "value": "1"}
    del result[missing]
    with pytest.raises(KeyError):
        mi.calc_render(result)


def test_calc_render_is_idempotent_on_its_own_output_fields():
    r = mi.calc_eval("x = 12 miles to km")[0]
    again = dict(r, value=r["value"] + "000")        # trailing zeros are not significant
    assert mi.calc_render(again) == r["display"]


# ---------------------------------------------------------------- properties


@given(numbers)
def test_a_literal_renders_as_its_canonical_form(literal):
    out = display(f"x = {literal}")
    assert out == mi.dec_canonical(literal)
    assert "e" not in out.lower() and "," not in out and not out.endswith(".") and not out.startswith("-0")
    if "." in out:
        assert not out.endswith("0")


@given(numbers, st.sampled_from(SYMBOLS), st.sampled_from(CURRENCIES))
def test_suffix_is_exactly_the_unit_or_currency(literal, unit, currency):
    u = display(f"x = {literal} {unit}") if unit != "deltaK" else None
    if u is not None:
        assert u == f"{mi.dec_canonical(literal)} {unit}"
    assert display(f"x = {literal} {currency}") == f"{mi.dec_canonical(literal)} {currency}"


@given(numbers, st.sampled_from(["decimal", "quantity", "money"]))
def test_calc_render_agrees_with_eval(literal, kind):
    src = {"decimal": f"x = {literal}", "quantity": f"x = {literal} kg", "money": f"x = {literal} EUR"}[kind]
    r = mi.calc_eval(src)[0]
    assert mi.calc_render(r) == r["display"]
