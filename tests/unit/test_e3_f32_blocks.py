"""Epic 3 / E3.2 (F02): block structure (`md_regions`) and directive scanning (`directive_scan`).

`md_regions(text)` lists the leaf blocks of a CommonMark document, containers (block quotes,
list items, nested) included: `{kind, start_line, end_line (0-based, exclusive), segments}`
where `kind` is paragraph / heading / code_fence / code_indented / html / hr and `segments`
are the char ranges of the inline content lines (container prefixes and indentation
removed) of paragraphs and headings. `directive_scan` and `interpolate` only look inside
paragraphs and headings, so `{{ }}` is never found in code or HTML at any nesting depth.
Checked against markdown-it-py (block structure and the set of expanded directives).
"""
import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi
from oracles.md_plain import leaf_blocks, live_and_literal

pytestmark = pytest.mark.unit

regions = mi.md_regions
scan = mi.directive_scan
interp = mi.interpolate


def mine(src):
    """md_regions in the oracle's shape (hr -> thematic_break, trailing blank lines trimmed)."""
    lines = src.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for r in regions(src):
        kind = "thematic_break" if r["kind"] == "hr" else r["kind"]
        a, b = r["start_line"], r["end_line"]
        while b > a + 1 and not lines[b - 1].strip():
            b -= 1
        out.append((kind, a, b))
    return out


def kinds(src):
    return [(r["kind"], r["start_line"], r["end_line"]) for r in regions(src)]


# ---------------------------------------------------------------- structure


def test_empty_and_blank():
    assert regions("") == [] and regions("\n\n  \n") == []


def test_basic_blocks():
    assert kinds("# h\n\npara\nmore\n\n```\ncode\n```\n\n    ind\n\n<div>\nx\n</div>\n\n---\n") == [
        ("heading", 0, 1), ("paragraph", 2, 4), ("code_fence", 5, 8), ("code_indented", 9, 10), ("html", 11, 14), ("hr", 15, 16)]


def test_segments_strip_prefixes():
    text = "> a b\n> c\n"
    segs = regions(text)[0]["segments"]
    assert [text[s:e] for s, e in segs] == ["a b", "c"]
    text = "- item\n  more\n"
    assert [text[s:e] for s, e in regions(text)[0]["segments"]] == ["item", "more"]
    text = "   indented\n  para\n"
    assert [text[s:e] for s, e in regions(text)[0]["segments"]] == ["indented", "para"]
    text = "## Title ##\n"
    assert text[slice(*regions(text)[0]["segments"][0])].startswith("Title")


def test_setext_heading_takes_the_paragraph_lines():
    r = regions("a\nb\n---\n")
    assert [(x["kind"], x["start_line"], x["end_line"], len(x["segments"])) for x in r] == [("heading", 0, 3, 2)]


def test_nested_containers():
    src = "> - a\n>   > b\n>\n> ```\n> c\n> ```\n"
    assert kinds(src) == [("paragraph", 0, 1), ("paragraph", 1, 2), ("code_fence", 3, 6)]
    src = "- a\n\n  - b\n\n    ```\n    c\n    ```\n"
    assert kinds(src) == [("paragraph", 0, 1), ("paragraph", 2, 3), ("code_fence", 4, 7)]


def test_lazy_continuation_and_its_limits():
    assert kinds("> a\nb\n") == [("paragraph", 0, 2)]
    assert kinds("- a\nb\n") == [("paragraph", 0, 2)]
    assert kinds("> a\n    b\n") == [("paragraph", 0, 2)]            # 4 spaces cannot start code after a paragraph
    assert kinds("> a\n\nb\n") == [("paragraph", 0, 1), ("paragraph", 2, 3)]
    assert kinds("> a\n===\n") == [("paragraph", 0, 2)]              # no setext across a lazy line
    assert kinds("> ```\n> a\nb\n") == [("code_fence", 0, 2), ("paragraph", 2, 3)]  # a lazy line never continues a fence


def test_commonmark_lazy_line_with_list_marker_is_continuation():
    # CommonMark (commonmark.js): continuation text. markdown-it-py 4 deviates here (nested quote), so
    # this one is pinned instead of compared.
    assert kinds("> > b\n    - a\n") == [("paragraph", 0, 2)]


