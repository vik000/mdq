"""Epic 1 / E1.2 (F14): unit catalogue, token matching, exact conversion.

Data is cross-checked three ways: the Rust-parsed table vs an independent
derivation (oracles/units_oracle.py), vs the Markdown table, and the spec's
conformance vectors (calculation-units.md section 9) vs the Rust output.
"""
import re
from fractions import Fraction as F
from decimal import Context, Decimal
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi
from oracles import decimal_oracle
from oracles import units_oracle as oracle

SPEC = Path(__file__).resolve().parents[2] / "spec"
MD = (SPEC / "calculation-units.md").read_text(encoding="utf-8")

REQUIRED_SYMBOLS = (  # calculation-units.md, original draft list
    "m km cm mm in ft yd miles kg g mg lb oz m2 km2 ft2 acre ha L mL m3 gal_us qt_us "
    "s min h day degC degF K m/s km/h mph J kJ cal kWh W kW hp Pa kPa bar psi rad deg "
    "bit B kB MB GB KiB MiB GiB"
).split()
REQUIRED_CATEGORIES = {
    "length", "mass", "area", "volume", "time", "temperature", "speed",
    "energy", "power", "pressure", "angle", "information",
}
TABLE = mi.unit_table()
BY_SYMBOL = {row["symbol"]: row for row in TABLE}


def md_section(start: str, end: str) -> str:
    return MD[MD.index(start):MD.index(end)]


def md_vectors():
    rows = re.findall(
        r"^\| `(-?[0-9.]+)` \| `([^`]+)` \| `([^`]+)` \| `([^`]+)` \|$",
        md_section("## 9.", "## 10."), flags=re.M,
    )
    assert len(rows) >= 40
    return rows


def md_table_rows():
    rx = re.compile(
        r"^\| `([^`]+)` \| (.*?) \| `([^`]*)` \| `([^`]*)` \| `([^`]*)` \| `([^`]*)` \| (\S+) \| (.*) \|$",
        re.M,
    )
    return rx.findall(md_section("## 4.", "## 5."))


class TestCatalogueData:
    @pytest.mark.spec("calculation-units §4")
    def test_table_size_and_uniqueness(self):
        symbols = [r["symbol"] for r in TABLE]
        assert len(symbols) == len(set(symbols)) == len(oracle.UNITS) == 55

    @pytest.mark.spec("calculation-units §1")
    def test_required_symbols_present(self):
        missing = [s for s in REQUIRED_SYMBOLS if s not in BY_SYMBOL]
        assert not missing
        assert "m3" in BY_SYMBOL and "deltaK" in BY_SYMBOL

    def test_required_categories_present_and_dimension_consistent(self):
        cats = {}
        for r in TABLE:
            cats.setdefault(r["category"], set()).add(r["dimension"].replace("dTh1", "Th1"))
        assert set(cats) == REQUIRED_CATEGORIES
        for cat, dims in cats.items():
            assert len(dims) == 1, (cat, dims)

    def test_row_shape(self):
        for r in TABLE:
            assert set(r) == {"symbol", "name", "category", "dimension", "scale",
                              "offset", "base_unit", "kind"}
            assert r["kind"] in {"linear", "absolute_temperature", "difference"}
            assert all(isinstance(v, str) and v for v in r.values())

    def test_symbols_unique_ignoring_case(self):
        lowered = [r["symbol"].lower() for r in TABLE]
        assert len(lowered) == len(set(lowered))

    @pytest.mark.parametrize("symbol", oracle.SYMBOLS)
    def test_row_matches_independent_derivation(self, symbol):
        dim, scale, offset = oracle.UNITS[symbol]
        row = BY_SYMBOL[symbol]
        assert row["dimension"] == dim
        assert oracle.parse_exact(row["scale"]) == scale, row["scale"]
        assert oracle.parse_exact(row["offset"]) == offset, row["offset"]

    def test_base_units_are_coherent(self):
        for r in TABLE:
            base = r["base_unit"]
            assert base in BY_SYMBOL, base
            b = BY_SYMBOL[base]
            assert (b["scale"], b["offset"]) == ("1", "0"), base
            assert b["dimension"] == r["dimension"]

    def test_kinds(self):
        assert {r["symbol"] for r in TABLE if r["kind"] == "absolute_temperature"} == {"K", "degC", "degF"}
        assert {r["symbol"] for r in TABLE if r["kind"] == "difference"} == {"deltaK"}
        assert all(r["offset"] == "0" for r in TABLE if r["kind"] == "linear")

    @pytest.mark.spec("calculation-units §7")
    @pytest.mark.parametrize(
        "value,a,b,expected",
        [("1", "in", "m", "0.0254"), ("1", "lb", "kg", "0.45359237"),
         ("1", "miles", "m", "1609.344"), ("1", "gal_us", "m3", "0.003785411784"),
         ("1", "KiB", "B", "1024"), ("1", "kB", "B", "1000"),
         ("0", "degC", "K", "273.15"), ("32", "degF", "degC", "0"),
         ("212", "degF", "degC", "100")],
    )
    def test_spec_reference_relations(self, value, a, b, expected):
        assert mi.unit_convert(value, a, b) == expected

    def test_markdown_table_in_sync_with_tsv(self):
        rows = md_table_rows()
        assert len(rows) == len(TABLE)
        for md_row, row in zip(rows, TABLE):
            symbol, _name, dim, scale, offset, base, kind, _defn = md_row
            assert (symbol, dim, scale, offset, base, kind) == (
                row["symbol"], row["dimension"], row["scale"], row["offset"],
                row["base_unit"], row["kind"],
            )

    def test_catalogue_version_exposed(self):
        assert mdq.version_info()["unit_catalogue"] == "0.1.0-draft"


