"""Epic 5 / E5.2a (F10): maths fallback and footnotes (spec section 7). G38.

- `math_fallback(text, first_line=1)`: `$...$` / `$$...$$` inline maths becomes a code span; a line
  holding only `$$` opens and closes a display block that becomes a fenced ```math block. Pandoc rules:
  the opening `$` is followed by a non-blank, the first unescaped `$` closes, it must follow a
  non-blank and not be followed by a digit, and its run length must equal the opener's.
- `footnote_check(text, first_line=1)`: labels in order of first reference; undefined reference is
  MDQ_UNRESOLVED_REFERENCE, a label defined twice MDQ_DUPLICATE_ID; unused definitions are allowed.
- `footnote_rename(text, scope)`: `[^id]` -> `[^scope--id]` for references and definitions.
- `import_expand(..., footnotes=True)`: every embedded file gets its own scope (its encoded path,
  `~N` for the Nth embedding of the same path); the root file keeps its labels.
"""
import re

import pytest
from hypothesis import given, strategies as st
from markdown_it import MarkdownIt

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

mf = mi.math_fallback
fc = mi.footnote_check
fr = mi.footnote_rename
MD = MarkdownIt("commonmark")


def codes(src):
    return re.findall(r"<code[^>]*>(.*?)</code>", MD.render(src), re.S)


# ---------------------------------------------------------------- inline maths
@pytest.mark.parametrize("src,expected", [
    ("$a$\n", "`a`\n"),
    ("x $a+b$ y\n", "x `a+b` y\n"),
    ("$a$ and $b$\n", "`a` and `b`\n"),
    ("$$a$$\n", "`a`\n"),
    ("a$b$c\n", "a`b`c\n"),
    ("$a b$\n", "`a b`\n"),
    ("$ a$\n", "$ a$\n"),
    ("$a $\n", "$a $\n"),
    ("$a$1\n", "$a$1\n"),
    ("$a$ 1\n", "`a` 1\n"),
    ("costs $5 and $6\n", "costs $5 and $6\n"),
    ("costs $5 and $6 or $x$\n", "costs $5 and $6 or `x`\n"),
    ("$a\n", "$a\n"),
    ("a$\n", "a$\n"),
    ("$\n", "$\n"),
    ("$$$a$$$\n", "$$$a$$$\n"),
    ("$a$$b$\n", "$a$$b$\n"),
    ("$$x $ y$$ z\n", "$$x $ y$$ z\n"),
    ("\\$a$\n", "\\$a$\n"),
    ("$a\\$b$\n", "`a\\$b`\n"),
    ("$a\\\\$\n", "`a\\\\`\n"),
    ("$\\alpha_1^2$\n", "`\\alpha_1^2`\n"),
    ("$a*b$ and $c*d$\n", "`a*b` and `c*d`\n"),
    ("$a\nb$\n", "`a\nb`\n"),
    ("# T $x$\n", "# T `x`\n"),
    ("T $x$\n===\n", "T `x`\n===\n"),
    ("[$x$](u)\n", "[`x`](u)\n"),
    ("[a](http://x/$1$)\n", "[a](http://x/$1$)\n"),
    ("[a](<u $x$>)\n", "[a](<u $x$>)\n"),
    ("`$a$`\n", "`$a$`\n"),
    ("``$a$ ` ``\n", "``$a$ ` ``\n"),
    ("<http://x/$a$>\n", "<http://x/$a$>\n"),
    ("<span title=\"$a$\">$b$</span>\n", "<span title=\"$a$\">`b`</span>\n"),
    ("    $a$\n", "    $a$\n"),
    ("```\n$a$\n```\n", "```\n$a$\n```\n"),
    ("<div>\n$a$\n</div>\n", "<div>\n$a$\n</div>\n"),
    ("> $a$\n", "> `a`\n"),
    ("- $a$\n", "- `a`\n"),
    ("$a$\r\n$b$\r\n", "`a`\r\n`b`\r\n"),
    ("no maths\n", "no maths\n"),
    ("", ""),
    ("$a$", "`a`"),
])
def test_inline_maths(src, expected):
    assert mf(src) == expected


