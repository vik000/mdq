"""Independent derivation of the unit catalogue, for tests only.

Builds every factor from DEFINING RELATIONS with exact `Fraction`s. It never
reads spec/units.tsv, so agreement between this module and the table is a
real cross-check. Value of a unit in its base unit:  (x + offset) * scale.
"""
from decimal import Context, Decimal, ROUND_HALF_EVEN
from fractions import Fraction as F

PI = F("3.1415926535897932384626433832795028841972")  # 40 decimals (calculation-units §5)
G0 = F("9.80665")  # standard gravity, m/s2 (exact by definition)

inch = F("0.0254")
ft = 12 * inch
yd = 3 * ft
mile = 1760 * yd
lb = F("0.45359237")
oz = lb / 16
ft2 = ft * ft
acre = 43560 * ft2
gal = 231 * inch**3
qt = gal / 4
lbf = lb * G0
hp = 550 * ft * lbf
psi = lbf / (inch * inch)

# Fahrenheit offset derived from K = (degF - 32) * 5/9 + 273.15:
DEGF_OFFSET = F("273.15") * F(9, 5) - 32

UNITS = {  # symbol -> (dimension, scale, offset)
    "m": ("L1", F(1), F(0)), "km": ("L1", F(1000), F(0)),
    "cm": ("L1", F(1, 100), F(0)), "mm": ("L1", F(1, 1000), F(0)),
    "in": ("L1", inch, F(0)), "ft": ("L1", ft, F(0)),
    "yd": ("L1", yd, F(0)), "miles": ("L1", mile, F(0)),
    "kg": ("M1", F(1), F(0)), "g": ("M1", F(1, 1000), F(0)),
    "mg": ("M1", F(1, 10**6), F(0)), "lb": ("M1", lb, F(0)), "oz": ("M1", oz, F(0)),
    "m2": ("L2", F(1), F(0)), "km2": ("L2", F(1000) ** 2, F(0)),
    "ft2": ("L2", ft2, F(0)), "acre": ("L2", acre, F(0)), "ha": ("L2", F(10**4), F(0)),
    "m3": ("L3", F(1), F(0)), "L": ("L3", F(1, 1000), F(0)),
    "mL": ("L3", F(1, 10**6), F(0)), "gal_us": ("L3", gal, F(0)), "qt_us": ("L3", qt, F(0)),
    "s": ("T1", F(1), F(0)), "min": ("T1", F(60), F(0)),
    "h": ("T1", F(3600), F(0)), "day": ("T1", F(24 * 3600), F(0)),
    "K": ("Th1", F(1), F(0)), "degC": ("Th1", F(1), F("273.15")),
    "degF": ("Th1", F(5, 9), DEGF_OFFSET), "deltaK": ("dTh1", F(1), F(0)),
    "m/s": ("L1*T-1", F(1), F(0)), "km/h": ("L1*T-1", F(1000, 3600), F(0)),
    "mph": ("L1*T-1", mile / 3600, F(0)),
    "J": ("M1*L2*T-2", F(1), F(0)), "kJ": ("M1*L2*T-2", F(1000), F(0)),
    "cal": ("M1*L2*T-2", F("4.184"), F(0)), "kWh": ("M1*L2*T-2", F(1000 * 3600), F(0)),
    "W": ("M1*L2*T-3", F(1), F(0)), "kW": ("M1*L2*T-3", F(1000), F(0)),
    "hp": ("M1*L2*T-3", hp, F(0)),
    "Pa": ("M1*L-1*T-2", F(1), F(0)), "kPa": ("M1*L-1*T-2", F(1000), F(0)),
    "bar": ("M1*L-1*T-2", F(10**5), F(0)), "psi": ("M1*L-1*T-2", psi, F(0)),
    "rad": ("A1", F(1), F(0)), "deg": ("A1", PI / 180, F(0)),
    "bit": ("I1", F(1), F(0)), "B": ("I1", F(8), F(0)),
    "kB": ("I1", F(8 * 1000), F(0)), "MB": ("I1", F(8 * 1000**2), F(0)),
    "GB": ("I1", F(8 * 1000**3), F(0)),
    "KiB": ("I1", F(8 * 1024), F(0)), "MiB": ("I1", F(8 * 1024**2), F(0)),
    "GiB": ("I1", F(8 * 1024**3), F(0)),
}

SYMBOLS = list(UNITS)


def parse_exact(text: str) -> F:
    """Exact Fraction from the table notation: 'a', 'a.b' or 'a/b' (decimals)."""
    if "/" in text:
        n, d = text.split("/")
        return F(Decimal(n)) / F(Decimal(d))
    return F(Decimal(text))


def exact_convert(value: str, a: str, b: str) -> F:
    dim_a, s_a, o_a = UNITS[a]
    dim_b, s_b, o_b = UNITS[b]
    assert dim_a == dim_b
    return ((F(Decimal(value)) + o_a) * s_a) / s_b - o_b


def convert(value: str, a: str, b: str) -> str:
    """Canonical text of the exact conversion, rounded ONCE to 28 digits half-even."""
    if a == b:
        return _canonical(Decimal(value))
    y = exact_convert(value, a, b)
    ctx = Context(prec=28, rounding=ROUND_HALF_EVEN)
    return _canonical(ctx.divide(Decimal(y.numerator), Decimal(y.denominator)))


def _canonical(d: Decimal) -> str:
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def same_dimension_pairs():
    return [
        (a, b) for a in SYMBOLS for b in SYMBOLS
        if a != b and UNITS[a][0] == UNITS[b][0]
    ]
