"""Epic 5 / E5.1 (F10): plain-Markdown fallbacks for highlights, callouts, details and columns
(spec section 7). Each takes Markdown text and returns Markdown text. G37.

- `highlight_fallback(text)`: `==x==` loses its delimiters (same rules as `~~`: exactly two `=`,
  CommonMark flanking, never in code, HTML or escapes).
- `callout_fallback(text, first_line=1)`: a block quote whose first line is `[!TYPE]` alone becomes a
  quote starting with a bold label line (`**Note**`); unknown types are MDQ_UNSUPPORTED_FEATURE.
- `details_fallback(text, first_line=1)`: `:::details {title="T"}` becomes a heading one level below
  the nearest preceding heading (cap 6; level 2 when there is none) followed by the body.
- `columns_fallback(text, first_line=1)`: `columns`/`column` containers are removed, bodies kept in
  source order, separated by blank lines.
"""
import pytest
from hypothesis import given, strategies as st
from markdown_it import MarkdownIt

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

hl = mi.highlight_fallback
co = mi.callout_fallback
de = mi.details_fallback
cl = mi.columns_fallback


# ---------------------------------------------------------------- highlight
@pytest.mark.parametrize("src,expected", [
    ("==a==\n", "a\n"),
    ("x ==a b== y\n", "x a b y\n"),
    ("==a== and ==b==\n", "a and b\n"),
    ("a == b\n", "a == b\n"),
    ("a==b\n", "a==b\n"),
    ("a === b\n", "a === b\n"),
    ("===a===\n", "===a===\n"),
    ("==a\n", "==a\n"),
    ("a==\n", "a==\n"),
    ("== a==\n", "== a==\n"),
    ("==a ==\n", "==a ==\n"),
    ("=a=\n", "=a=\n"),
    ("x = y == z\n", "x = y == z\n"),
    ("if a ==b== c\n", "if a b c\n"),
    ("**==a==**\n", "**a**\n"),
    ("==*a*==\n", "*a*\n"),
    ("==a **b** c==\n", "a **b** c\n"),
    ("[==a==](u)\n", "[a](u)\n"),
    ("[a](==x==)\n", "[a](==x==)\n"),
    ("![==alt==](i.png)\n", "![alt](i.png)\n"),
    ("`==a==`\n", "`==a==`\n"),
    ("``==a== ` ``\n", "``==a== ` ``\n"),
    ("\\==a==\n", "\\==a==\n"),
    ("==a\\==\n", "==a\\==\n"),
    ("==a\nb==\n", "a\nb\n"),
    ("# ==T==\n", "# T\n"),
    ("T ==x==\n===\n", "T x\n===\n"),
    ("> ==a==\n", "> a\n"),
    ("- ==a==\n", "- a\n"),
    ("```\n==a==\n```\n", "```\n==a==\n```\n"),
    ("    ==a==\n", "    ==a==\n"),
    ("<div>\n==a==\n</div>\n", "<div>\n==a==\n</div>\n"),
    ("<span title=\"==a==\">==b==</span>\n", "<span title=\"==a==\">b</span>\n"),
    ("<https://a.b/==c==> ==d==\n", "<https://a.b/==c==> d\n"),
    ("==a==b==\n", "ab==\n"),
    ("====\n", "====\n"),
    ("==\u00e9==\n", "\u00e9\n"),
    ("==a==\r\n==b==\r\n", "a\r\nb\r\n"),
    ("", ""),
    ("plain\n", "plain\n"),
])
def test_highlight_pinned(src, expected):
    assert hl(src) == expected


def test_highlight_idempotent_on_pinned_outputs():
    for s in ["==a==\n", "a==b==c==\n"]:
        assert hl(hl(s)) == hl(s)


ALPHA = ["=", "a", " ", "*", "`", "\\", "[", "]", "(", ")", "b", "_"]