def test_list_interrupting_a_paragraph():
    assert kinds("a\n- b\n") == [("paragraph", 0, 1), ("paragraph", 1, 2)]
    assert kinds("a\n2. b\n") == [("paragraph", 0, 2)]               # ordered list must start at 1
    assert kinds("a\n1. b\n") == [("paragraph", 0, 1), ("paragraph", 1, 2)]
    assert kinds("a\n-\n") == [("heading", 0, 2)]                    # setext, not an empty item


def test_indented_code_in_items_and_quotes():
    assert kinds("-     code\n") == [("code_indented", 0, 1)]
    assert kinds("> a\n>\n>     code\n") == [("paragraph", 0, 1), ("code_indented", 2, 3)]
    assert kinds("- a\n\n      code\n") == [("paragraph", 0, 1), ("code_indented", 2, 3)]
    assert kinds("- a\n\n    para\n") == [("paragraph", 0, 1), ("paragraph", 2, 3)]


def test_fences():
    assert kinds("```\n~~~\n```\n") == [("code_fence", 0, 3)]
    assert kinds("````\n```\nx\n````\n") == [("code_fence", 0, 4)]
    assert kinds("```\nx\n") == [("code_fence", 0, 2)] and kinds("```\nx") == [("code_fence", 0, 2)]  # unclosed: to the end
    assert kinds("> ```\n> x\n\ny\n") == [("code_fence", 0, 2), ("paragraph", 3, 4)]   # the quote ends the fence
    assert kinds("```a`b\nx\n")[0][0] == "paragraph"                  # backtick fence info cannot hold backticks
    assert kinds("  ```\n  x\n  ```\n") == [("code_fence", 0, 3)]
    assert kinds("    ```\n") == [("code_indented", 0, 1)]


def test_html_blocks():
    assert kinds("<div>\na\n\nb\n") == [("html", 0, 2), ("paragraph", 3, 4)]
    assert kinds("<!-- a\n\nb -->\nc\n") == [("html", 0, 3), ("paragraph", 3, 4)]
    assert kinds("a\n<span>\n") == [("paragraph", 0, 2)]             # type 7 cannot interrupt a paragraph
    assert kinds("a\n<div>\n") == [("paragraph", 0, 1), ("html", 1, 2)]
    assert kinds("> <div>\n> x\n") == [("html", 0, 2)]


def test_line_endings_do_not_change_structure():
    src = "> a\n> ```\n> x\n> ```\n- b\n\n  c\n"
    base = kinds(src)
    assert kinds(src.replace("\n", "\r\n")) == base and kinds(src.replace("\n", "\r")) == base


def test_tabs():
    assert kinds("\tcode\n") == [("code_indented", 0, 1)]
    assert kinds(">\tcode\n") == [("paragraph", 0, 1)]  # `>` takes one column of the tab, two remain
    assert kinds("-\ta\n") == [("paragraph", 0, 1)]
    assert kinds("a\n\tb\n") == [("paragraph", 0, 2)]


def test_types_and_limits(assert_mdq_error):
    for bad in (None, 1, b"x"):
        with pytest.raises(TypeError):
            regions(bad)
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        regions("a" * (16 * 1024 * 1024 + 1))


def test_nesting_limit(assert_mdq_error):
    assert kinds("> " * 128 + "x\n") == [("paragraph", 0, 1)]
    with assert_mdq_error("MDQ_RESOURCE_LIMIT", line=1, column=257):
        regions("> " * 129 + "x\n")
    with assert_mdq_error("MDQ_RESOURCE_LIMIT", line=4):
        regions("a\n\n\n" + ">" * 1000 + "\n")
    src = "".join("  " * i + "- a\n" for i in range(100))
    assert len(regions(src)) == 100
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        regions("".join("  " * i + "- a\n" for i in range(300)))
    with assert_mdq_error("MDQ_RESOURCE_LIMIT", line=12):
        interp("x\n" * 9 + "\n" + "> " * 200, {}, first_line=2)


# ---------------------------------------------------------------- differential vs markdown-it