class TestLookup:
    @pytest.mark.parametrize("symbol", oracle.SYMBOLS)
    def test_lookup_returns_table_row(self, symbol):
        assert mi.unit_lookup(symbol) == BY_SYMBOL[symbol]

    @pytest.mark.spec("calculation-units §3")
    @pytest.mark.parametrize(
        "symbol",
        ["C", "F", "mile", "Miles", "MILES", "sec", "hr", "metre", "l", "KB", "mb", "Kg",
         "KM", "Km", "kw", "GIB", "kib", "EUR", "", " ", " m", "m ", "m\n", "m2 ", "mph2",
         "µm", "km/hr"],
    )
    def test_unknown_or_miscased_symbols_rejected(self, symbol, assert_mdq_error):
        with assert_mdq_error("MDQ_CALC_ERROR") as info:
            mi.unit_lookup(symbol)
        assert "unknown unit" in str(info.value)

    def test_non_str_is_type_error(self):
        for bad in (None, 1, b"m"):
            with pytest.raises(TypeError):
                mi.unit_lookup(bad)


class TestMatchAt:
    @pytest.mark.spec("calculation-units §3")
    @pytest.mark.parametrize(
        "text,pos,expected",
        [
            ("12 mph", 3, "mph"), ("12 m", 3, "m"), ("12 min", 3, "min"),
            ("12 mins", 3, None),            # `min` is followed by `s`
            ("12 km/h", 3, "km/h"), ("12 m/s", 3, "m/s"), ("12 km/s", 3, "km"),
            ("12 km/hour", 3, "km"),         # `km/h` is followed by `o`: fall back to `km`
            ("12 m/2", 3, "m"), ("12 m/sec", 3, "m"),
            ("12 miles", 3, "miles"), ("12 mile", 3, None),
            ("12 GB", 3, "GB"), ("12 GBP", 3, None),   # a currency, not `GB` + `P`
            ("12 KiB", 3, "KiB"), ("12 kB", 3, "kB"), ("12 KB", 3, None),
            ("5 degC", 2, "degC"), ("5 degCx", 2, None), ("5 C", 2, None),
            ("x = 3 h + 1", 6, "h"),
            ("12 m2", 3, "m2"), ("12 m3", 3, "m3"), ("12 m)", 3, "m"),
            ("12 m_x", 3, None), ("12 m9", 3, None),
            ("12 s", 3, "s"), ("12 ", 3, None), ("12", 5, None), ("", 0, None),
            ("m", 0, "m"), ("m/", 0, "m"), ("km/", 0, "km"),
            ("12 mph", 4, None),             # mid-token position: no symbol starts there
            ("12 deltaK", 3, None),          # result-only unit is never a source token
            ("é m", 2, "m"),            # position counts characters, not bytes
            ("12 mé", 3, "m"),          # identifiers are ASCII-only
        ],
    )
    def test_match(self, text, pos, expected):
        assert mi.unit_match_at(text, pos) == expected

    def test_every_source_symbol_matches_alone_and_before_delimiters(self):
        for r in TABLE:
            s = r["symbol"]
            if r["kind"] == "difference":
                continue
            assert mi.unit_match_at(s, 0) == s
            for c in " \t\n+-*%)#":
                assert mi.unit_match_at(f"1 {s}{c}", 2) == s, (s, c)

    def test_identifier_characters_block_a_match(self):
        for r in TABLE:
            s = r["symbol"]
            for c in "xX_9":
                assert mi.unit_match_at(s + c, 0) != s, (s, c)

    def test_pos_type_errors(self):
        with pytest.raises(OverflowError):
            mi.unit_match_at("12 m", -1)
        with pytest.raises(TypeError):
            mi.unit_match_at("12 m", "3")
        with pytest.raises(TypeError):
            mi.unit_match_at(None, 0)