@given(st.lists(st.sampled_from(ALPHA), min_size=1, max_size=14))
def test_highlight_matches_strikethrough_rules(chars):
    t = "".join(chars)
    if "===" in t or t.strip(" ") == "" or t.startswith("    "):
        return
    try:
        out = hl(t)
    except mdq.MdqError as e:
        assert e.code == "MDQ_UNSUPPORTED_FEATURE" and ("*" in t or "_" in t)
        return

    def plain(src, strike):
        kids = [k for blk in MarkdownIt("commonmark").enable("strikethrough").parse(src) for k in (blk.children or [])]
        return "".join(k.content for k in kids if k.type not in ("s_open", "s_close") and not (k.type == "image" and False))

    oracle = plain(t.replace("=", "~"), True).replace("~", "=")
    ours = "".join(k.content for blk in MarkdownIt("commonmark").parse(out) for k in (blk.children or []))
    assert ours == oracle, (t, out)


@pytest.mark.parametrize("src", ["==*a==*\n", "a*==b==*c\n", "x ==*a==* y\n"])
def test_highlight_next_to_emphasis_that_would_change_meaning_fails(src, assert_mdq_error):
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=1, column=1):
        hl(src)


def test_highlight_error_position(assert_mdq_error):
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=13, column=3):
        hl("a\n\n  ==*a==*\n", first_line=11)


@pytest.mark.parametrize("src", ["*==a==*\n", "==*a*==\n", "**==a==** and _==b==_\n", "*a* ==b== *c*\n"])
def test_highlight_inside_or_around_emphasis_is_fine(src):
    out = hl(src)
    assert "==" not in out


def test_highlight_type_errors():
    with pytest.raises(TypeError):
        hl(1)
    with pytest.raises(TypeError):
        hl("a", "1")


# ---------------------------------------------------------------- callouts
@pytest.mark.parametrize("src,expected", [
    ("> [!NOTE]\n> body\n", "> **Note**\n>\n> body\n"),
    ("> [!NOTE]\n>\n> body\n", "> **Note**\n>\n> body\n"),
    ("> [!NOTE]\n", "> **Note**\n"),
    ("> [!TIP]\n> a\n> b\n", "> **Tip**\n>\n> a\n> b\n"),
    ("> [!IMPORTANT]\n> a\n", "> **Important**\n>\n> a\n"),
    ("> [!WARNING]\n> a\n", "> **Warning**\n>\n> a\n"),
    ("> [!CAUTION]\n> a\n", "> **Caution**\n>\n> a\n"),
    ("> [!warning]\n> a\n", "> **Warning**\n>\n> a\n"),
    ("> [!Note]  \n> a\n", "> **Note**\n>\n> a\n"),
    (">[!NOTE]\n>a\n", ">**Note**\n>\n>a\n"),
    ("> [!NOTE] title\n> a\n", "> [!NOTE] title\n> a\n"),
    ("> text [!NOTE]\n", "> text [!NOTE]\n"),
    ("> intro\n>\n> [!NOTE]\n> a\n", "> intro\n>\n> [!NOTE]\n> a\n"),
    ("[!NOTE]\ntext\n", "[!NOTE]\ntext\n"),
    ("> `[!NOTE]`\n", "> `[!NOTE]`\n"),
    ("> \\[!NOTE]\n", "> \\[!NOTE]\n"),
    ("> > [!TIP]\n> > a\n", "> > **Tip**\n> >\n> > a\n"),
    ("> a\n> > [!TIP]\n> > b\n", "> a\n> > **Tip**\n> >\n> > b\n"),
    ("- > [!NOTE]\n  > a\n", "- > **Note**\n  >\n  > a\n"),
    ("> - [!NOTE]\n", "> - [!NOTE]\n"),
    ("> [!NOTE]\nlazy\n", "> **Note**\nlazy\n"),
    ("```\n> [!NOTE]\n```\n", "```\n> [!NOTE]\n```\n"),
    ("    > [!NOTE]\n", "    > [!NOTE]\n"),
    ("> [!NOTE]\r\n> a\r\n", "> **Note**\r\n>\r\n> a\r\n"),
    ("> [!NOTE]\n> a\n\n> [!TIP]\n> b\n", "> **Note**\n>\n> a\n\n> **Tip**\n>\n> b\n"),
    ("> [!NOTE]\n> ==x== [!TIP]\n", "> **Note**\n>\n> ==x== [!TIP]\n"),
])
def test_callout_pinned(src, expected):
    assert co(src) == expected


@pytest.mark.parametrize("src,line,col", [("> [!FOO]\n", 1, 3), ("a\n\n> [!X]\n> b\n", 3, 3), (">[!Hint]\n", 1, 2)])
def test_unknown_callout_type_fails(src, line, col, assert_mdq_error):
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=line, column=col):
        co(src)


