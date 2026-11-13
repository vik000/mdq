"""Epic 4 / E4.3 (F06): block IDs, document index and reference resolution (spec sections 2, 3, 3.1).

- `doc_index(text, first_line=1)`: headings (ordinal, level, plain text, id, line range, section end)
  and identified blocks. A block ID is a trailing `^id` (`[A-Za-z][A-Za-z0-9_-]*`) after white space
  at the end of a paragraph or heading, or alone on the last line of a multi-line paragraph.
  Duplicates raise MDQ_DUPLICATE_ID at the second marker. Lines are 0-based, ends exclusive.
- `resolve_ref(text, fragment, first_line=1)`: "" is the whole document, `^id` a block, anything else
  the FIRST heading with that plain text (NFC, collapsed white space, case-sensitive). G34.
"""
import re
import unicodedata

import pytest
from hypothesis import given, strategies as st
from markdown_it import MarkdownIt

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

index = mi.doc_index
resolve = mi.resolve_ref


def ids(src):
    return [b["id"] for b in index(src)["blocks"]]


# ---------------------------------------------------------------- markers
@pytest.mark.parametrize("src,expected", [
    ("text ^a\n", ["a"]),
    ("text ^a", ["a"]),
    ("text ^a   \n", ["a"]),
    ("text\t^a\n", ["a"]),
    ("text ^abc-DEF_9\n", ["abc-DEF_9"]),
    ("line one\nline two ^x\n", ["x"]),
    ("line one\n^x\n", ["x"]),
    ("line one\n  ^x\n", ["x"]),
    ("a ^one\n\nb ^two\n", ["one", "two"]),
    ("> quoted ^q\n", ["q"]),
    ("- item ^i\n", ["i"]),
    ("# Head ^h\n", ["h"]),
    ("Setext ^s\n=====\n", ["s"]),
    ("## Head ^h ##\n", ["h"]),
    # not markers
    ("^only\n", []),
    ("text^a\n", []),
    ("text ^a b\n", []),
    ("text ^1a\n", []),
    ("text ^-a\n", []),
    ("text ^_a\n", []),
    ("text ^\n", []),
    ("text \\^a\n", []),
    ("text ^a^b\n", []),
    ("text ^é\n", []),
    ("`code ^a`\n", []),
    ("# ^h\n", []),
    ("    indented ^a\n", []),
    ("```\ncode ^a\n```\n", []),
    ("<div>\nhtml ^a\n</div>\n", []),
    ("***\n", []),
])
def test_markers(src, expected):
    assert ids(src) == expected


def test_marker_is_not_part_of_heading_text():
    h = index("## Intro *x* ^intro\n")["headings"][0]
    assert h["text"] == "Intro x" and h["id"] == "intro"


def test_atx_closing_sequence_before_marker():
    assert index("## A ^i ##\n")["headings"][0]["text"] == "A"
    assert index("## A ## ^i\n")["headings"][0]["text"] == "A ##"


def test_setext_multiline_heading_marker_on_last_content_line():
    h = index("Foo\nbar ^k\n---\n")["headings"][0]
    assert (h["text"], h["id"], h["level"], h["start_line"], h["end_line"]) == ("Foo bar", "k", 2, 0, 3)


# ---------------------------------------------------------------- duplicates
def test_duplicate_ids_error_at_second_marker(assert_mdq_error):
    with assert_mdq_error("MDQ_DUPLICATE_ID", line=3, column=3):
        index("a ^x\n\nb ^x\n")


def test_duplicate_between_heading_and_paragraph(assert_mdq_error):
    with assert_mdq_error("MDQ_DUPLICATE_ID", line=3, column=6):
        index("# H ^x\n\npara ^x\n")


def test_duplicate_position_uses_first_line_and_last_line_of_paragraph(assert_mdq_error):
    with assert_mdq_error("MDQ_DUPLICATE_ID", line=13, column=1):
        index("a ^x\n\nb\n^x\n", first_line=10)


def test_ids_are_case_sensitive():
    assert ids("a ^x\n\nb ^X\n") == ["x", "X"]


def test_same_id_in_code_is_not_a_duplicate():
    assert ids("a ^x\n\n```\nb ^x\n```\n") == ["x"]


# ---------------------------------------------------------------- headings
def test_index_headings_basic():
    d = "# A\n\ntext\n\n## B\n\n### C\n\n## D\n\n# E\n"
    hs = index(d)["headings"]
    assert [(h["ordinal"], h["level"], h["text"], h["start_line"], h["end_line"], h["section_end"]) for h in hs] == [
        (0, 1, "A", 0, 1, 10), (1, 2, "B", 4, 5, 8), (2, 3, "C", 6, 7, 8), (3, 2, "D", 8, 9, 10), (4, 1, "E", 10, 11, 11)]


@pytest.mark.parametrize("src,level", [
    ("# a\n", 1), ("###### a\n", 6), ("   ## a\n", 2), ("> ### a\n", 3), ("a\n=\n", 1), ("a\n---\n", 2), ("a\n==  \n", 1),
    ("##\n", 2), ("- # a\n", 1),
])
def test_levels(src, level):
    assert index(src)["headings"][0]["level"] == level


def test_heading_text_is_rendered_text():
    hs = index("# *a* &amp; `b`\n\n## [c](d)\n")["headings"]
    assert [h["text"] for h in hs] == ["a & b", "c"]


