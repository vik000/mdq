"""Generative oracle for the calc lexer (tests only).

Builds a source text AND the tokens it must produce at the same time, from the
lexer rules, so the Rust lexer is checked against an independent construction.
Expected tokens are (kind, text, relative_line, col); relative_line is 0-based.
"""
from hypothesis import strategies as st

from oracles.units_oracle import SYMBOLS

UNIT_SYMBOLS = sorted(s for s in SYMBOLS if s != "deltaK")  # deltaK is result-only
CURRENCIES = ["EUR", "USD", "GBP", "JPY", "CHF", "AUD"]
OPS = {"+": "plus", "-": "minus", "*": "star", "/": "slash",
       "(": "lparen", ")": "rparen", "%": "percent", "=": "equals"}

WORDISH = {"number", "ident", "unit", "currency", "to"}
NEXT = {
    None: ["number", "ident", "op"],
    "number": ["number", "unit", "currency", "to", "op"],
    "ident": ["ident", "number", "to", "op"],
    "unit": ["ident", "number", "to", "op"],
    "currency": ["ident", "number", "to", "op"],
    "to": ["unit", "currency"],
    "op": ["number", "ident", "op"],
}

_LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ws = st.text(alphabet=" \t", min_size=1, max_size=3)
_opt_ws = st.text(alphabet=" \t", min_size=0, max_size=3)

numbers = st.one_of(
    st.builds(lambda a: a, st.text("0123456789", min_size=1, max_size=6)),
    st.builds(lambda a, b: f"{a}.{b}", st.text("0123456789", min_size=1, max_size=6),
              st.text("0123456789", min_size=1, max_size=5)),
    st.builds(lambda b: f".{b}", st.text("0123456789", min_size=1, max_size=5)),
)
_generated_idents = st.builds(
    lambda a, b: a + b, st.sampled_from(_LETTERS),
    st.text(_LETTERS + "0123456789_", max_size=6),
).filter(lambda w: w != "to")
_unitlike_idents = st.sampled_from([s for s in UNIT_SYMBOLS if s.isidentifier()] + CURRENCIES)
idents = st.one_of(_generated_idents, _unitlike_idents)


@st.composite
def statement(draw):
    """One non-empty statement line -> (text, [(kind, text, col)], newline_col)."""
    text = draw(_opt_ws)
    toks, prev = [], None  # prev = real kind of the previous token
    for _ in range(draw(st.integers(1, 8))):
        key = prev if (prev is None or prev in WORDISH) else "op"
        choice = draw(st.sampled_from(NEXT[key]))
        if choice == "number":
            kind, t = "number", draw(numbers)
        elif choice == "ident":
            kind, t = "ident", draw(idents)
        elif choice == "unit":
            kind, t = "unit", draw(st.sampled_from(UNIT_SYMBOLS))
        elif choice == "currency":
            kind, t = "currency", draw(st.sampled_from(CURRENCIES))
        elif choice == "to":
            kind, t = "to", "to"
        else:
            t = draw(st.sampled_from(sorted(OPS)))
            kind = OPS[t]
        need_ws = (prev in WORDISH and kind in WORDISH) or (
            prev in ("unit", "currency") and kind == "slash")
        if toks:
            text += draw(_ws) if need_ws else draw(_opt_ws)
        toks.append((kind, t, len(text) + 1))
        text += t
        prev = kind
    text += draw(_opt_ws)
    return text, toks, len(text) + 1


@st.composite
def programs(draw):
    """-> (source_text, expected_tokens)"""
    nlines = draw(st.integers(0, 6))
    text, expected = "", []
    for idx in range(nlines):
        kind = draw(st.sampled_from(["stmt", "stmt", "stmt", "blank", "comment"]))
        if idx == nlines - 1:
            term = draw(st.sampled_from(["", "\n", "\r\n"]))
        else:
            term = draw(st.sampled_from(["\n", "\r\n"]))
        if kind == "stmt":
            line, toks, nl_col = draw(statement())
            expected += [(k, t, idx, c) for k, t, c in toks]
            expected.append(("newline", "\n", idx, nl_col))
        elif kind == "blank":
            line = draw(_opt_ws)
        else:
            line = draw(_opt_ws) + "#" + draw(st.text(
                st.characters(blacklist_characters="\n", blacklist_categories=("Cs",)), max_size=20))
        text += line + term
    return text, expected
