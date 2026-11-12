"""Epic 1 / E1.1 (F13): exact decimal arithmetic.

Policy under test (spec gap G4 marks the range limits as provisional):
28 significant digits, round-half-even applied to every RESULT; literals are
exact; exponent of leading digit within +-9999; literals at most 10,000 bytes.
"""
import decimal

import pytest
from hypothesis import assume, given, strategies as st

import mdq
from mdq import _internal as mi
from oracles import decimal_oracle as oracle

BINARY = {
    "add": mi.dec_add,
    "sub": mi.dec_sub,
    "mul": mi.dec_mul,
    "div": mi.dec_div,
}

BAD_LITERALS = [
    "", " ", "+", "-", ".", "+.", "1.", "-1.", "1..2", "1.2.3", "..5",
    "1e3", "1E3", "1e-3", "NaN", "nan", "Infinity", "inf", "-inf",
    "1,000", "1_000", " 1", "1 ", "\t1", "1\n", "0x10", "--1", "+-1", "1-",
    "12abc", "٣", "١٢٣", "1٫5", " 1", "１２",
]


class TestGeneral:
    def test_precision_is_28(self):
        assert mi.dec_precision() == 28

    @pytest.mark.spec("extended-markdown §8.9")
    @pytest.mark.parametrize(
        "op,a,b,expected",
        [
            ("add", "2", "12", "14"),
            ("mul", "3", "4", "12"),
            ("mul", "120", "0.21", "25.2"),  # `120 * 21%` once 21% = 0.21
            ("add", "0.1", "0.2", "0.3"),  # exact, unlike binary floats
            ("sub", "0.3", "0.1", "0.2"),
            ("sub", "1", "1", "0"),
            ("sub", "5", "8", "-3"),
            ("add", "-1.5", "1.5", "0"),
            ("mul", "-2", "3", "-6"),
            ("mul", "-2", "-3", "6"),
            ("mul", "0.5", "0.5", "0.25"),
            ("div", "1", "4", "0.25"),
            ("div", "10", "4", "2.5"),
            ("div", "10", "2", "5"),
            ("div", "-7", "2", "-3.5"),
            ("div", "1", "3", "0." + "3" * 28),
            ("div", "2", "3", "0." + "6" * 27 + "7"),
            ("div", "1234567.1", "3", "411522.3666666666666666666667"),
            ("add", "100", "0.001", "100.001"),
        ],
    )
    def test_vectors(self, op, a, b, expected):
        assert BINARY[op](a, b) == expected

    @pytest.mark.spec("extended-markdown §8.9")
    def test_spec_expression_chains(self):
        assert mi.dec_add("2", mi.dec_mul("3", "4")) == "14"  # 2 + 3 * 4
        assert mi.dec_mul(mi.dec_add("2", "3"), "4") == "20"  # (2 + 3) * 4
        pct = mi.dec_div("21", "100")  # 21%
        assert pct == "0.21"
        assert mi.dec_mul("120", pct) == "25.2"  # 120 * 21%

    @pytest.mark.parametrize(
        "value,expected",
        [("5", "-5"), ("-5", "5"), ("0", "0"), ("-0", "0"), ("1.50", "-1.5")],
    )
    def test_neg(self, value, expected):
        assert mi.dec_neg(value) == expected

    def test_results_are_str(self):
        assert isinstance(mi.dec_add("1", "2"), str)


class TestCanonicalForm:
    @pytest.mark.spec("extended-markdown §8.6")
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1.500", "1.5"), ("0.0", "0"), ("-0", "0"), ("-0.000", "0"),
            ("+5", "5"), ("007", "7"), (".5", "0.5"), ("0.50", "0.5"),
            ("100", "100"), ("1000.0", "1000"), ("-1.50", "-1.5"),
            ("25.20", "25.2"), ("0.001", "0.001"), ("10.010", "10.01"),
        ],
    )
    def test_canonical(self, text, expected):
        assert mi.dec_canonical(text) == expected

    def test_negative_zero_results_print_zero(self):
        assert mi.dec_mul("-0", "5") == "0"
        assert mi.dec_mul("-1", "0") == "0"
        assert mi.dec_neg("0") == "0"

    def test_literals_are_exact_not_rounded(self):
        long_int = "1234567890123456789012345678901234567890"  # 40 digits
        assert mi.dec_canonical(long_int) == long_int
        tiny = "0." + "0" * 28 + "1"  # 29 fractional digits
        assert mi.dec_canonical(tiny) == tiny

    def test_no_exponent_notation_ever(self):
        assert mi.dec_mul("1" + "0" * 20, "1" + "0" * 20) == "1" + "0" * 40
        assert mi.dec_div("1", "1" + "0" * 30) == "0." + "0" * 29 + "1"  # 10^-30


