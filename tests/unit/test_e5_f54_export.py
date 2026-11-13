"""Epic 5 / E5.4 (F23): `export_markdown` (spec sections 7 and 9, steps 5 and 6). G41.

`export_markdown(text, profile="commonmark", bibs=None, first_line=1)` turns an EVALUATED document body
(imports expanded, variables interpolated, `if`/`for` evaluated, frontmatter removed) into portable
Markdown: details, columns and optional generic containers, callouts, highlights, citations with their
bibliography, maths (profile `commonmark` only; `gfm` keeps `$` notation), footnote validation and the
TOC, in that order. Anything unevaluated or without a handler fails with MDQ_UNSUPPORTED_FEATURE.
`generic_fallback(text, first_line=1)` drops the fences of containers with `optional="true"`.
"""
import pytest
from hypothesis import given, strategies as st
from markdown_it import MarkdownIt

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

ex = mi.export_markdown
gf = mi.generic_fallback
MD = MarkdownIt("commonmark")
BIB = [("bibtex", "@misc{k, author={Q, R}, title={T}, year=2000}\n")]

DOC = """[[toc]]

# Title

> [!NOTE]
> a ==hi== $x^2$

:::details {title="More"}
see [@k]

::::columns
:::::column
left
:::::
::::
:::

:::vendor.x {optional="true"}
kept
:::

Text[^1]

[^1]: note

[[bibliography]]
"""

DOC_OUT = """- [Title](#title)
  - [More](#more)

# Title

> **Note**
>
> a hi `x^2`

## More

see (Q 2000)

left

kept

Text[^1]

[^1]: note

- Q, R (2000). T.
"""


def test_full_example_commonmark():
    assert ex(DOC, bibs=BIB) == DOC_OUT


def test_gfm_profile_keeps_maths():
    out = ex(DOC, "gfm", BIB)
    assert "> a hi $x^2$" in out and "`x^2`" not in out
    assert ex("$$\nx\n$$\n", "gfm") == "$$\nx\n$$\n"
    assert ex("$$\nx\n$$\n") == "```math\nx\n```\n"


def test_mermaid_tables_tasks_strikethrough_footnotes_are_kept():
    src = "```mermaid\ngraph TD\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- [x] done\n\n~~s~~ n[^1]\n\n[^1]: f\n"
    assert ex(src) == src


def test_toc_sees_the_headings_made_by_fallbacks_and_keeps_anchors_unique():
    src = "[[toc]]\n\n:::details\nx\n:::\n\n## Details\n"
    out = ex(src)
    assert out.startswith("- [Details](#details)\n- [Details](#details-2)\n\n## Details\n\nx\n\n## Details\n")


def test_citations_add_references_to_the_toc():
    out = ex("[[toc]]\n\n# A\n\n[@k]\n", bibs=BIB)
    assert out.startswith("- [A](#a)\n  - [References](#references)\n\n") and out.endswith("## References\n\n- Q, R (2000). T.\n")


# ---------------------------------------------------------------- unevaluated constructs
def test_unevaluated_variable_fails(assert_mdq_error):
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=3, column=3):
        ex("a\n\nb {{ x }}\n")
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=13, column=1):
        ex("{{a.b}}\n", first_line=13)


def test_escaped_and_code_variables_are_fine():
    assert ex("`{{x}}` \\{{x}}\n\n```\n{{y}}\n```\n") == "`{{x}}` \\{{x}}\n\n```\n{{y}}\n```\n"


def test_malformed_variable_syntax_propagates(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX"):
        ex("a {{ 1x }}\n")


def test_unexpanded_embed_fails(assert_mdq_error):
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=3, column=1):
        ex("a\n\n![[x.md]]\n")
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=1, column=3):
        ex("a ![[x.md#H]] b\n")
    assert ex("`![[x]]`\n\n```\n![[x]]\n```\n") == "`![[x]]`\n\n```\n![[x]]\n```\n"