@pytest.mark.parametrize("src,expected", [
    ("$a`b$\n", "``a`b``\n"),
    ("$`a$\n", "`` `a ``\n"),
    ("$a`$\n", "`` a` ``\n"),
    ("$a`b``c$\n", "```a`b``c```\n"),
    ("$```$\n", "` ``` `\n"),
])
def test_backticks_inside_maths_choose_a_safe_code_span(src, expected):
    out = mf(src)
    assert out == expected
    assert codes(out) == [src.strip()[1:-1]]


def test_inline_maths_renders_as_code_with_the_exact_body():
    src = "Let $a<b & c*d_e$ hold, so $x\\_1$.\n"
    assert codes(mf(src)) == ["a&lt;b &amp; c*d_e", "x\\_1"]


def test_math_in_multi_line_quote_keeps_prefixes():
    src = "> a $x\n> = y$ b\n"
    out = mf(src)
    assert out == "> a `x\n> = y` b\n"
    assert codes(out) == ["x = y"]


# ---------------------------------------------------------------- display maths
@pytest.mark.parametrize("src,expected", [
    ("$$\nx\n$$\n", "```math\nx\n```\n"),
    ("$$\r\nx\r\n$$\r\n", "```math\r\nx\r\n```\r\n"),
    ("text\n$$\nx\n$$\nmore\n", "text\n```math\nx\n```\nmore\n"),
    ("$$  \nx\n$$ \n", "```math  \nx\n``` \n"),
    ("  $$\nx\n  $$\n", "  ```math\nx\n  ```\n"),
    ("$$\n$$\n", "```math\n```\n"),
    ("$$\na\n\nb\n$$\n", "```math\na\n\nb\n```\n"),
    ("$$\na\n- b\n# c\n> d\n$$\n", "```math\na\n- b\n# c\n> d\n```\n"),
    ("$$\n$x$\n$$\n", "```math\n$x$\n```\n"),
    ("$$\na ``` b\n$$\n", "````math\na ``` b\n````\n"),
    ("$$\n```\n$$\n", "````math\n```\n````\n"),
    ("> $$\n> x\n> $$\n", "> ```math\n> x\n> ```\n"),
    ("- a\n\n  $$\n  x\n  $$\n", "- a\n\n  ```math\n  x\n  ```\n"),
    ("$$\na\n$$\n\n$$\nb\n$$\n", "```math\na\n```\n\n```math\nb\n```\n"),
])
def test_display_maths(src, expected):
    assert mf(src) == expected


def test_display_maths_renders_as_a_math_code_block():
    out = mf("a\n$$\nx = 1\n\n- y\n$$\nb\n")
    html = MD.render(out)
    assert '<pre><code class="language-math">x = 1\n\n- y\n</code></pre>' in html


def test_display_block_in_quote_and_list_renders_inside_them():
    html = MD.render(mf("> $$\n> a\n> $$\n"))
    assert html.startswith("<blockquote>\n<pre><code class=\"language-math\">a\n</code></pre>")
    html = MD.render(mf("- item\n\n  $$\n  a\n  $$\n"))
    assert '<pre><code class="language-math">a\n</code></pre>' in html


def test_dollar_lines_inside_code_are_not_display_maths():
    src = "```\n$$\nx\n```\n\n    $$\n"
    assert mf(src) == src
    src = "<div>\n$$\n</div>\n"
    assert mf(src) == src


def test_display_content_is_not_scanned_for_inline_maths_but_neighbours_are():
    assert mf("$a$\n$$\n$b$\n$$\n$c$\n") == "`a`\n```math\n$b$\n```\n`c`\n"


def test_unclosed_display_is_an_error_at_the_opener(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=3, column=1):
        mf("a\n\n$$\nx\n")
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=13, column=3):
        mf("a\n\n  $$\nx\n", first_line=11)
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=1, column=3):
        mf("> $$\n> x\n")


def test_odd_number_of_display_fences_is_an_error(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=5, column=1):
        mf("$$\na\n$$\n\n$$\nb\n")
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=2, column=1):
        mf("$$ x\n$$\n")
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=1, column=1):
        mf("$$\n")


def test_math_type_and_limit_errors(assert_mdq_error):
    with pytest.raises(TypeError):
        mf(1)
    with pytest.raises(TypeError):
        mf("a", "1")
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        mf("a" * (16 * 1024 * 1024 + 1))


