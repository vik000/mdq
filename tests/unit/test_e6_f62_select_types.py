"""Epic 6 / E6.2 (F16): JSON Pointer selection and type checks of source values (sources-and-permissions.md
sections 2 and 3). G43.

`json_pointer_select(text, pointer)` parses strict JSON and follows an RFC 6901 pointer (numbers come back as
`decimal.Decimal`). `value_type_check(value, type, unit=None, from_text=False)` checks a selected value (or a
text body) against a declared type and returns `{"type", "value", "unit"}`.
"""
import json
from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

sel = mi.json_pointer_select
chk = mi.value_type_check
INVALID, UNRES, TYPE = "MDQ_SOURCE_INVALID", "MDQ_SOURCE_UNRESOLVED", "MDQ_SOURCE_TYPE_ERROR"

RFC = ('{"foo":["bar","baz"],"":0,"a/b":1,"c%d":2,"e^f":3,"g|h":4,"i\\\\j":5,"k\\"l":6," ":7,"m~n":8}')


@pytest.mark.parametrize("pointer,expected", [
    ("", {"foo": ["bar", "baz"], "": 0, "a/b": 1, "c%d": 2, "e^f": 3, "g|h": 4, "i\\j": 5, 'k"l': 6, " ": 7, "m~n": 8}),
    ("/foo", ["bar", "baz"]), ("/foo/0", "bar"), ("/foo/1", "baz"), ("/", 0), ("/a~1b", 1), ("/c%d", 2), ("/e^f", 3),
    ("/g|h", 4), ("/i\\j", 5), ('/k"l', 6), ("/ ", 7), ("/m~0n", 8),
])
def test_rfc6901_section5_vectors(pointer, expected):
    assert _norm(sel(RFC, pointer)) == expected


def test_numbers_are_decimals_and_exact():
    assert sel('{"p": 1.50}', "/p") == Decimal("1.50") and str(sel('{"p": 1.50}', "/p")) == "1.50"
    assert isinstance(sel('{"p": 3}', "/p"), Decimal)
    assert sel('{"p": 12345678901234567890123456789.1234567890123456789}', "/p") == Decimal("12345678901234567890123456789.1234567890123456789")
    assert sel('{"p": [1, 2.5, {"q": 1e2}]}', "/p") == [Decimal(1), Decimal("2.5"), {"q": Decimal("1e2")}]


def test_scalars_null_and_containers():
    assert sel('{"a": null}', "/a") is None
    assert sel('{"a": true}', "/a") is True and sel('{"a": false}', "/a") is False
    assert sel('{"a": "é😀"}', "/a") == "é😀"
    assert sel("[1]", "") == [Decimal(1)] and sel('"x"', "") == "x" and sel("7", "") == Decimal(7)
    assert sel('{"a": {}}', "/a") == {} and sel('{"a": []}', "/a") == []


def test_escape_order_tilde_one_is_not_a_slash():
    assert sel('{"~1": 1, "/": 2}', "/~01") == Decimal(1)
    assert sel('{"~1": 1, "/": 2}', "/~1") == Decimal(2)
    assert sel('{"~0": 3}', "/~00") == Decimal(3)


def test_unicode_keys_are_exact():
    assert sel('{"é": 1, "e\\u0301": 2}', "/é") == Decimal(1)
    assert sel('{"é": 1, "e\\u0301": 2}', "/é") == Decimal(2)
    assert sel('{"a\\u0062": 1}', "/ab") == Decimal(1)


@pytest.mark.parametrize("doc,pointer", [
    ('{"a": 1}', "/b"), ('{"a": 1}', "/A"), ('{"a": {"b": 1}}', "/a/c"), ('{"a": 1}', "/a/b"), ('{"a": "s"}', "/a/0"),
    ('{"a": null}', "/a/b"), ('{"a": true}', "/a/b"), ("[1,2]", "/2"), ("[1,2]", "/-"), ("[1,2]", "/01"), ("[1,2]", "/00"),
    ("[1,2]", "/+1"), ("[1,2]", "/-1"), ("[1,2]", "/1.0"), ("[1,2]", "/ 1"), ("[1,2]", "/1 "), ("[1,2]", "/a"), ("[1,2]", "/"),
    ("[1,2]", "/١"), ("[1,2]", "/99999999999999999999999999"), ("[]", "/0"), ('{"a": [1]}', "/a/1"), ("5", "/a"), ('{"": 1}', "//"),
    ('{"a": 1}', "/a/"),
])
def test_selection_failures_are_unresolved(doc, pointer, assert_mdq_error):
    with assert_mdq_error(UNRES):
        sel(doc, pointer)


def test_leading_zero_index_is_unresolved_even_when_it_looks_valid():
    assert sel("[10, 20]", "/0") == Decimal(10)
    with pytest.raises(mdq.MdqError):
        sel("[10, 20]", "/00")