class TestConvertVectors:
    @pytest.mark.spec("calculation-units §9")
    @pytest.mark.parametrize("value,a,b,expected", md_vectors())
    def test_spec_document_vectors(self, value, a, b, expected):
        assert mi.unit_convert(value, a, b) == expected
        # and the vector itself is what the independent oracle computes
        assert oracle.convert(value, a, b) == expected

    @pytest.mark.spec("extended-markdown §8.9")
    def test_main_spec_vectors(self):
        assert mi.unit_convert("12", "miles", "km") == "19.312128"
        assert mi.unit_convert("32", "degF", "degC") == "0"

    @pytest.mark.spec("calculation-units §2")
    def test_single_rounding_not_per_step(self):
        # Rounding after `* 5/9` would give 27 digits here; one rounding gives 28.
        assert mi.unit_convert("72", "degF", "degC") == "22.22222222222222222222222222"
        assert mi.unit_convert("1", "km/h", "m/s") == "0.2777777777777777777777777778"
        assert mi.unit_convert("1", "psi", "Pa") == "6894.757293168361336722673445"
        assert mi.unit_convert("180", "deg", "rad") == "3.141592653589793238462643383"


class TestConvertEdge:
    @pytest.mark.parametrize("a,b", [("m", "m"), ("degC", "degC"), ("deltaK", "deltaK"), ("psi", "psi")])
    def test_identity_is_canonical_not_rounded(self, a, b):
        assert mi.unit_convert("1.500", a, b) == "1.5"
        assert mi.unit_convert("-0", a, b) == "0"
        forty = "1234567890123456789012345678901234567890"
        assert mi.unit_convert(forty, a, b) == forty  # never rounded

    @pytest.mark.parametrize("a,b", [("km", "miles"), ("degC", "degF"), ("GiB", "GB"), ("hp", "kW")])
    def test_zero_and_negative_values(self, a, b):
        assert mi.unit_convert("0", a, b) == oracle.convert("0", a, b)
        assert mi.unit_convert("-5", a, b) == oracle.convert("-5", a, b)

    def test_absolute_zero_and_negative_kelvin_are_not_policed_here(self):
        # Range/physics checks are not the catalogue's job; arithmetic is exact.
        assert mi.unit_convert("0", "K", "degC") == "-273.15"
        assert mi.unit_convert("-1", "K", "degC") == "-274.15"

    def test_large_and_tiny_magnitudes(self):
        assert mi.unit_convert("1" + "0" * 20, "km", "m") == "1" + "0" * 23
        assert mi.unit_convert("0." + "0" * 20 + "1", "km", "mm") == oracle.convert("0." + "0" * 20 + "1", "km", "mm")

    def test_input_literal_is_exact_before_conversion(self):
        forty = "1234567890123456789012345678901234567890"
        assert mi.unit_convert(forty, "m", "km") == oracle.convert(forty, "m", "km")


