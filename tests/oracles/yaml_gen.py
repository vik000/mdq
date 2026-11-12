"""Shared generators and emitters for the YAML / metadata tests (test-only).

`documents` generates nested mappings of None/bool/Decimal/str; `json_text`
writes them as YAML-compatible JSON flow text (non-BMP characters use `\\U`);
`emit` writes them as randomised block-style YAML; `normalise` maps PyYAML
results onto the loader's representation (numbers as Decimal).
"""
from decimal import Decimal as D

from hypothesis import strategies as st

import json

def normalise(value):
    """PyYAML result -> our representation (numbers as Decimal)."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return D(repr(value))
    if isinstance(value, list):
        return [normalise(v) for v in value]
    return {str(k): normalise(v) for k, v in value.items()}


_alnum = st.text(alphabet="abcxyz019 _-", min_size=1, max_size=6).map(lambda s: "k" + s.strip())
_strings = st.text(
    alphabet=st.one_of(st.sampled_from(list("ab 1.-:#'\"\\{}[],&*!|>%@`?~\t\n")), st.characters(
        blacklist_categories=("Cs", "Cc"), blacklist_characters="\u2028\u2029\u0085\ufeff\ufffe\uffff")),
    max_size=12,
)
_number_texts = st.sampled_from(["0", "7", "-3", "3.14", "10.50", "1e3", "-2.5E-2", "123456789012345678901234567890", "0.001"])
leaves = st.one_of(
    st.none(), st.booleans(), _number_texts.map(D), _strings,
)
values = st.recursive(
    leaves,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4),
        st.dictionaries(_alnum, inner, max_size=4),
    ),
    max_leaves=12,
)
documents = st.dictionaries(_alnum, values, max_size=5)


def _quote(s, ascii_only=True):
    """Double-quoted YAML/JSON-style string; non-BMP characters use `\\U`, never surrogate pairs."""
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif 0x20 <= o < 0x7F or (not ascii_only and o >= 0xA0 and o not in (0xFEFF, 0xFFFE, 0xFFFF)):
            out.append(ch)
        elif o <= 0xFFFF:
            out.append(f"\\u{o:04X}")
        else:
            out.append(f"\\U{o:08X}")
    out.append('"')
    return "".join(out)


def json_text(value, indent=None, ensure_ascii=True):
    def conv(v):
        if isinstance(v, D):
            return JsonNumber(str(v))
        if isinstance(v, list):
            return [conv(x) for x in v]
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        return v

    class JsonNumber(float):
        def __new__(cls, text):
            obj = float.__new__(cls, 0.0)
            obj.text = text
            return obj

    return _dump(conv(value), indent, ensure_ascii)


def _dump(v, indent, ensure_ascii, level=0):
    pad = "" if indent is None else "\n" + " " * (indent * (level + 1))
    end = "" if indent is None else "\n" + " " * (indent * level)
    sep = "," if indent is None else ","
    if hasattr(v, "text"):
        return v.text
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[" + sep.join(pad + _dump(x, indent, ensure_ascii, level + 1) for x in v) + end + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        colon = ":" if indent is None else ": "
        return "{" + sep.join(pad + _quote(k, ensure_ascii) + colon + _dump(x, indent, ensure_ascii, level + 1)
                              for k, x in v.items()) + end + "}"
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    return _quote(v, ensure_ascii)


def emit(value, indent, rnd, width):
    """Independent block-style emitter. Returns lines."""
    pad = " " * indent
    if isinstance(value, dict) and value:
        out = []
        for k, v in value.items():
            key = k if rnd.random() < 0.5 else _quote(k)
            if _is_block(v):
                if isinstance(v, list) and rnd.random() < 0.4:
                    out.append(f"{pad}{key}:")
                    out += emit(v, indent, rnd, width)                       # sequence at the key's indent
                else:
                    out.append(f"{pad}{key}:")
                    out += emit(v, indent + width, rnd, width)
            else:
                out.append(f"{pad}{key}: {scalar(v, rnd)}{comment(rnd)}")
            if rnd.random() < 0.15:
                out.append(" " * rnd.choice([0, indent, indent + 1]) + "# interleaved comment")
        return out
    if isinstance(value, list) and value:
        out = []
        for item in value:
            if _is_block(item):
                if rnd.random() < 0.6:                                         # compact `- key: v` / `- - x`
                    sub = emit(item, indent + 2, rnd, width)
                    sub[0] = pad + "- " + sub[0][indent + 2:]
                    out += sub
                else:
                    out.append(f"{pad}-")
                    out += emit(item, indent + width, rnd, width)
            else:
                out.append(f"{pad}- {scalar(item, rnd)}{comment(rnd)}")
        return out
    return [pad + scalar(value, rnd)]


def _is_block(v):
    return isinstance(v, (dict, list)) and len(v) > 0


def comment(rnd):
    return " # note" if rnd.random() < 0.2 else ""


def scalar(v, rnd):
    if v is None:
        return rnd.choice(["null", "~", "Null"])
    if isinstance(v, bool):
        return rnd.choice(["true", "True", "TRUE"] if v else ["false", "False", "FALSE"])
    if isinstance(v, D):
        return json_number(v)
    if isinstance(v, list):
        return "[]"
    if isinstance(v, dict):
        return "{}"
    if "\n" not in v and "\t" not in v and rnd.random() < 0.5:
        return "'" + v.replace("'", "''") + "'"
    return _quote(v)


def json_number(v):
    return str(v) if "E" not in str(v) else format(v, "f")


