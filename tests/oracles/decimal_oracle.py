"""Independent decimal oracle: Python's stdlib `decimal`.

Test-only. Mirrors the MDQ policy: 28 significant digits, round-half-even,
adjusted exponent within +-9999 (spec gap G4), traps for the errors MDQ
rejects. Inputs must already be valid MDQ literals: Python's Decimal also
accepts things MDQ rejects (`1e3`, `NaN`, Arabic-Indic digits, spaces).
"""
from decimal import (
    Context, Decimal, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_EVEN,
)

from hypothesis import strategies as st

MAX_EXP = 9999


def context(prec: int = 28) -> Context:
    return Context(
        prec=prec, rounding=ROUND_HALF_EVEN, Emax=MAX_EXP, Emin=-MAX_EXP,
        traps=[Overflow, DivisionByZero, InvalidOperation],
    )


def canonical(d: Decimal) -> str:
    s = format(d, "f")  # never uses an exponent
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def op(name: str, a: str, b: str) -> str:
    ctx = context()
    fn = {"add": ctx.add, "sub": ctx.subtract, "mul": ctx.multiply, "div": ctx.divide}[name]
    return canonical(fn(Decimal(a), Decimal(b)))


def round_sig(a: str, prec: int) -> str:
    return canonical(context(prec).plus(Decimal(a)))


def significant_digits(s: str) -> int:
    """Digits left after dropping sign, point, leading and trailing zeros."""
    digits = s.lstrip("+-").replace(".", "").strip("0")
    return len(digits)


# ---- Hypothesis strategies producing VALID MDQ literals ----
_digits = st.text(alphabet="0123456789", min_size=1, max_size=40)
_signs = st.sampled_from(["", "", "-", "+"])


@st.composite
def literals(draw):
    sign = draw(_signs)
    if draw(st.integers(0, 9)) == 0:
        return sign + "." + draw(_digits)  # ".5" form
    text = sign + draw(_digits)
    if draw(st.booleans()):
        text += "." + draw(_digits)
    return text


@st.composite
def tie_literals(draw):
    """28 digits then a 5 (then maybe zeros): an exact tie at digit 28."""
    head = draw(st.text(alphabet="0123456789", min_size=27, max_size=27))
    first = draw(st.sampled_from("123456789"))
    text = first + head + "5" + "0" * draw(st.integers(0, 3))
    if draw(st.booleans()):
        text = text[:10] + "." + text[10:]
    return draw(st.sampled_from(["", "-"])) + text


small_ints = st.integers(min_value=-10**6, max_value=10**6).map(str)