class TestConvertNegative:
    @pytest.mark.parametrize(
        "a,b",
        [("kg", "m"), ("degC", "deltaK"), ("rad", "m"), ("B", "kg"), ("m2", "m"),
         ("m/s", "km"), ("bit", "rad"), ("K", "s"), ("J", "W"), ("Pa", "bar_x")],
    )
    def test_incompatible_or_unknown(self, a, b, assert_mdq_error):
        with assert_mdq_error("MDQ_CALC_ERROR"):
            mi.unit_convert("1", a, b)

    def test_incompatible_message_names_both_units(self, assert_mdq_error):
        with assert_mdq_error("MDQ_CALC_ERROR") as info:
            mi.unit_convert("1", "kg", "m")
        msg = str(info.value)
        assert "kg" in msg and "m" in msg and "dimension" in msg

    @pytest.mark.parametrize("bad", ["", "1e3", "NaN", " 1", "1,5", "1."])
    def test_bad_value_literal(self, bad, assert_mdq_error):
        with assert_mdq_error("MDQ_CALC_ERROR"):
            mi.unit_convert(bad, "m", "km")

    def test_unknown_unit_either_side(self, assert_mdq_error):
        with assert_mdq_error("MDQ_CALC_ERROR"):
            mi.unit_convert("1", "mile", "km")
        with assert_mdq_error("MDQ_CALC_ERROR"):
            mi.unit_convert("1", "km", "mile")

    def test_type_errors(self):
        for args in [(1, "m", "km"), ("1", None, "km"), ("1", "m", 3)]:
            with pytest.raises(TypeError):
                mi.unit_convert(*args)


VALUES = [
    "0", "1", "-1", "0.1", "-0.5", "123456.789", "-273.15", "32", "98.6", "-40",
    "0.000001", "1" + "0" * 20, "0." + "0" * 20 + "1", "1234567890123456789012345678901234567890",
]


@pytest.mark.differential
class TestAgainstIndependentOracle:
    @pytest.mark.parametrize("a,b", oracle.same_dimension_pairs())
    def test_every_pair_on_fixed_values(self, a, b):
        for v in VALUES:
            assert mi.unit_convert(v, a, b) == oracle.convert(v, a, b), (v, a, b)

    @given(st.sampled_from(oracle.same_dimension_pairs()), decimal_oracle.literals())
    def test_random_values(self, pair, x):
        a, b = pair
        assert mi.unit_convert(x, a, b) == oracle.convert(x, a, b)


@pytest.mark.property
class TestProperties:
    @given(st.sampled_from(oracle.same_dimension_pairs()), decimal_oracle.literals())
    def test_round_trip_within_rounding_error(self, pair, x):
        a, b = pair
        y = mi.unit_convert(x, a, b)
        back = mi.unit_convert(y, b, a)
        X, Y, B = (F(Decimal(t)) for t in (x, y, back))
        tolerance = F(1, 10**26) * max(abs(X), abs(Y), F(1))
        assert abs(B - X) <= tolerance, (x, y, back)

    @given(decimal_oracle.literals())
    def test_conversion_is_monotonic_for_positive_scale_units(self, x):
        # All catalogue scales are positive, so x < x + 1 must never reverse.
        # (x + 1 is built EXACTLY: mi.dec_add would round it to 28 digits.)
        exact = Context(prec=500)
        bigger = format(exact.add(Decimal(x), Decimal(1)), "f")
        low = F(Decimal(mi.unit_convert(x, "m", "ft")))
        high = F(Decimal(mi.unit_convert(bigger, "m", "ft")))
        assert high >= low

    def test_zero_maps_to_known_offsets(self):
        assert mi.unit_convert("0", "degC", "K") == "273.15"
        assert mi.unit_convert("0", "m", "km") == "0"