LINES = ["text", "more text", "", "", "> q", ">", "> ", ">> d", "- item", "-", "* x", "+ y", "1. one", "2. two", "10) ten",
         "  - nested", "    code", "      deep", "  text", "```", "~~~", "> ```", "- ```", "  ```", "# h", "## h ##", "===", "---",
         "***", "<div>", "</div>", "<!--", "-->", "<span>x</span>", "<p>", "  continued", "> - a", "- > q", "-   wide", "1.  two",
         "   > q", "  1. a", "- a\n  b", "> a\n> b", "x\n---", "x\n===", "- # h", "> # h", "1) a", "<pre>", "</pre>", "```py",
         "````", "\tcode", ">\tq", "-\tx", "1.\tx", "- \tcode", ">     code", "-     code", "- - a", "> > b", "- 1. x", "  ", "\t",
         "    ", "1. a\n   b"]


@given(st.lists(st.sampled_from(LINES), min_size=1, max_size=14))
def test_blocks_match_markdown_it(lines):
    src = "\n".join(lines) + "\n"
    assert mine(src) == leaf_blocks(src), src


@given(st.lists(st.sampled_from(LINES), min_size=1, max_size=14), st.sampled_from(["\n", "\r\n", "\r"]))
def test_blocks_match_markdown_it_with_any_line_end(lines, eol):
    src = eol.join(lines)
    if lines[-1].strip(" >") == "":
        # markdown-it drops a final empty quoted line that has no line end (`> ```\n>`);
        # CommonMark keeps it, so give that line its terminator for the comparison.
        src += eol
    assert mine(src) == leaf_blocks(src), repr(src)


@pytest.mark.parametrize("src", [
    "> a\n> b\n", "- a\n  b\n- c\n", "1. a\n\n   b\n2. c\n", ">     x\n", "-\n  a\n", "-\n\n  a\n", "- a\n - b\n  - c\n   - d\n    - e\n",
    "> - a\n> - b\n>\n> c\n", "```\n- a\n```\n", "- ```\n  a\n  ```\n- b\n", "    a\n\n    b\n", "- a\n\n\n- b\n", "1. a\n   ```\n   b\n   ```\n",
    "> a\n>\n>\tb\n", "<div>\n> a\n</div>\n", "a\n<!-- c -->\nb\n", "<p>\n\n- a\n", "- <div>\n  a\n  </div>\n", "***\n- a\n---\n",
    "> # h\n> ===\n", "- a\n===\n", "a\n\n===\n", "- a\n  ===\n", "10. a\n11. b\n", "1234567890. a\n", "  # h\n   ## i\n    ### j\n",
])
def test_curated_documents_match_markdown_it(src):
    assert mine(src) == leaf_blocks(src)


# ---------------------------------------------------------------- directives


def test_scan_positions_and_names():
    assert scan("a {{x}} b {{ y.z }}") == [
        {"start": 2, "end": 7, "name": "x", "line": 1, "column": 3},
        {"start": 10, "end": 19, "name": "y.z", "line": 1, "column": 11}]
    r = scan("> - {{a}}\n>   {{b}}\n", first_line=5)
    assert [(d["name"], d["line"], d["column"]) for d in r] == [("a", 5, 5), ("b", 6, 5)]


def test_scan_ignores_code_html_and_escapes_at_any_depth():
    for src in ["> ```\n> {{x}}\n> ```\n", "- ```\n  {{x}}\n  ```\n", "- a\n\n      {{x}}\n", "> <div>\n> {{x}}\n> </div>\n",
                "- <!-- {{x}} -->\n", "> `{{x}}`\n", "> a `b\n> {{x}}` c\n", "- `a\n  {{x}}`\n", "1. a\n\n       {{x}}\n",
                "> > ```\n> > {{x}}\n", "\\{{x}} `{{x}}`\n", "para\n\n    {{x}}\n", "```\n{{x}}\n"]:
        assert scan(src) == [], src


def test_scan_finds_text_inside_containers():
    for src in ["> a\n> {{x}}\n", "- a\n  {{x}}\n", "- a\n\n  {{x}}\n", "> > {{x}}\n", "- > {{x}}\n", "> a\n{{x}}\n", "1. {{x}}\n",
                "- a\n\n    {{x}}\n", "> - # {{x}}\n", "# {{x}}\n", "a\n{{x}}\n===\n", "  {{x}}\n"]:
        assert [d["name"] for d in scan(src)] == ["x"], src