# ---------------------------------------------------------------- maths differential
def oracle_inline(s):
    return re.sub(r"\$(?=\S)([^$]*)(?<=\S)\$(?!\d)", lambda m: "`" + m.group(1) + "`", s)


@given(st.lists(st.sampled_from(list("ab1 $") + ["  ", "x y"]), max_size=14).map("".join))
def test_inline_maths_matches_regex_oracle(s):
    if "$$" in s:
        return
    src = s.replace("\n", " ") + "\n"
    if src.startswith("    "):
        return
    assert mf(src) == oracle_inline(src)


@given(st.lists(st.sampled_from(list("ab1 $\\`*_") + ["\n", "\n\n", "$$"]), max_size=16).map("".join))
def test_math_is_idempotent_and_never_changes_dollar_free_text(s):
    try:
        once = mf(s)
    except mdq.MdqError as e:
        assert e.code == "MDQ_INVALID_SYNTAX"
        return
    if "$" not in s:
        assert once == s
    assert mf(once) == once


@given(st.lists(st.sampled_from(["a", "b", " ", "$x$", "$a b$", "\\$", "$", "`c`", "*", "_"]), max_size=10).map("".join))
def test_rendered_code_spans_hold_exactly_the_math_bodies(s):
    src = s + "\n"
    if not s.strip() or s.startswith("    "):
        return
    expected = [m.group(1) for m in re.finditer(r"(?<![\\$])\$([^\s$][^$]*?)(?<!\s)\$(?!\d)", src)]
    if "`" in s or "\\" in s or "*" in s or "_" in s or "$$" in s:
        return
    assert codes(mf(src)) == expected


# ---------------------------------------------------------------- footnote_check
def test_check_orders_labels_by_first_reference():
    src = "a[^b] c[^a] d[^b]\n\n[^a]: A\n[^b]: B\n"
    assert fc(src) == ["b", "a"]


@pytest.mark.parametrize("src,expected", [
    ("", []),
    ("no notes\n", []),
    ("a[^1]\n\n[^1]: x\n", ["1"]),
    ("[^1]: x\n\na[^1]\n", ["1"]),
    ("[^1]: x\n", []),
    ("a[^1] b[^1]\n\n[^1]: x\n", ["1"]),
    ("a[^long-id_2.x]\n\n[^long-id_2.x]: x\n", ["long-id_2.x"]),
    ("a `[^z]` b \\[^z] [^z\n", []),
    ("<span title=\"[^z]\">a</span>\n", []),
    ("```\n[^z]\n```\n", []),
    ("    [^z]\n", []),
    ("# H[^1]\n\n[^1]: x\n", ["1"]),
    ("> a[^1]\n\n> [^1]: x\n", ["1"]),
    ("- a[^1]\n\n[^1]: x\n", ["1"]),
    ("a[^1]\n[^1]: x\n", ["1"]),
    ("a [^1]: not a def\n", None),
    ("[^]\n", []),
    ("[^ a]\n", []),
    ("[^a b]\n", []),
    ("[^a^b]\n\n[^a^b]: x\n", ["a^b"]),
    ("[[^1]]\n\n[^1]: x\n", ["1"]),
])
def test_check_examples(src, expected, assert_mdq_error):
    if expected is None:
        with assert_mdq_error("MDQ_UNRESOLVED_REFERENCE", line=1, column=3):
            fc(src)
    else:
        assert fc(src) == expected


def test_check_undefined_reference_error_position(assert_mdq_error):
    with assert_mdq_error("MDQ_UNRESOLVED_REFERENCE", line=11, column=5):
        fc("a\n\nxx [^ok]\n\n[^ok]: x\n\n  b [^nope]\n", first_line=5)


def test_check_duplicate_definition_error_position(assert_mdq_error):
    with assert_mdq_error("MDQ_DUPLICATE_ID", line=4, column=1):
        fc("a[^1]\n\n[^1]: x\n[^1]: y\n")


def test_check_first_error_in_document_order(assert_mdq_error):
    with assert_mdq_error("MDQ_UNRESOLVED_REFERENCE", line=1, column=2):
        fc("a[^u]\n\n[^d]: x\n[^d]: y\n")
    with assert_mdq_error("MDQ_DUPLICATE_ID", line=2, column=1):
        fc("[^d]: x\n[^d]: y\n\n[^u]\n")


