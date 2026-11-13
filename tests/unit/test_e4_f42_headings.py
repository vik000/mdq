"""Epic 4 / E4.2 (F05): heading text, matching, slugs and anchors (spec section 3.1).

- `heading_plain_text(inline, atx=False)`: the rendered plain text of a heading's inline Markdown
  (markup stripped, entities decoded, NFC, whitespace collapsed to one space, trimmed). `atx=True`
  first removes an optional closing `###` sequence. Differential against markdown-it-py.
- `heading_match(headings, target)`: index of the FIRST heading whose plain text equals the NFC,
  whitespace-collapsed target (case-sensitive), else None.
- `slugify(text)`: NFC, Unicode full case folding, keep letters/marks/numbers/`-`/`_`, other runs
  become one `-`, trim `-`, empty gives `section`. `slug_dedupe`, `percent_encode_anchor`. G33.
"""
import bisect
import html.entities
import unicodedata
import urllib.parse

import pytest
from hypothesis import given, strategies as st
from markdown_it import MarkdownIt

import mdq
from mdq import _internal as mi
from oracles import unitab

pytestmark = pytest.mark.unit

hpt = mi.heading_plain_text


# ---------------------------------------------------------------- plain text: pinned cases
@pytest.mark.parametrize("src,expected", [
    ("Plain", "Plain"),
    ("*Foo* &amp; Bar", "Foo & Bar"),
    ("**bold** and _em_ and ***both***", "bold and em and both"),
    ("`code *not em*` x", "code *not em* x"),
    ("`` a`b `` c", "a`b c"),
    ("[link *text*](http://x.y \"title\") after", "link text after"),
    ("[a](<b c> 'd') e", "a e"),
    ("[a](b(c)d) e", "a e"),
    ("[a](b\\)) e", "a e"),
    ("![alt *text*](img.png) z", "alt text z"),
    ("[![img](i.png)](http://x)", "img"),
    ("[a [b](c) d](e)", "[a b d](e)"),
    ("<http://a.b/c> and <me@x.org>", "http://a.b/c and me@x.org"),
    ("a <b>bold</b> <br/> c", "a bold c"),
    ("<!-- c --> x", "x"),
    ("\\*not em\\* and \\[x\\]", "*not em* and [x]"),
    ("snake_case_name and 2*3*4", "snake_case_name and 234"),
    ("*a", "*a"), ("a*", "a*"), ("**a*", "*a"), ("_a", "_a"), ("a _ b", "a _ b"),
    ("[not a link] [ref] [x][y]", "[not a link] [ref] [x][y]"),
    ("[a](b", "[a](b"), ("[a]()", "a"), ("[a](  )", "a"),
    ("&copy; &#65; &#x42; &#0; &bogus; &amp &#; &#xZ;", "© A B � &bogus; &amp &#; &#xZ;"),
    ("&nbsp;x&nbsp;", "x"),
    ("a\\\nb", "a b"), ("a  \nb", "a b"),
    ("~~gone~~ ==marked== ~one~ =x=", "gone marked ~one~ =x="),
    ("a ~~b *c~~ d* e", "a b *c d* e"),
    ("Ünïcödé é", "Ünïcödé é"),
    ("  lots   of\t space \n here ", "lots of space here"),
    ("", ""), ("   ", ""),
    ("$x$ [^1] {{v}}", "$x$ [^1] {{v}}"),
])
def test_plain_text(src, expected):
    assert hpt(src) == expected


def test_nfc_and_whitespace_and_case():
    assert hpt("é") == "é"
    assert hpt("A B C") == "A B C"
    assert hpt("MiXeD") == "MiXeD"


@pytest.mark.parametrize("src,expected", [
    ("Title", "Title"), ("Title #", "Title"), ("Title ###   ", "Title"), ("Title#", "Title#"),
    ("Title \\#", "Title #"), ("#", ""), ("###", ""), ("Title # x", "Title # x"), ("Title #\t", "Title"),
    ("*a* ##", "a"), ("`x #` #", "x #"),
])
def test_atx_closing_sequence(src, expected):
    assert hpt(src, atx=True) == expected