@pytest.mark.parametrize("pointer", ["a", "a/b", " /a", "~0", "/~", "/~2", "/a~", "/a~x", "/~01~", "#/a", "$.a", "$", ".a", "/a\u0000~"])
def test_invalid_pointer_syntax(pointer, assert_mdq_error):
    with assert_mdq_error(INVALID):
        sel('{"a": 1}', pointer)


@pytest.mark.parametrize("doc", [
    "", " ", "{", '{"a": 1,}', "[1,]", "{'a': 1}", '{"a": 1} x', '{"a": 1}{"b": 2}', "NaN", "Infinity", '{"a": NaN}', '{"a": 01}', '{"a": .5}',
    '{"a": 1.}', '{"a": +1}', '{"a": "\n"}', '{"a": 1, "a": 2}', '{"a": "\\ud800"}', '{"a": "\\x41"}', "// c\n{}", "{} // c",
])
def test_invalid_json_documents(doc, assert_mdq_error):
    with assert_mdq_error(INVALID):
        sel(doc, "")


def test_depth_limit_and_bom(assert_mdq_error):
    assert sel("[" * 65 + "]" * 65, "") is not None
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        sel("[" * 66 + "]" * 66, "")
    assert sel("﻿[1]", "/0") == Decimal(1)


def test_input_limit(assert_mdq_error):
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        sel("[" + " " * (16 * 1024 * 1024) + "]", "")


def test_select_argument_errors():
    for a in ((1, ""), ("{}", 1), ("{}", None), (None, "")):
        with pytest.raises(TypeError):
            sel(*a)


def test_error_text_never_contains_values(assert_mdq_error):
    with assert_mdq_error(UNRES) as info:
        sel('{"token": "S3CRET-VALUE"}', "/token/x")
    assert "S3CRET" not in str(info.value)
    with assert_mdq_error(TYPE) as info:
        chk("S3CRET-VALUE", "number")
    assert "S3CRET" not in str(info.value)
    with assert_mdq_error(TYPE) as info:
        chk("S3CRET-VALUE", "date")
    assert "S3CRET" not in str(info.value)


# ---------------------------------------------------------------- type checks
def test_number():
    assert chk(Decimal("1.50"), "number", "USD") == {"type": "number", "value": Decimal("1.5"), "unit": "USD"}
    assert chk(Decimal("1E+2"), "number")["value"] == Decimal(100) and str(chk(Decimal("1E+2"), "number")["value"]) == "100"
    assert str(chk(Decimal("-0"), "number")["value"]) == "0" and str(chk(Decimal("0E-10"), "number")["value"]) == "0"
    assert str(chk(Decimal("12345678901234567890123456789.1234567890123456789"), "number")["value"]) == "12345678901234567890123456789.1234567890123456789"
    assert format(chk(Decimal("1E-9998"), "number")["value"], "f").startswith("0.") and chk(5, "number")["value"] == Decimal(5)
    assert chk(Decimal(3), "number", None)["unit"] is None


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), Decimal("1E+10000"), Decimal("1E-10000")])
def test_number_out_of_range(value, assert_mdq_error):
    with assert_mdq_error(TYPE):
        chk(value, "number")


@pytest.mark.parametrize("value,ty", [
    ("1", "number"), ("1.5", "number"), (True, "number"), (False, "number"), (None, "number"), ([], "number"), ({}, "number"),
    (Decimal(1), "string"), (True, "string"), (None, "string"), ([], "string"), ({}, "string"),
    ("true", "boolean"), (1, "boolean"), (Decimal(0), "boolean"), (None, "boolean"), ([], "boolean"),
    (Decimal(20240101), "date"), (True, "date"), (None, "date"), ({}, "date"),
])
def test_mismatches_are_not_coerced(value, ty, assert_mdq_error):
    with assert_mdq_error(TYPE):
        chk(value, ty)


def test_string_and_boolean():
    assert chk("", "string")["value"] == "" and chk("é😀 x", "string")["value"] == "é😀 x"
    assert chk(True, "boolean")["value"] is True and chk(False, "boolean")["value"] is False


@pytest.mark.parametrize("s", ["2024-02-29", "2000-02-29", "0001-01-01", "9999-12-31", "2023-04-30", "2024-12-01"])
def test_valid_dates(s):
    assert chk(s, "date") == {"type": "date", "value": s, "unit": None}


@pytest.mark.parametrize("s", [
    "2023-02-29", "1900-02-29", "2024-13-01", "2024-00-10", "2024-01-00", "2024-04-31", "0000-01-01", "24-01-01", "2024-1-1", "2024/01/01",
    "20240101", "2024-01-01T00:00:00Z", "2024-01-01 ", " 2024-01-01", "2024-01-01\n", "", "٢٠٢٤-٠١-٠١", "2024-01-011", "+2024-01-01",
])
def test_invalid_dates(s, assert_mdq_error):
    with assert_mdq_error(TYPE):
        chk(s, "date")