def test_check_type_and_limit_errors(assert_mdq_error):
    with pytest.raises(TypeError):
        fc(1)
    with pytest.raises(TypeError):
        fc("a", "1")
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        fc("a" * (16 * 1024 * 1024 + 1))


# ---------------------------------------------------------------- footnote_rename
@pytest.mark.parametrize("src,expected", [
    ("a[^1]\n\n[^1]: x\n", "a[^s--1]\n\n[^s--1]: x\n"),
    ("[^a]: x [^b]\n[^b]: y\n", "[^s--a]: x [^s--b]\n[^s--b]: y\n"),
    ("`[^a]` \\[^a] <i title=\"[^a]\">[^a]</i>\n", "`[^a]` \\[^a] <i title=\"[^a]\">[^s--a]</i>\n"),
    ("```\n[^a]\n```\n", "```\n[^a]\n```\n"),
    ("# H[^a]\n", "# H[^s--a]\n"),
    ("> a[^x]\n", "> a[^s--x]\n"),
    ("[^a b] [^] [^a\n", "[^a b] [^] [^a\n"),
    ("a [^x]: z\n", "a [^s--x]: z\n"),
    ("a[^1]\r\n\r\n[^1]: x\r\n", "a[^s--1]\r\n\r\n[^s--1]: x\r\n"),
    ("none\n", "none\n"),
])
def test_rename_examples(src, expected):
    assert fr(src, "s") == expected


def test_rename_result_checks_the_same_as_the_original():
    src = "a[^1] b[^2]\n\n[^2]: two\n[^1]: one\n"
    assert fc(fr(src, "p~2")) == ["p~2--1", "p~2--2"] and fc(src) == ["1", "2"]