def test_directive_split_across_a_code_span_is_not_a_directive():
    with pytest.raises(mdq.MdqError):
        scan("{{x`}}`")  # a code span cannot hide the closing braces: `{{x` is malformed
    assert [d["name"] for d in scan("`a` {{x}} `b`")] == ["x"]
    assert scan("`{{`x}}`") == []  # a span between the braces: the braces are not adjacent text


def test_malformed_after_a_good_directive(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=2, column=3):
        scan("{{x}}\n> {{ y z }}\n")
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=1, column=1):
        interp("{{\n", {})


def test_directives_cannot_span_lines_even_in_one_paragraph(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=1, column=3):
        scan("a {{x\n}}\n")


def test_scan_types_and_limits(assert_mdq_error):
    with pytest.raises(TypeError):
        scan(None)
    with pytest.raises(TypeError):
        scan("a", first_line="1")
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        scan("a" * (16 * 1024 * 1024 + 1))


# ---------------------------------------------------------------- interpolate through containers

V = {"s": "text", "e": ""}


@pytest.mark.parametrize("src", [
    "> ```\n> {{s}}\n> ```\n", "- ```\n  {{s}}\n  ```\n", "- a\n\n      {{s}}\n", "> <div>\n> {{s}}\n> </div>\n", "- <!-- {{s}} -->\n",
    "> `{{s}}`\n", "> a `b\n> {{s}}` c\n", "1. a\n\n       {{s}}\n", "> > ```\n> > {{s}}\n> > ```\n", "- - ```\n    {{s}}\n    ```\n",
])
def test_nested_code_and_html_stay_literal(src):
    assert interp(src, V) == src


@pytest.mark.parametrize("src,out", [
    ("> a {{s}}\n", "> a text\n"), ("- {{s}}\n  {{s}}\n", "- text\n  text\n"), ("> > {{s}}\n", "> > text\n"), ("> a\n{{s}}\n", "> a\ntext\n"),
    ("- a\n\n    {{s}}\n", "- a\n\n    text\n"), ("1. {{s}}\n", "1. text\n"), ("> - # {{s}}\n", "> - # text\n"),
    ("> ```\n> x\n> ```\n> {{s}}\n", "> ```\n> x\n> ```\n> text\n"), ("- ```\n  x\n  ```\n  {{s}}\n", "- ```\n  x\n  ```\n  text\n"),
])
def test_text_in_containers_is_expanded(src, out):
    assert interp(src, V) == out


def test_value_escaping_sees_container_prefixes():
    assert interp("> {{x}}\n", {"x": "- a"}) == "> \\- a\n"
    assert interp("- {{x}}\n", {"x": "# a"}) == "- \\# a\n"
    assert interp("> a\n> {{x}}\n", {"x": "> b"}) == "> a\n> \\> b\n"


# ---- expanded directives agree with markdown-it's view of text vs code, nesting included

X = {"x": "MARK"}
LINES2 = ["{{x}}", "text {{x}}", "", "", "> {{x}}", "> ```", "> a", ">", "- {{x}}", "- ```", "- a", "  {{x}}", "    {{x}}", "      {{x}}",
          "```", "~~~", "  ```", "<div>", "</div>", "<!--", "-->", "`{{x}}`", "`a", "b` {{x}}", "<b x='{{x}}'>", "\\{{y}}", "1. {{x}}", "1. ```",
          "   {{x}}", "> > {{x}}", "- - {{x}}", "# {{x}}", "===", "---", "<p>", "\t{{x}}", "> - {{x}}", "  - ```", "<span>{{x}}</span>"]


@given(st.lists(st.sampled_from(LINES2), min_size=1, max_size=12))
def test_expanded_directives_match_markdown_it(lines):
    doc = "\n".join(lines) + "\n"
    live, literal = live_and_literal(doc)
    out = interp(doc, X)
    assert out.count("MARK") == live, (doc, out)
    assert out.count("{{x}}") == literal, (doc, out)
    assert len(scan(doc)) == live


@given(st.lists(st.sampled_from(LINES), min_size=1, max_size=10))
def test_documents_without_directives_are_unchanged(lines):
    doc = "\n".join(l for l in lines if "{{" not in l) + "\n"
    assert interp(doc, {}) == doc