class TestRounding:
    @pytest.mark.spec("extended-markdown §8.7")
    @pytest.mark.parametrize(
        "value,prec,expected",
        [
            ("1.2345", 4, "1.234"),   # tie, kept digit even: stay
            ("1.2355", 4, "1.236"),   # tie, kept digit odd: up
            ("1.2346", 4, "1.235"),
            ("1.2344", 4, "1.234"),
            ("-1.2345", 4, "-1.234"),  # symmetric for negatives
            ("-1.2355", 4, "-1.236"),
            ("9.9995", 4, "10"),       # carry into a new digit
            ("99999", 1, "100000"),
            ("15", 1, "20"), ("25", 1, "20"), ("35", 1, "40"),
            ("0", 5, "0"),
            ("0.5", 1, "0.5"),         # fits: untouched
            ("123", 5, "123"), ("123", 3, "123"),
            ("0.000123456", 3, "0.000123"),
            ("0.000123500", 3, "0.000124"),
            ("0.000122500", 3, "0.000122"),
        ],
    )
    def test_round_sig_half_even(self, value, prec, expected):
        assert mi.dec_round_sig(value, prec) == expected

    def test_28_digits_pass_29_digits_round(self):
        d28 = "1234567890123456789012345678"
        d29 = "12345678901234567890123456789"
        assert mi.dec_add(d28, "0") == d28
        assert mi.dec_add(d29, "0") == "12345678901234567890123456790"

    def test_tie_at_digit_28_goes_to_even(self):
        even_tie = "1." + "0" * 26 + "05"  # kept ...0 (even), dropped 5
        odd_tie = "1." + "0" * 26 + "15"   # kept ...1 (odd), dropped 5
        assert mi.dec_add(even_tie, "0") == "1"
        assert mi.dec_add(odd_tie, "0") == "1." + "0" * 26 + "2"

    def test_carry_across_all_nines(self):
        nines_tie = "9" * 28 + "5"  # 29 digits; ties to even -> up -> 10^29
        assert mi.dec_add(nines_tie, "0") == "1" + "0" * 29

    def test_division_tie_with_exact_half(self):
        # (10^28 + 1) / 2 = 5*10^27 + 0.5: a true tie, kept digit even -> down
        assert mi.dec_div("1" + "0" * 27 + "1", "2") == "5" + "0" * 27
        # (10^28 + 3) / 2 = 5*10^27 + 1.5: a true tie, kept digit odd -> up
        assert mi.dec_div("1" + "0" * 27 + "3", "2") == "5" + "0" * 26 + "2"

    def test_huge_plus_tiny(self):
        assert mi.dec_add("1" + "0" * 30, "1") == "1" + "0" * 30


class TestNegative:
    @pytest.mark.parametrize("text", BAD_LITERALS)
    def test_bad_literal_rejected_everywhere(self, text, assert_mdq_error):
        with assert_mdq_error("MDQ_CALC_ERROR"):
            mi.dec_canonical(text)
        with assert_mdq_error("MDQ_CALC_ERROR"):
            mi.dec_neg(text)
        with assert_mdq_error("MDQ_CALC_ERROR"):
            mi.dec_round_sig(text, 5)
        for fn in BINARY.values():
            with assert_mdq_error("MDQ_CALC_ERROR"):
                fn(text, "1")  # bad first operand
            with assert_mdq_error("MDQ_CALC_ERROR"):
                fn("1", text)  # bad second operand

    def test_error_names_the_bad_literal(self, assert_mdq_error):
        with assert_mdq_error("MDQ_CALC_ERROR") as info:
            mi.dec_canonical("1e3")
        assert "1e3" in str(info.value)

    @pytest.mark.parametrize("value", [1, 1.5, None, b"1", decimal.Decimal("1")])
    def test_non_str_input_is_type_error(self, value):
        # Internal hooks take text only; the public API will define Decimal/int rules.
        with pytest.raises(TypeError):
            mi.dec_canonical(value)
        with pytest.raises(TypeError):
            mi.dec_add(value, "1")

    def test_literal_length_limit(self, assert_mdq_error):
        ok = "1" * 10_000
        assert mi.dec_canonical(ok) == ok
        with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
            mi.dec_canonical("1" * 10_001)

    @pytest.mark.parametrize("prec", [0, 1001])
    def test_round_sig_precision_range(self, prec, assert_mdq_error):
        with assert_mdq_error("MDQ_CALC_ERROR"):
            mi.dec_round_sig("1.5", prec)

    def test_round_sig_precision_types(self):
        with pytest.raises(OverflowError):
            mi.dec_round_sig("1.5", -1)
        with pytest.raises(TypeError):
            mi.dec_round_sig("1.5", "3")