def test_unit_rules(assert_mdq_error):
    for ty in ("string", "boolean", "date"):
        with assert_mdq_error(INVALID):
            chk("x" if ty != "boolean" else True, ty, "USD")
    for u in ("", "  "):
        with assert_mdq_error(INVALID):
            chk(Decimal(1), "number", u)


def test_check_argument_errors():
    with pytest.raises(ValueError):
        chk("x", "text")
    with pytest.raises(ValueError):
        chk("x", "Number")
    for bad in (1.5, b"x", object(), (1,), {1}):
        with pytest.raises(TypeError):
            chk(bad, "number")
    with pytest.raises(TypeError):
        chk(Decimal(1), "number", 5)
    with pytest.raises(TypeError):
        chk(Decimal(1), "number", None, "yes")
    with pytest.raises(TypeError):
        chk("1", "number", None, True) if False else chk(Decimal(1), "number", None, True)


# ---------------------------------------------------------------- text bodies
@pytest.mark.parametrize("body,expected", [
    ("12.5", "12.5"), (" 12.50\n", "12.5"), ("-3", "-3"), ("0", "0"), ("1e3", "1000"), ("1.5E-3", "0.0015"), ("-0.0", "0"), ("\t7\r\n", "7"),
    ("0.1", "0.1"), ("10", "10"), ("12345678901234567890123456789", "12345678901234567890123456789"),
])
def test_text_numbers(body, expected):
    assert str(chk(body, "number", "EUR", True)["value"]) == expected


@pytest.mark.parametrize("body", ["", " ", "abc", "1,5", "1 2", ".5", "5.", "+5", "01", "1e", "e3", "0x10", "NaN", "inf", "١٢", "1_000", "--1", "1e99999999"])
def test_invalid_text_numbers(body, assert_mdq_error):
    with assert_mdq_error(TYPE):
        chk(body, "number", None, True)


def test_text_booleans_dates_and_strings():
    assert chk(" true\n", "boolean", None, True)["value"] is True and chk("false", "boolean", None, True)["value"] is False
    for b in ("True", "TRUE", "yes", "1", "", "tru"):
        with pytest.raises(mdq.MdqError):
            chk(b, "boolean", None, True)
    assert chk("2024-02-29\n", "date", None, True)["value"] == "2024-02-29"
    with pytest.raises(mdq.MdqError):
        chk("2023-02-29", "date", None, True)
    assert chk("hello\n", "string", None, True)["value"] == "hello"
    assert chk("hello\r\n", "string", None, True)["value"] == "hello"
    assert chk("hello\n\n", "string", None, True)["value"] == "hello\n"
    assert chk("  hi  ", "string", None, True)["value"] == "  hi  " and chk("", "string", None, True)["value"] == ""


def test_text_mode_needs_a_string():
    for v in (Decimal(1), True, None, []):
        with pytest.raises(TypeError):
            chk(v, "number", None, True)


# ---------------------------------------------------------------- properties
_json = st.recursive(
    st.none() | st.booleans() | st.integers(-10**20, 10**20) | st.text(max_size=8),
    lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=5), c, max_size=4), max_leaves=12)


def _paths(v, prefix=""):
    yield prefix, v
    if isinstance(v, dict):
        for k, x in v.items():
            yield from _paths(x, prefix + "/" + k.replace("~", "~0").replace("/", "~1"))
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from _paths(x, prefix + "/" + str(i))


def _norm(v):
    if isinstance(v, Decimal): return int(v)
    if isinstance(v, list): return [_norm(x) for x in v]
    if isinstance(v, dict): return {k: _norm(x) for k, x in v.items()}
    return v


@given(_json)
def test_every_path_of_a_document_selects_its_value(doc):
    text = json.dumps(doc)
    for pointer, expected in _paths(doc):
        assert _norm(sel(text, pointer)) == expected


@given(st.decimals(allow_nan=False, allow_infinity=False, min_value=Decimal("-1E+30"), max_value=Decimal("1E+30")))
def test_numbers_round_trip_exactly(d):
    out = chk(d, "number")["value"]
    assert out == d
    assert chk(str(out), "number", None, True)["value"] == out


@given(st.integers(1, 9999), st.integers(1, 12), st.integers(1, 31))
def test_dates_agree_with_datetime(y, m, d):
    import datetime
    s = f"{y:04d}-{m:02d}-{d:02d}"
    try:
        datetime.date(y, m, d); ok = True
    except ValueError:
        ok = False
    if ok:
        assert chk(s, "date")["value"] == s
    else:
        with pytest.raises(mdq.MdqError):
            chk(s, "date")


@given(st.text(max_size=30))
def test_arbitrary_pointers_and_text_never_crash(s):
    for f in (lambda: sel('{"a": [1, {"b": 2}], "": 3}', s), lambda: chk(s, "number", None, True), lambda: chk(s, "date", None, True),
              lambda: chk(s, "boolean", None, True)):
        try:
            f()
        except mdq.MdqError as e:
            assert e.code in (INVALID, UNRES, TYPE)