def test_no_headings_no_blocks():
    assert index("") == {"headings": [], "blocks": []}
    assert index("just text\n") == {"headings": [], "blocks": []}


def test_section_end_is_last_block_without_later_heading():
    assert index("# A\n\ntext\n")["headings"][0]["section_end"] == 3


# ---------------------------------------------------------------- resolve
DOC = "# Top\n\nintro ^intro\n\n## Dup\n\nfirst\n\n## Dup\n\nsecond ^second\n\n## Other ^other\n\nbody\n"


def test_resolve_document():
    r = resolve(DOC, "")
    assert r == {"kind": "document", "start_line": 0, "end_line": 15, "id": None}


def test_resolve_first_duplicate_heading():
    r = resolve(DOC, "Dup")
    assert (r["kind"], r["ordinal"], r["start_line"], r["end_line"], r["section_end"]) == ("heading", 1, 4, 5, 8)


def test_resolve_block_id_paragraph():
    assert resolve(DOC, "^second") == {"kind": "paragraph", "start_line": 10, "end_line": 11, "id": "second"}


def test_resolve_block_id_on_heading():
    r = resolve(DOC, "^other")
    assert (r["kind"], r["text"], r["id"], r["ordinal"], r["section_end"]) == ("heading", "Other", "other", 3, 15)


def test_resolve_heading_ignores_marker_text():
    assert resolve(DOC, "Other")["id"] == "other"
    with pytest.raises(mdq.MdqError) as e:
        resolve(DOC, "Other ^other")
    assert e.value.code == "MDQ_UNRESOLVED_REFERENCE"


def test_resolve_heading_normalisation():
    d = "# Caf\u00e9  au   lait\n"
    for frag in ["Caf\u00e9 au lait", "Cafe\u0301 au lait", "  Caf\u00e9\tau lait "]:
        assert resolve(d, frag)["ordinal"] == 0


def test_resolve_heading_case_sensitive():
    with pytest.raises(mdq.MdqError) as e:
        resolve("# Intro\n", "intro")
    assert e.value.code == "MDQ_UNRESOLVED_REFERENCE"


def test_resolve_heading_in_code_is_not_a_heading():
    with pytest.raises(mdq.MdqError) as e:
        resolve("```\n# Fake\n```\n", "Fake")
    assert e.value.code == "MDQ_UNRESOLVED_REFERENCE"


@pytest.mark.parametrize("frag", ["^nope", "Nope", "^Intro"])
def test_resolve_unresolved(frag):
    with pytest.raises(mdq.MdqError) as e:
        resolve(DOC, frag)
    assert e.value.code == "MDQ_UNRESOLVED_REFERENCE"


@pytest.mark.parametrize("frag", ["^", "^1a", "^a b", "^a.b", "^\u00e9"])
def test_resolve_invalid_block_id(frag):
    with pytest.raises(mdq.MdqError) as e:
        resolve(DOC, frag)
    assert e.value.code == "MDQ_INVALID_SYNTAX"


def test_resolve_propagates_duplicate_id(assert_mdq_error):
    with assert_mdq_error("MDQ_DUPLICATE_ID", line=3, column=3):
        resolve("a ^x\n\nb ^x\n", "x")


def test_resolve_empty_document():
    assert resolve("", "")["end_line"] == 0


# ---------------------------------------------------------------- types
@pytest.mark.parametrize("args", [(1,), (None,), (b"x",)])
def test_index_type_errors(args):
    with pytest.raises(TypeError):
        index(*args)


def test_index_first_line_type_error():
    with pytest.raises(TypeError):
        index("a", "1")


@pytest.mark.parametrize("args", [("a", 1), (1, "a"), ("a", None)])
def test_resolve_type_errors(args):
    with pytest.raises(TypeError):
        resolve(*args)


# ---------------------------------------------------------------- differential
LINES = ["# One", "## Two ^t", "### Three ##", "Para ^p", "Para", "", "", "```", "code ^c", "~~~", "Setext", "===", "---",
         "- item ^i", "> quote ^q", "    indented ^z", "<div>", "</div>", "text ^p", "# Same", "# Same"]


def oracle(src):
    md = MarkdownIt("commonmark")
    lines = src.split("\n")
    heads, marks = [], []
    for t in md.parse(src):
        if t.type not in ("heading_open", "paragraph_open") or not t.map:
            continue
        a, b = t.map
        if t.type == "heading_open":
            heads.append((int(t.tag[1]), a, t.map[1]))
        if t.type == "heading_open" and b - a > 1:
            b -= 1  # setext: the underline is not content
        m = re.search(r"(?:^|[ \t])\^([A-Za-z][A-Za-z0-9_-]*)[ \t]*$", lines[b - 1])
        if m and (lines[b - 1][:m.start(1) - 1].strip(" \t") or b - a > 1):
            marks.append(m.group(1))
    return heads, marks


@given(st.lists(st.sampled_from(LINES), min_size=1, max_size=10))
def test_headings_and_ids_match_markdown_it(lines):
    src = "\n".join(lines) + "\n"
    heads, marks = oracle(src)
    if len(marks) != len(set(marks)):
        with pytest.raises(mdq.MdqError) as e:
            index(src)
        assert e.value.code == "MDQ_DUPLICATE_ID"
        return
    d = index(src)
    assert [(h["level"], h["start_line"], h["end_line"]) for h in d["headings"]] == heads, src
    assert [b["id"] for b in d["blocks"]] == marks, src