@pytest.mark.parametrize("src,line", [
    (":::if {var=\"a\" equals=\"b\"}\nx\n:::\n", 1),
    ("a\n\n:::for {each=\"x\" as=\"y\"}\nx\n:::\n", 3),
    (":::details\n::::if {var=\"a\" equals=\"b\"}\nx\n::::\n:::\n", 2),
])
def test_unevaluated_containers_fail(src, line, assert_mdq_error):
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=line, column=1):
        ex(src)


@pytest.mark.parametrize("src,line", [
    (":::vendor.x\nx\n:::\n", 1),
    ("a\n\n:::note {optional=\"false\"}\nx\n:::\n", 3),
    (":::vendor.x {key=\"v\"}\nx\n:::\n", 1),
    (":::details\n::::vendor.y\nx\n::::\n:::\n", 2),
    (":::vendor.x {optional=\"true\"}\n::::vendor.y\nx\n::::\n:::\n", 2),
])
def test_unknown_required_containers_fail(src, line, assert_mdq_error):
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=line, column=1):
        ex(src)


def test_optional_generic_containers_keep_their_body():
    assert ex("a\n\n:::vendor.x {optional=\"true\" k=\"v\"}\n**b**\n:::\n\nc\n") == "a\n\n**b**\n\nc\n"
    assert ex(":::a {optional=\"true\"}\n::::b {optional=\"true\"}\nx\n::::\n:::\n") == "x\n"


def test_malformed_container_syntax_propagates(assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX"):
        ex(":::details\nx\n")


# ---------------------------------------------------------------- diagnostics carry original lines
def test_errors_report_lines_of_the_original_text(assert_mdq_error):
    top = ":::details\nx\n:::\n\n> [!NOTE]\n> ok\n\n"
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=14, column=3):
        ex(top + "> [!FOO]\n> x\n", first_line=7)
    with assert_mdq_error("MDQ_UNSUPPORTED_FEATURE", line=8, column=1):
        ex(top + "x ==*a==* y\n")
    with assert_mdq_error("MDQ_INVALID_SYNTAX", line=8, column=1):
        ex(top + "$$\nx\n")
    with assert_mdq_error("MDQ_UNRESOLVED_REFERENCE", line=8, column=3):
        ex(top + "a [@nope]\n", bibs=BIB)
    with assert_mdq_error("MDQ_UNRESOLVED_REFERENCE", line=8, column=2):
        ex(top + "a[^z]\n")
    with assert_mdq_error("MDQ_DUPLICATE_ID", line=9, column=1):
        ex(top + "[^a]: x\n[^a]: y\n")


def test_gfm_profile_does_not_require_closed_display_maths():
    assert ex("$$\nx\n", "gfm") == "$$\nx\n"


# ---------------------------------------------------------------- arguments
def test_argument_errors(assert_mdq_error):
    with pytest.raises(TypeError):
        ex(1)
    with pytest.raises(TypeError):
        ex("a", 1)
    with pytest.raises(ValueError):
        ex("a", "html")
    with pytest.raises(TypeError):
        ex("a", "commonmark", "bib")
    with pytest.raises(TypeError):
        ex("a", "commonmark", None, "1")
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        ex("a" * (16 * 1024 * 1024 + 1))


# ---------------------------------------------------------------- generic_fallback
@pytest.mark.parametrize("src,expected", [
    (":::v.x {optional=\"true\"}\nb\n:::\n", "b\n"),
    ("a\n:::v.x {optional=\"true\"}\nb\n:::\nc\n", "a\n\nb\n\nc\n"),
    (":::v.x\nb\n:::\n", ":::v.x\nb\n:::\n"),
    (":::v.x {optional=\"no\"}\nb\n:::\n", ":::v.x {optional=\"no\"}\nb\n:::\n"),
    (":::details {optional=\"true\"}\nb\n:::\n", ":::details {optional=\"true\"}\nb\n:::\n"),
    (":::if {var=\"a\" equals=\"b\"}\nb\n:::\n", ":::if {var=\"a\" equals=\"b\"}\nb\n:::\n"),
    (":::v.x\n::::v.y {optional=\"true\"}\nb\n::::\n:::\n", ":::v.x\nb\n:::\n"),
    ("plain\n", "plain\n"),
    ("", ""),
])
def test_generic_fallback(src, expected):
    assert gf(src) == expected


