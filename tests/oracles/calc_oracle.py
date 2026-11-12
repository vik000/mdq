"""Independent evaluator for calc parse trees (tests only).

Takes the tree dicts returned by `calc_parse` and evaluates them with Python's
`decimal` (28 digits, half-even, one rounding per operation) and exact
`Fraction`s for unit work. Written from the spec text, NOT from the Rust code:
dimension algebra, result-unit choice and the temperature rules are re-derived
here, so agreement is a real cross-check.

Values: ("dec", Decimal) | ("qty", Decimal, symbol) | ("money", Decimal, code)
Errors: `CalcError(code)`.
"""
from decimal import Decimal
from fractions import Fraction as F

from oracles import decimal_oracle as D
from oracles.units_oracle import UNITS

ABSOLUTE = {"degC", "degF", "K"}
ORDER = ["M", "L", "T", "Th", "dTh", "A", "I"]
CTX = D.context()


class CalcError(Exception):
    def __init__(self, code="MDQ_CALC_ERROR", message=""):
        super().__init__(message)
        self.code = code


def dim_of(symbol):
    out = {}
    for part in UNITS[symbol][0].split("*"):
        i = next(k for k, c in enumerate(part) if c.isdigit() or c == "-")
        out[part[:i]] = int(part[i:])
    return out


def dim_mul(a, b, sign):
    out = dict(a)
    for k, e in b.items():
        out[k] = out.get(k, 0) + sign * e
    return {k: e for k, e in out.items() if e}


def dim_key(d):
    return tuple(sorted(d.items()))


# one coherent unit per dimension: scale 1, offset 0, a real (non-affine) unit
COHERENT = {}
for _s, (_dim, _scale, _off) in UNITS.items():
    if _scale == 1 and _off == 0 and _s != "deltaK":
        COHERENT.setdefault(dim_key(dim_of(_s)), _s)


def base(x, sym):
    _, scale, off = UNITS[sym]
    return (F(x) + off) * scale


def to_decimal(fr):
    return CTX.divide(Decimal(fr.numerator), Decimal(fr.denominator))


def convert(x, a, b):
    _, sb, ob = UNITS[b]
    return to_decimal(base(x, a) / sb - ob)


def err(msg="", code="MDQ_CALC_ERROR"):
    raise CalcError(code, msg)


def add_sub(sub, l, r):
    op = CTX.subtract if sub else CTX.add
    if l[0] == "dec" and r[0] == "dec":
        return ("dec", op(l[1], r[1]))
    if l[0] == "money" and r[0] == "money":
        if l[2] != r[2]:
            err("currency mismatch")
        return ("money", op(l[1], r[1]), l[2])
    if l[0] == "qty" and r[0] == "qty":
        a, b = l[2], r[2]
        if a in ABSOLUTE and b in ABSOLUTE:
            if not sub:
                err("absolute + absolute")
            return ("qty", to_decimal(base(l[1], a) - base(r[1], b)), "deltaK")
        if (a in ABSOLUTE and b == "deltaK") or (a == "deltaK" and b in ABSOLUTE):
            err("difference with absolute")
        if dim_of(a) != dim_of(b):
            err("dimension mismatch")
        right = r[1] if a == b else convert(r[1], b, a)
        return ("qty", op(l[1], right), a)
    err("type mismatch")


def mul_div(div, l, r):
    op = CTX.divide if div else CTX.multiply
    if l[0] == "dec" and r[0] == "dec":
        return ("dec", op(l[1], r[1]))
    if l[0] == "qty" and r[0] == "dec":
        if l[2] in ABSOLUTE:
            err("absolute temperature in product")
        return ("qty", op(l[1], r[1]), l[2])
    if l[0] == "dec" and r[0] == "qty":
        if div or r[2] in ABSOLUTE:
            err("number / quantity or absolute temperature")
        return ("qty", op(l[1], r[1]), r[2])
    if l[0] == "money" and r[0] == "dec":
        return ("money", op(l[1], r[1]), l[2])
    if l[0] == "dec" and r[0] == "money":
        if div:
            err("number / money")
        return ("money", op(l[1], r[1]), r[2])
    if l[0] == "money" and r[0] == "money":
        if not div or l[2] != r[2]:
            err("money * money or currency mismatch")
        return ("dec", op(l[1], r[1]))
    if l[0] == "qty" and r[0] == "qty":
        a, b = l[2], r[2]
        if a in ABSOLUTE or b in ABSOLUTE:
            err("absolute temperature in product")
        if "deltaK" in (a, b) and not (div and a == b == "deltaK"):
            err("difference in product")
        if div and r[1] == 0:
            err("division by zero")
        dim = dim_mul(dim_of(a), dim_of(b), -1 if div else 1)
        x, y = base(l[1], a), base(r[1], b)
        value = x / y if div else x * y
        if not dim:
            return ("dec", to_decimal(value))
        unit = None
        if not div and a == b:
            for suffix in ("2", "3"):
                cand = a + suffix
                if cand in UNITS and dim_of(cand) == dim:
                    unit = cand
                    break
        if unit is None and div and f"{a}/{b}" in UNITS and dim_of(f"{a}/{b}") == dim:
            unit = f"{a}/{b}"
        if unit is None:
            unit = COHERENT.get(dim_key(dim))
        if unit is None:
            err("no catalogue unit for the result dimension")
        _, scale, off = UNITS[unit]
        return ("qty", to_decimal(value / scale - off), unit)
    err("type mismatch")


def convert_to(v, target, currency):
    if currency:
        if v[0] != "money":
            err("not money")
        if v[2] != target:
            err("no observation", "MDQ_SOURCE_UNRESOLVED")
        return v
    if v[0] != "qty":
        err("only quantities convert")
    if dim_of(v[2]) != dim_of(target):
        err("dimension mismatch")
    if v[2] == target:
        return v
    return ("qty", convert(v[1], v[2], target), target)


def evaluate(node, env=None):
    env = env or {}
    k = node["kind"]
    if k == "number":
        return ("dec", Decimal(node["text"]))
    if k == "quantity":
        if node["currency"]:
            return ("money", Decimal(node["text"]), node["unit"])
        return ("qty", Decimal(node["text"]), node["unit"])
    if k == "ident":
        if node["name"] not in env:
            err("unknown name")
        return env[node["name"]]
    if k == "unary":
        v = evaluate(node["operand"], env)
        return v if node["op"] == "+" else (v[0], -v[1]) + v[2:]
    if k == "percent":
        v = evaluate(node["operand"], env)
        if v[0] != "dec":
            err("percent of a non-number")
        return ("dec", CTX.divide(v[1], Decimal(100)))
    if k == "binary":
        l = evaluate(node["lhs"], env)
        r = evaluate(node["rhs"], env)
        if node["op"] in "+-":
            return add_sub(node["op"] == "-", l, r)
        return mul_div(node["op"] == "/", l, r)
    if k == "convert":
        return convert_to(evaluate(node["operand"], env), node["unit"], node["currency"])
    raise AssertionError(k)


def run(tree):
    """-> (type, canonical value, unit or currency) or raises CalcError"""
    v = evaluate(tree)
    kind = {"dec": "decimal", "qty": "quantity", "money": "money"}[v[0]]
    return kind, D.canonical(v[1]), (v[2] if len(v) > 2 else None)
