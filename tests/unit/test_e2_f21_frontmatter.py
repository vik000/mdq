"""Epic 2 / E2.1 (F03): splitting YAML frontmatter from the body (spec 5).

`mdq._internal.frontmatter_split(text)` returns a dict: has_frontmatter,
frontmatter (text between the fences with LF line ends, or None),
frontmatter_line (2, or 0 when absent), body (verbatim) and body_line.
Decisions (G5, G22): a document whose first line is `---` is always
frontmatter and an unterminated block is MDQ_METADATA_ERROR; a leading BOM is
ignored; `\\r\\n` and `\\n` end lines, a lone `\\r` does not; the first later
`---` line closes the block (YAML is not parsed here).
"""
import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

split = mi.frontmatter_split


def parts(text):
    r = split(text)
    return r["frontmatter"], r["body"], r["body_line"]


# ---------------------------------------------------------------- general


def test_basic_frontmatter():
    assert split("---\ntitle: A\n---\n# Heading\n") == {
        "has_frontmatter": True, "frontmatter": "title: A", "frontmatter_line": 2,
        "body": "# Heading\n", "body_line": 4,
    }


def test_multiple_lines_and_blank_lines_are_kept():
    assert parts("---\na: 1\n\nb:\n  - x\n---\nbody") == ("a: 1\n\nb:\n  - x", "body", 7)


def test_no_frontmatter():
    assert split("# Title\n\ntext\n") == {
        "has_frontmatter": False, "frontmatter": None, "frontmatter_line": 0,
        "body": "# Title\n\ntext\n", "body_line": 1,
    }


@pytest.mark.parametrize("text", ["", "\n", "\n\n", "   ", "text"])
def test_documents_without_a_fence(text):
    r = split(text)
    assert (r["has_frontmatter"], r["body"], r["body_line"]) == (False, text, 1)


def test_empty_frontmatter():
    assert parts("---\n---\nbody") == ("", "body", 3)


def test_frontmatter_with_only_blank_lines():
    assert parts("---\n\n\n---\nx") == ("\n", "x", 5)


def test_empty_body_variants():
    assert parts("---\na: 1\n---\n") == ("a: 1", "", 4)
    assert parts("---\na: 1\n---") == ("a: 1", "", 4)
    assert parts("---\na: 1\n---\n\n") == ("a: 1", "\n", 4)


# -------------------------------------------------- fence recognition


@pytest.mark.parametrize("opener", ["---", "---  ", "---\t", "--- \t "])
def test_trailing_blanks_on_fences(opener):
    assert parts(f"{opener}\na: 1\n{opener}\nbody")[0] == "a: 1"


@pytest.mark.parametrize("first", [
    "----", "-- -", "--- title", "---a", "- - -", "***", "___", " ---", "\t---", "\n---", "--", "-",
    "---:", "---#", "--- #comment",
])
def test_first_line_that_is_not_a_fence(first):
    text = f"{first}\na: 1\n---\nbody"
    r = split(text)
    assert not r["has_frontmatter"] and r["body"] == text and r["body_line"] == 1


@pytest.mark.parametrize("closer", ["----", " ---", "--- x", "...", "--", "***"])
def test_closer_must_be_an_exact_fence(closer, assert_mdq_error):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=1, column=1):
        split(f"---\na: 1\n{closer}\nbody\n")


def test_first_later_fence_closes_the_block():
    assert parts("---\na: 1\n---\nb: 2\n---\nc") == ("a: 1", "b: 2\n---\nc", 4)


def test_a_leading_thematic_break_is_frontmatter_by_decision():
    # G22: `---` on the first line always opens frontmatter; use `***` for a rule.
    assert parts("---\n\nSome text\n\n---\nMore\n") == ("\nSome text\n", "More\n", 6)


def test_a_thematic_break_later_in_the_body_is_untouched():
    text = "# T\n\n---\n\nmore\n"
    assert split(text)["has_frontmatter"] is False
    assert parts("---\na: 1\n---\n# T\n\n---\n\nmore\n")[1] == "# T\n\n---\n\nmore\n"


def test_fence_inside_a_yaml_block_scalar_still_closes():
    assert parts("---\ntext: |\n  one\n---\ntwo\n")[:2] == ("text: |\n  one", "two\n")


# --------------------------------------------------------------- unterminated


@pytest.mark.parametrize("text", [
    "---", "---\n", "---\na: 1", "---\na: 1\n", "---\n\n\n", "---\nb: 2\n----\n", "---\r\na: 1\r\n",
])
def test_unterminated(assert_mdq_error, text):
    with assert_mdq_error("MDQ_METADATA_ERROR", line=1, column=1) as info:
        split(text)
    assert "unterminated" in str(info.value)


# ------------------------------------------------------------------- CRLF