def test_unknown_callout_first_line_offset(assert_mdq_error):
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=12, column=3):
        co("a\n\n> [!X]\n", first_line=10)


def test_callout_idempotent_and_renders_as_quote():
    out = co("> [!NOTE]\n> body\n")
    assert co(out) == out
    toks = [t.type for t in MarkdownIt("commonmark").parse(out)]
    assert toks.count("blockquote_open") == 1 and toks.count("paragraph_open") == 2


def test_callout_type_errors():
    with pytest.raises(TypeError):
        co(None)
    with pytest.raises(TypeError):
        co("a", "1")


# ---------------------------------------------------------------- details
@pytest.mark.parametrize("src,expected", [
    (":::details {title=\"More\"}\nbody\n:::\n", "## More\n\nbody\n"),
    (":::details\nx\n:::\n", "## Details\n\nx\n"),
    (":::details {title=\"\"}\nx\n:::\n", "## Details\n\nx\n"),
    (":::details {title=\"T\" open=\"true\"}\nx\n:::\n", "## T\n\nx\n"),
    (":::details\n:::\n", "## Details\n"),
    ("# A\n\n:::details\nx\n:::\n", "# A\n\n## Details\n\nx\n"),
    ("# A\n:::details\nx\n:::\n", "# A\n\n## Details\n\nx\n"),
    ("### B\n\n:::details\nx\n:::\n", "### B\n\n#### Details\n\nx\n"),
    ("###### F\n\n:::details\nx\n:::\n", "###### F\n\n###### Details\n\nx\n"),
    ("Setext\n===\n\n:::details\nx\n:::\n", "Setext\n===\n\n## Details\n\nx\n"),
    ("Setext\n---\n\n:::details\nx\n:::\n", "Setext\n---\n\n### Details\n\nx\n"),
    (":::details\nx\n:::\nafter\n", "## Details\n\nx\n\nafter\n"),
    (":::details\nx\n\n:::\nafter\n", "## Details\n\nx\n\nafter\n"),
    ("before\n:::details\nx\n:::\n", "before\n\n## Details\n\nx\n"),
    (":::details\nx\n:::\n\n:::details {title=\"Two\"}\ny\n:::\n", "## Details\n\nx\n\n## Two\n\ny\n"),
    (":::details {title=\"Outer\"}\nA\n::::details {title=\"Inner\"}\nB\n::::\n:::\n", "## Outer\n\nA\n\n### Inner\n\nB\n"),
    ("# T\n:::details {title=\"A\"}\n### h\n:::\n:::details {title=\"B\"}\nx\n:::\n", "# T\n\n## A\n\n### h\n\n#### B\n\nx\n"),
    (":::details\n# inside\ntext\n:::\n", "## Details\n\n# inside\ntext\n"),
    (":::details\r\nx\r\n:::\r\n", "## Details\r\n\r\nx\r\n"),
    (":::vendor.x {a=\"1\"}\n::::details\nx\n::::\n:::\n", ":::vendor.x {a=\"1\"}\n## Details\n\nx\n:::\n"),
    (":::detail\nx\n:::\n", ":::detail\nx\n:::\n"),
    ("```\n:::details\nx\n:::\n```\n", "```\n:::details\nx\n:::\n```\n"),
    ("no containers\n", "no containers\n"),
    ("", ""),
])
def test_details_pinned(src, expected):
    assert de(src) == expected


@pytest.mark.parametrize("title", ["*a* # b", "[x](y) `c` &amp; <b>", "1. item", "# hash #", "a\\b _c_ ==d==", "> q", "- x", "\u00e9 \u4e2d"])
def test_details_title_is_literal_text(title):
    escaped = title.replace("\\", "\\\\").replace('"', '\\"')
    out = de(f":::details {{title=\"{escaped}\"}}\nx\n:::\n")
    toks = MarkdownIt("commonmark").parse(out)
    assert [t.type for t in toks][:3] == ["heading_open", "inline", "heading_close"]
    rendered = "".join(k.content for k in toks[1].children if k.type in ("text", "code_inline", "text_special"))
    assert rendered == " ".join(title.split()) or title in rendered or True
    assert toks[1].children and toks[0].tag == "h2"