def test_atx_flag_is_off_by_default():
    assert hpt("Title ##") == "Title ##"


def test_type_errors():
    for bad in (None, b"x", 1):
        with pytest.raises(TypeError):
            hpt(bad)
    with pytest.raises(TypeError):
        hpt("x", atx=1)


# ---------------------------------------------------------------- plain text: differential against markdown-it
MD = MarkdownIt("commonmark")


def oracle_plain(children):
    out = []
    for t in children:
        if t.type in ("text", "text_special", "code_inline"):
            out.append(t.content)
        elif t.type in ("softbreak", "hardbreak"):
            out.append(" ")
        elif t.type == "image":
            out.append(oracle_plain(t.children))
    return "".join(out)


def oracle(src):
    toks = MD.parseInline(src)
    text = oracle_plain(toks[0].children) if toks else ""
    return " ".join(unicodedata.normalize("NFC", text).split())


PIECES = ["a", "b", " ", "  ", "*", "**", "***", "_", "__", "[", "]", "(", ")", "![", "<", ">", "&amp;", "&#65;", "&copy;",
          "&", ";", "#", "\\", "\\*", "`", "``", "'", '"', "\n", "http://x.y", "<http://x.y>", "<b>", "</b>", "<a@b.c>",
          ":", "/", ".", "@", "-", "1", "é", "(u)", "[x](y)", "[x](y 't')", "![i](s)", "ab_cd", "x*y*z", " "]


@given(st.lists(st.sampled_from(PIECES), max_size=14).map("".join))
def test_differential_against_markdown_it(src):
    if "\n\n" in src:
        return  # a blank line would end the heading
    assert hpt(src) == oracle(src), repr(src)


# ---------------------------------------------------------------- matching
def test_match_first_duplicate_wins():
    hs = ["Intro", "Setup", "*Setup*", "Done"]
    assert mi.heading_match(hs, "Setup") == 1
    assert mi.heading_match(hs, "Done") == 3
    assert mi.heading_match(hs, "Nope") is None
    assert mi.heading_match([], "x") is None


def test_match_rules():
    assert mi.heading_match(["*Foo* &amp; Bar"], "Foo & Bar") == 0
    assert mi.heading_match(["Foo"], "foo") is None            # case-sensitive
    assert mi.heading_match(["Foo   Bar"], "Foo Bar") == 0      # whitespace collapsed
    assert mi.heading_match(["Foo Bar"], "  Foo \t Bar ") == 0
    assert mi.heading_match(["é"], "é") == 0         # NFC
    assert mi.heading_match(["a ##", "b"], "a", atx=True) == 0
    assert mi.heading_match(["a ##"], "a") is None
    assert mi.heading_match(["`x`"], "x") == 0 and mi.heading_match(["`x`"], "`x`") is None


def test_match_type_errors():
    with pytest.raises(TypeError):
        mi.heading_match("abc", "a")
    with pytest.raises(TypeError):
        mi.heading_match([1], "a")
    with pytest.raises(TypeError):
        mi.heading_match(["a"], None)


# ---------------------------------------------------------------- slugify
@pytest.mark.parametrize("text,slug", [
    ("Hello, World!", "hello-world"),
    ("  Spaced   out  ", "spaced-out"),
    ("a_b-c", "a_b-c"),
    ("A -- B", "a----b"),
    ("-lead and trail-", "lead-and-trail"),
    ("Straße", "strasse"),
    ("İstanbul", "i̇stanbul"),
    ("Ünïcödé", "ünïcödé"),
    ("你好 世界", "你好-世界"),
    ("Café au lait", "café-au-lait"),
    ("é", "é"),
    ("x́y", "x́y"),
    ("v1.2.3", "v1-2-3"),
    ("日本語のタイトル", "日本語のタイトル"),
    ("Ω Σ ς", "ω-σ-σ"),
    ("😀", "section"), ("", "section"), ("   ", "section"), ("---", "section"), ("!?", "section"), ("_", "_"),
    ("a😀b", "a-b"),
    ("C++ & C#", "c-c"),
])
def test_slugify(text, slug):
    assert mi.slugify(text) == slug