@pytest.mark.parametrize("scope", ["", "a b", "a]", "[a", "a\tb"])
def test_rename_rejects_bad_scope(scope, assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX"):
        fr("a\n", scope)


def test_rename_type_errors():
    with pytest.raises(TypeError):
        fr(1, "s")
    with pytest.raises(TypeError):
        fr("a", 1)


LINES = st.sampled_from([
    "text [^a] more", "[^a]: def", "[^b]: def [^a]", "plain", "x [^b][^c] y", "[^c]: it [^d]",
    "a `[^a]` b", "[^d]: end", "see [^a b] and [^]",
])


@given(st.lists(LINES, min_size=1, max_size=8))
def test_footnotes_match_line_oracle(lines):
    src = "\n".join(lines) + "\n"
    assert fr(src, "q") == re.sub(r"(?<!`)\[\^(?=[^\s\[\]]+\])", "[^q--", src)
    defs, refs, bad = [], [], None
    for k, ln in enumerate(lines):
        m = re.match(r"\[\^([^\s\[\]]+)\]:", ln)
        rest = ln
        if m:
            defs.append((m.group(1), k)); rest = ln[m.end():]
        refs += [(r, k) for r in re.findall(r"(?<!`)\[\^([^\s\[\]]+)\]", rest)]
    dset = {d for d, _ in defs}
    seen = set(); expect = None
    first_err = None
    order = []
    # document order of events (defs and refs interleaved per line: label first, then refs)
    events = []
    for k, ln in enumerate(lines):
        m = re.match(r"\[\^([^\s\[\]]+)\]:", ln)
        if m: events.append(("def", m.group(1)))
        rest = ln[m.end():] if m else ln
        events += [("ref", r) for r in re.findall(r"(?<!`)\[\^([^\s\[\]]+)\]", rest)]
    for kind, label in events:
        if kind == "def":
            if label in seen:
                first_err = "MDQ_DUPLICATE_ID"; break
            seen.add(label)
        else:
            if label not in dset:
                first_err = "MDQ_UNRESOLVED_REFERENCE"; break
            if label not in order: order.append(label)
    if first_err:
        with pytest.raises(mdq.MdqError) as e:
            fc(src)
        assert e.value.code == first_err
    else:
        assert fc(src) == order


# ---------------------------------------------------------------- import_expand integration
class FS:
    def __init__(self, files):
        self.files = files

    def __call__(self, p):
        return self.files.get(p)


def expand(root, files, **kw):
    return mi.import_expand(root, "main.md", FS(files), **kw)["text"]


def test_embedded_files_get_their_own_footnote_scope():
    files = {"a.md": "A[^1]\n\n[^1]: from a\n"}
    out = expand("R[^1]\n\n![[a.md]]\n\n[^1]: from root\n", files)
    assert out == "R[^1]\n\nA[^a_2emd--1]\n\n[^a_2emd--1]: from a\n\n[^1]: from root\n"
    assert fc(out) == ["1", "a_2emd--1"]


def test_same_file_embedded_twice_gets_distinct_scopes():
    files = {"a.md": "A[^1]\n\n[^1]: n\n"}
    out = expand("![[a.md]]\n\n![[a.md]]\n", files)
    assert "[^a_2emd--1]" in out and "[^a_2emd~2--1]" in out
    assert fc(out) == ["a_2emd--1", "a_2emd~2--1"]


def test_nested_embeds_and_directories_use_the_encoded_path():
    files = {"d/a.md": "![[b.md]]\n\nA[^1]\n\n[^1]: a\n", "d/b.md": "B[^1]\n\n[^1]: b\n"}
    out = expand("![[d/a.md]]\n", files)
    assert fc(out) == ["d_2fb_2emd--1", "d_2fa_2emd--1"]


def test_scope_encoding_is_injective_for_similar_paths():
    files = {"a-b.md": "[^1]: x\n\nA[^1]\n", "a/b.md": "[^1]: y\n\nB[^1]\n", "a_b.md": "[^1]: z\n\nC[^1]\n"}
    out = expand("![[a-b.md]]\n\n![[a/b.md]]\n\n![[a_b.md]]\n", files)
    assert len(set(fc(out))) == 3


def test_root_file_keeps_its_labels_and_footnotes_false_keeps_everything():
    files = {"a.md": "A[^1]\n\n[^1]: a\n"}
    root = "R[^1]\n\n![[a.md]]\n\n[^1]: r\n"
    out = expand(root, files, footnotes=False)
    assert out == "R[^1]\n\nA[^1]\n\n[^1]: a\n\n\n[^1]: r\n".replace("\n\n\n", "\n\n")
    with pytest.raises(mdq.MdqError) as e:
        fc(out)
    assert e.value.code == "MDQ_DUPLICATE_ID"


def test_partial_embed_without_its_definition_fails_the_final_check(assert_mdq_error):
    files = {"a.md": "# One\n\nText[^n]\n\n# Two\n\n[^n]: note\n"}
    out = expand("![[a.md#One]]\n", files)
    with assert_mdq_error("MDQ_UNRESOLVED_REFERENCE"):
        fc(out)
    assert fc(expand("![[a.md]]\n", files)) == ["a_2emd--n"]


def test_footnote_rename_and_link_rebase_apply_to_the_same_piece():
    files = {"d/a.md": "[x](b.md)[^1]\n\n[^1]: [y](c.md)\n"}
    out = expand("![[d/a.md]]\n", files)
    assert out == "[x](d/b.md)[^d_2fa_2emd--1]\n\n[^d_2fa_2emd--1]: [y](d/c.md)\n"


def test_footnotes_param_type_error():
    with pytest.raises(TypeError):
        mi.import_expand("a\n", "main.md", FS({}), footnotes="no")


# Regression (found by Hypothesis, 2026-11-13): a `$` run that fails to open a span must not pair with a
# later `$` once a following span has been turned into a code span. Such runs are escaped (`\$`, renders
# as `$`); runs that would not pair again are left as they are.
@pytest.mark.parametrize("src,want", [
    ("$$a\n$$a$$a$$", "\\$\\$a\n`a`a$$"),
    ("$a\n$b$c$", "\\$a\n`b`c$"),
    ("x $a\n$b$ $c$ d$", "x \\$a\n`b` `c` d$"),
    ("$a $b$", "$a `b`"),          # nothing to pair with afterwards: unchanged
    ("$a\n$b$", "$a\n`b`"),
])
def test_math_output_is_a_fixed_point_regression(src, want):
    assert mf(src) == want
    assert mf(want) == want
    # the escapes are invisible once rendered: no backslash survives, every `$` is still shown
    html = MarkdownIt("commonmark").render(want)
    assert "\\" not in html and html.count("$") == want.count("$")