def test_crlf_frontmatter_is_normalised_and_body_is_verbatim():
    r = split("---\r\ntitle: A\r\nb: 2\r\n---\r\nline1\r\nline2\r\n")
    assert r["frontmatter"] == "title: A\nb: 2"
    assert r["body"] == "line1\r\nline2\r\n"
    assert r["body_line"] == 5


def test_mixed_line_endings():
    assert parts("---\r\na: 1\nb: 2\r\n---\nx\r\ny") == ("a: 1\nb: 2", "x\r\ny", 5)


def test_a_lone_carriage_return_is_not_a_line_end():
    assert not split("---\ra: 1\r---\rbody")["has_frontmatter"]
    r = split("---\na: 1\rb: 2\n---\nbody")
    assert r["frontmatter"] == "a: 1\rb: 2"
    assert not split("---\r")["has_frontmatter"]            # `---\r` is not a fence line without `\n`


def test_trailing_carriage_returns_inside_content_are_kept():
    assert split("---\na: 1\r\r\n---\n")["frontmatter"] == "a: 1\r"


# -------------------------------------------------------------------- BOM


def test_leading_bom_is_ignored_and_dropped():
    assert parts("﻿---\na: 1\n---\nbody") == ("a: 1", "body", 4)
    r = split("﻿# Title")
    assert (r["has_frontmatter"], r["body"]) == (False, "# Title")


def test_bom_only_applies_at_the_start():
    assert split("\n﻿---\na: 1\n---\n")["has_frontmatter"] is False
    assert parts("---\n﻿a: 1\n---\n")[0] == "﻿a: 1"
    assert split("﻿﻿---\na: 1\n---\n")["has_frontmatter"] is False     # only one BOM is dropped


def test_bom_alone():
    r = split("﻿")
    assert (r["has_frontmatter"], r["body"], r["body_line"]) == (False, "", 1)


# ---------------------------------------------------------------- Unicode


def test_non_ascii_content_and_positions_are_character_safe():
    r = split("---\ntítulo: ñandú 日本語 🙂\n---\ncuerpo — texto\n")
    assert r["frontmatter"] == "título: ñandú 日本語 🙂"
    assert r["body"] == "cuerpo — texto\n"


def test_unicode_line_separators_do_not_end_lines():
    r = split("---\na: 1 b: 2\u0085\n---\nx")
    assert r["frontmatter"] == "a: 1 b: 2\u0085"


# ---------------------------------------------------------------- types


@pytest.mark.parametrize("bad", [None, 5, b"---\n---\n", ["---"]])
def test_type_errors(bad):
    with pytest.raises(TypeError):
        split(bad)


# ----------------------------------------------------------- properties


def oracle(text):
    """Independent implementation: split on \\n, strip one trailing \\r per line."""
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.split("\n")
    clean = [ln[:-1] if ln.endswith("\r") else ln for ln in lines]

    def fence(i):
        return clean[i].startswith("---") and clean[i][3:].strip(" \t") == ""

    # a final element of split() is only a line if non-empty (text did not end at a terminator)
    count = len(lines) - (1 if lines[-1] == "" else 0)
    if count == 0 or not fence(0):
        return None, text, 1
    for i in range(1, count):
        if fence(i):
            body = "\n".join(lines[i + 1:])
            return "\n".join(clean[1:i]), body, i + 2
    return "error", None, None


_line = st.sampled_from(["---", "--- ", "----", "a: 1", "", "text", "  - x", "***", "...", " ---", "k: v # c", "é: 日本"])
_end = st.sampled_from(["\n", "\r\n"])


@st.composite
def documents(draw):
    bom = draw(st.sampled_from(["", "﻿"]))
    n = draw(st.integers(0, 8))
    out = bom
    for _ in range(n):
        out += draw(_line) + draw(_end)
    if draw(st.booleans()):
        out += draw(_line)                       # last line without terminator
    return out


@given(documents())
def test_matches_the_oracle(text):
    fm, body, line = oracle(text)
    if fm == "error":
        with pytest.raises(mdq.MdqError) as info:
            split(text)
        assert info.value.code == "MDQ_METADATA_ERROR" and (info.value.line, info.value.column) == (1, 1)
        return
    r = split(text)
    assert (r["frontmatter"], r["body"], r["body_line"]) == (fm, body, line)
    assert r["has_frontmatter"] == (fm is not None)


@given(documents())
def test_body_is_always_a_verbatim_suffix_and_lines_add_up(text):
    try:
        r = split(text)
    except mdq.MdqError:
        return
    plain = text[1:] if text.startswith("\ufeff") else text
    assert plain.endswith(r["body"])
    prefix = plain[: len(plain) - len(r["body"])]
    if not r["has_frontmatter"]:
        assert prefix == "" and r["body_line"] == 1
    else:
        lines_in_prefix = prefix.count("\n") + (0 if prefix.endswith("\n") else 1)
        assert lines_in_prefix == r["body_line"] - 1