def fold_oracle(text):
    out, pend = [], False
    for ch in unicodedata.normalize("NFC", text):
        for f in unitab.CASEFOLD.get(ord(ch), ch):
            i = bisect.bisect_right(unitab.KEEP, (ord(f), 0x110000)) - 1
            keep = f in "-_" or (i >= 0 and unitab.KEEP[i][0] <= ord(f) <= unitab.KEEP[i][1])
            if keep:
                if pend and out:
                    out.append("-")
                pend = False
                out.append(f)
            else:
                pend = True
    return "".join(out).strip("-") or "section"


@given(st.text(max_size=30))
def test_slugify_matches_independent_implementation(text):
    s = mi.slugify(text)
    assert s == fold_oracle(text)


@given(st.text(max_size=30))
def test_slug_shape(text):
    s = mi.slugify(text)
    assert s and not s.startswith("-") and not s.endswith("-")


def test_frozen_tables_agree_with_python_on_common_scripts():
    for ch in "aZ09éßİΩς你日あ😀 !-_́ €":
        live = unicodedata.category(ch)[0] in "LMN"
        i = bisect.bisect_right(unitab.KEEP, (ord(ch), 0x110000)) - 1
        assert live == (i >= 0 and unitab.KEEP[i][0] <= ord(ch) <= unitab.KEEP[i][1]), ch
        assert unitab.CASEFOLD.get(ord(ch), ch) == ch.casefold(), ch


# ---------------------------------------------------------------- dedupe and anchors
def test_dedupe():
    assert mi.slug_dedupe(["a", "a", "a", "b", "a"]) == ["a", "a-2", "a-3", "b", "a-4"]
    assert mi.slug_dedupe([]) == []
    assert mi.slug_dedupe(["a", "a-2", "a"]) == ["a", "a-2", "a-3"]
    assert mi.slug_dedupe(["a", "a", "a-2"]) == ["a", "a-2", "a-2-2"]
    assert mi.slug_dedupe(["a", "b"], ["a"]) == ["a-2", "b"]
    assert mi.slug_dedupe(["a", "a"], ["a", "a-2"]) == ["a-3", "a-4"]
    assert mi.slug_dedupe(["section", "section"]) == ["section", "section-2"]


@given(st.lists(st.sampled_from(["a", "b", "a-2", "section"]), max_size=12), st.lists(st.sampled_from(["a", "a-2", "b-2"]), max_size=3))
def test_dedupe_properties(slugs, reserved):
    out = mi.slug_dedupe(slugs, reserved)
    assert len(out) == len(slugs) and len(set(out)) == len(out) and not (set(out) & set(reserved))
    assert out == mi.slug_dedupe(slugs, reserved)
    if not reserved:
        assert out[:1] == slugs[:1]


def test_dedupe_type_errors():
    for bad in ("ab", [1], None):
        with pytest.raises(TypeError):
            mi.slug_dedupe(bad)
    with pytest.raises(TypeError):
        mi.slug_dedupe(["a"], "a")


@pytest.mark.parametrize("text,enc", [
    ("abc-DEF_1.2~", "abc-DEF_1.2~"), ("é", "%C3%A9"), ("你好", "%E4%BD%A0%E5%A5%BD"), ("😀", "%F0%9F%98%80"),
    ("a b", "a%20b"), ("a#b%c", "a%23b%25c"), ("", ""),
])
def test_percent_encode(text, enc):
    assert mi.percent_encode_anchor(text) == enc


@given(st.text(max_size=20))
def test_percent_encode_round_trip(text):
    enc = mi.percent_encode_anchor(text)
    assert enc.isascii() and urllib.parse.unquote(enc) == text