class TestErrors:
    @pytest.mark.parametrize(
        "a,b", [("10", "0"), ("10", "0.00"), ("10", "-0"), ("0", "0"), ("0.0", "0"), ("-3", "0")]
    )
    def test_division_by_zero(self, a, b, assert_mdq_error):
        with assert_mdq_error("MDQ_CALC_ERROR") as info:
            mi.dec_div(a, b)
        assert "zero" in str(info.value)

    @pytest.mark.parametrize("b", ["5", "-5", "0.001"])
    def test_zero_numerator_is_zero(self, b):
        assert mi.dec_div("0", b) == "0"

    def test_overflow_boundary(self, assert_mdq_error):
        big = "1" + "0" * 9999  # 10^9999: allowed
        assert mi.dec_canonical(big) == big
        assert mi.dec_add(big, big) == "2" + "0" * 9999
        with assert_mdq_error("MDQ_CALC_ERROR") as info:
            mi.dec_mul(big, "10")
        assert "overflow" in str(info.value)
        nine = "9" + "0" * 9999
        with assert_mdq_error("MDQ_CALC_ERROR"):
            mi.dec_add(nine, nine)

    def test_underflow_boundary(self, assert_mdq_error):
        tiny = "." + "0" * 9998 + "1"  # 10^-9999: allowed
        assert mi.dec_canonical(tiny) == "0" + tiny
        with assert_mdq_error("MDQ_CALC_ERROR") as info:
            mi.dec_mul(tiny, "0.1")
        assert "underflow" in str(info.value)


def _both(name, a, b):
    """Run one op in Rust and in the oracle; outcomes must agree."""
    fn = BINARY[name]
    try:
        expected = oracle.op(name, a, b)
    except (decimal.DivisionByZero, decimal.InvalidOperation, decimal.Overflow):
        with pytest.raises(mdq.MdqError) as info:
            fn(a, b)
        assert info.value.code == "MDQ_CALC_ERROR"
        return
    assert fn(a, b) == expected, (name, a, b)


@pytest.mark.property
class TestProperties:
    @given(oracle.literals())
    def test_canonical_is_idempotent(self, x):
        once = mi.dec_canonical(x)
        assert mi.dec_canonical(once) == once

    @given(oracle.literals(), oracle.literals())
    def test_add_and_mul_commute(self, a, b):
        assert mi.dec_add(a, b) == mi.dec_add(b, a)
        assert mi.dec_mul(a, b) == mi.dec_mul(b, a)

    @given(oracle.literals(), oracle.literals())
    def test_sub_is_negated_swap(self, a, b):
        assert mi.dec_sub(a, b) == mi.dec_neg(mi.dec_sub(b, a))

    @given(oracle.literals())
    def test_double_negation(self, x):
        assert mi.dec_neg(mi.dec_neg(x)) == mi.dec_canonical(x)

    @given(oracle.literals(), oracle.literals())
    def test_results_have_at_most_28_significant_digits(self, a, b):
        for name in ("add", "sub", "mul"):
            assert oracle.significant_digits(BINARY[name](a, b)) <= 28
        assume(oracle.significant_digits(b) > 0)
        assert oracle.significant_digits(mi.dec_div(a, b)) <= 28

    @given(oracle.literals())
    def test_adding_zero_equals_rounding_to_28(self, x):
        assert mi.dec_add(x, "0") == mi.dec_round_sig(x, 28)


@pytest.mark.differential
class TestAgainstPythonDecimal:
    """Every operation must equal Python's decimal at prec=28, ROUND_HALF_EVEN."""

    @pytest.mark.parametrize("name", list(BINARY))
    @given(a=oracle.literals(), b=oracle.literals())
    def test_random_literals(self, name, a, b):
        _both(name, a, b)

    @pytest.mark.parametrize("name", list(BINARY))
    @given(a=oracle.tie_literals(), b=oracle.tie_literals())
    def test_tie_heavy_operands(self, name, a, b):
        _both(name, a, b)

    @given(oracle.tie_literals())
    def test_tie_against_zero_and_small_divisors(self, a):
        _both("add", a, "0")
        for divisor in ("2", "4", "8", "16", "5", "1000"):
            _both("div", a, divisor)

    @given(a=oracle.small_ints, b=oracle.small_ints)
    def test_small_integer_division(self, a, b):
        _both("div", a, b)

    @given(oracle.literals(), st.integers(min_value=1, max_value=45))
    def test_round_sig(self, a, prec):
        assert mi.dec_round_sig(a, prec) == oracle.round_sig(a, prec)