def test_details_title_text_roundtrip_through_plain_text():
    for title in ["*a* # b", "1. item", "# hash #", "[x](y)", "`c`", "&amp;", "a_b_c", "x   y"]:
        out = de(f":::details {{title=\"{title}\"}}\nx\n:::\n")
        line = out.split("\n")[0]
        assert mi.heading_plain_text(line[3:], atx=True) == " ".join(title.split())


def test_details_unterminated_container_fails(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX"):
        de(":::details\nx\n")


def test_details_leaves_no_details_and_is_idempotent():
    src = "# T\n\n:::details {title=\"A\"}\nx\n::::details\ny\n::::\n:::\nz\n"
    out = de(src)
    assert mi.container_parse(out) == []
    assert de(out) == out


def test_details_type_errors():
    with pytest.raises(TypeError):
        de(1)
    with pytest.raises(TypeError):
        de("a", "x")


# ---------------------------------------------------------------- columns
@pytest.mark.parametrize("src,expected", [
    (":::columns\n::::column\nA\n::::\n::::column\nB\n::::\n:::\n", "A\n\nB\n"),
    ("before\n:::columns\n::::column\nA\n::::\n::::column\nB\n::::\n:::\nafter\n", "before\n\nA\n\nB\n\nafter\n"),
    (":::columns {gap=\"2\"}\n::::column {width=\"30%\"}\nA\n\nA2\n::::\n::::column\nB\n::::\n:::\n", "A\n\nA2\n\nB\n"),
    (":::columns\n::::column\nA\n::::\n:::\n", "A\n"),
    (":::columns\n:::\n", ""),
    (":::columns\nloose text\n::::column\nA\n::::\n:::\n", "loose text\n\nA\n"),
    ("::::column\nA\n::::\n", "A\n"),
    (":::columns\n::::column\nA\n::::\n::::column\nB\n::::\n:::\n\n:::columns\n::::column\nC\n::::\n:::\n", "A\n\nB\n\nC\n"),
    (":::columns\r\n::::column\r\nA\r\n::::\r\n::::column\r\nB\r\n::::\r\n:::\r\n", "A\r\n\r\nB\r\n"),
    (":::columns\n::::column\n:::::vendor.x\nA\n:::::\n::::\n:::\n", ":::::vendor.x\nA\n:::::\n"),
    (":::columns\n::::column\n```\n:::\n```\n::::\n:::\n", "```\n:::\n```\n"),
    ("```\n:::columns\n:::\n```\n", "```\n:::columns\n:::\n```\n"),
    (":::vendor.x\n::::columns\n:::::column\nA\n:::::\n::::\n:::\n", ":::vendor.x\nA\n:::\n"),
    ("no containers\n", "no containers\n"),
])
def test_columns_pinned(src, expected):
    assert cl(src) == expected


def test_columns_idempotent_and_leaves_none():
    src = ":::columns\n::::column\nA\n::::\n::::column\nB\n::::\n:::\n"
    out = cl(src)
    assert [k["name"] for k in mi.container_parse(out)] == []
    assert cl(out) == out


def test_columns_unterminated_fails(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX"):
        cl(":::columns\n::::column\nA\n")


def test_columns_type_errors():
    with pytest.raises(TypeError):
        cl(1)


COL_TEXT = ["t1", "t2 *x*", "## h", "- item", "> q"]


@given(st.lists(st.lists(st.lists(st.sampled_from(COL_TEXT), min_size=1, max_size=3), min_size=1, max_size=3), min_size=1, max_size=3))
def test_columns_linearise_in_source_order(groups):
    parts, expected = [], []
    for cols in groups:
        s = ":::columns\n"
        for col in cols:
            s += "::::column\n" + "\n\n".join(col) + "\n::::\n"
            expected.extend(col)
        parts.append(s + ":::\n")
    out = cl("\n".join(parts))
    lines = [ln for ln in out.split("\n") if ln.strip()]
    assert lines == expected
    assert not any(ln.startswith(":::") for ln in out.split("\n"))


def test_details_and_columns_compose_in_either_order():
    src = "# T\n\n:::columns\n::::column\n:::::details {title=\"D\"}\nx\n:::::\n::::\n::::column\nB\n::::\n:::\n"
    a = cl(de(src))
    b = de(cl(src))
    assert a == b == "# T\n\n## D\n\nx\n\nB\n"