def test_generic_fallback_argument_errors(assert_mdq_error):
    with pytest.raises(TypeError):
        gf(1)
    with pytest.raises(TypeError):
        gf("a", "1")
    with assert_mdq_error("MDQ_INVALID_SYNTAX"):
        gf(":::v.x {optional=\"true\"}\nx\n")


# ---------------------------------------------------------------- properties
RICH = [
    "# H", "## Sub", "text $x$ and ==hl== more", "> [!TIP]\n> tip", "> [!warning]\n> w\n> more",
    ":::details {title=\"D\"}\nbody\n:::", ":::details\n::::columns\n:::::column\na\n:::::\n:::::column\nb\n:::::\n::::\n:::",
    ":::v.y {optional=\"true\"}\nopt\n:::", "[[toc]]", "para [@k]", "see [@k, p. 3]", "[[bibliography]]",
    "Fn[^a]\n\n[^a]: n", "$$\nx = 1\n\n- y\n$$", "```mermaid\ng\n```", "| a | b |\n|---|---|\n| 1 | 2 |", "plain *text*",
    "```\n$$\n==x==\n```", "costs $5 and $6",
]
PLAIN = [
    "# H", "para text", "- a\n- b", "> q", "1. x\n2. y", "```py\ncode\n```", "| a | b |\n|---|---|\n| 1 | 2 |",
    "text with $5 and $6", "a == b and c = d", "[x](u) ![i](p)", "***", "<div>\nh\n</div>", "a ~~b~~ c", "- [ ] t",
    "text[^1]\n\n[^1]: n", "```mermaid\ng\n```", "Setext\n===", "    indented code", "a [[wiki]] b", "email@host and @user",
    "[ref]: http://x \"t\"", "`$a$` and `[@k]`",
]


@given(st.lists(st.sampled_from(RICH), min_size=0, max_size=7).map("\n\n".join))
def test_export_is_idempotent_and_leaves_no_rich_constructs(src):
    src = src + "\n" if src else src
    for profile in ("commonmark", "gfm"):
        try:
            once = ex(src, profile, BIB)
        except mdq.MdqError as e:
            assert e.code in ("MDQ_DUPLICATE_ID", "MDQ_UNSUPPORTED_FEATURE", "MDQ_INVALID_SYNTAX", "MDQ_UNRESOLVED_REFERENCE"), e
            continue
        assert ex(once, profile, BIB) == once
        assert mi.directive_scan(once) == []
        for marker in ("[!TIP]", "[!WARNING]", "[!warning]", ":::", "[[toc]]", "[[bibliography]]", "[@k"):
            assert marker not in once.replace("```", "") or marker in "".join(
                b for b in once.split("\n\n") if b.startswith("```")), (marker, once)


@given(st.lists(st.sampled_from(PLAIN), min_size=0, max_size=6, unique=True).map("\n\n".join))
def test_plain_markdown_passes_through_byte_identical(src):
    src = src + "\n" if src else src
    for profile in ("commonmark", "gfm"):
        out = ex(src, profile)
        assert out == src
        assert MD.render(out) == MD.render(src)


def test_export_is_deterministic():
    outs = {ex(DOC, bibs=BIB) for _ in range(200)}
    assert outs == {DOC_OUT}


def test_export_after_import_expansion_includes_imported_headings():
    files = {"a.md": "# Imported\n\nText $m$ ==h==\n"}
    expanded = mi.import_expand("[[toc]]\n\n# Root\n\n![[a.md]]\n", "main.md", lambda p: files.get(p))["text"]
    assert ex(expanded) == "- [Root](#root)\n- [Imported](#imported)\n\n# Root\n\n# Imported\n\nText `m` h\n"


def test_export_after_evaluation():
    evaluated = mi.container_eval(":::if {var=\"flag\" equals=\"yes\"}\nHello {{ name }} ==!==\n:::\n", {"flag": "yes", "name": "Ann"})
    assert ex(evaluated) == "Hello Ann !\n"
