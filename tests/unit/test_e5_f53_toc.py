"""Epic 5 / E5.3 (F10): table of contents (spec section 7). G40.

`toc_build(text, first_line=1)` replaces every paragraph that is exactly `[[toc]]` at column 1 by a nested
list of links `- [Heading text](#slug)` to all headings of the text (also those in quotes and lists, never
those in code). Slugs follow G33 (`slugify`, `slug_dedupe`, `percent_encode_anchor`). Nesting follows the
heading levels: a heading is nested under the nearest earlier heading of a lower level, skipped levels
add no empty levels, two spaces of indentation per depth. Run it on the output of `import_expand` and the
imported headings are included.
"""
import re

import pytest
from hypothesis import given, strategies as st
from markdown_it import MarkdownIt

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

toc = mi.toc_build
MD = MarkdownIt("commonmark")


@pytest.mark.parametrize("src,expected", [
    ("[[toc]]\n\n# A\n", "- [A](#a)\n\n# A\n"),
    ("# A\n\n[[toc]]\n\n## B\n", "# A\n\n- [A](#a)\n  - [B](#b)\n\n## B\n"),
    ("[[toc]]\n\n# A\n## B\n### C\n## D\n# E\n", "- [A](#a)\n  - [B](#b)\n    - [C](#c)\n  - [D](#d)\n- [E](#e)\n\n# A\n## B\n### C\n## D\n# E\n"),
    ("[[toc]]\n\n# A\n### C\n## B\n", "- [A](#a)\n  - [C](#c)\n  - [B](#b)\n\n# A\n### C\n## B\n"),
    ("[[toc]]\n\n## A\n# B\n", "- [A](#a)\n- [B](#b)\n\n## A\n# B\n"),
    ("[[toc]]\n\n### A\n## B\n", "- [A](#a)\n- [B](#b)\n\n### A\n## B\n"),
    ("[[toc]]\n\n# A\n# A\n# A\n", "- [A](#a)\n- [A](#a-2)\n- [A](#a-3)\n\n# A\n# A\n# A\n"),
    ("[[toc]]\n\n# A\n# A-2\n# A\n", "- [A](#a)\n- [A-2](#a-2)\n- [A](#a-3)\n\n# A\n# A-2\n# A\n"),
    ("[[toc]]\n\n# Hello, World!\n", "- [Hello, World!](#hello-world)\n\n# Hello, World!\n"),
    ("[[toc]]\n\n# Café ñ\n", "- [Café ñ](#caf%C3%A9-%C3%B1)\n\n# Café ñ\n"),
    ("[[toc]]\n\n# A *b* `c` [d](u) &amp; ![i](p)\n", "- [A b c d \\& i](#a-b-c-d-i)\n\n# A *b* `c` [d](u) &amp; ![i](p)\n"),
    ("[[toc]]\n\nSetext\n===\n\nS2\n---\n", "- [Setext](#setext)\n  - [S2](#s2)\n\nSetext\n===\n\nS2\n---\n"),
    ("[[toc]]\n\n> # Q\n\n- # L\n", "- [Q](#q)\n- [L](#l)\n\n> # Q\n\n- # L\n"),
    ("[[toc]]\n\n```\n# no\n```\n\n    # no\n\n<div>\n# no\n</div>\n\n# Yes\n", None),
    ("[[toc]]\n\n#\n", "- [section](#section)\n\n#\n"),
    ("[[toc]]\n\n# a_b *c* [x]\n", None),
    ("[[toc]]\n\n# A ^id1\n", "- [A](#a)\n\n# A ^id1\n"),
    ("[[toc]]\n\n# A\n\n[[toc]]\n", "- [A](#a)\n\n# A\n\n- [A](#a)\n"),
    ("[[toc]]\r\n\r\n# A\r\n## B\r\n", "- [A](#a)\r\n  - [B](#b)\r\n\r\n# A\r\n## B\r\n"),
    ("  [[toc]]\n\n# A\n", "  [[toc]]\n\n# A\n"),
    ("[[toc]]  \n\n# A\n", "- [A](#a)\n\n# A\n"),
    ("# A\n\n[[toc]]", "# A\n\n- [A](#a)"),
    ("[[toc]]\n", "\n"),
    ("[[toc]]\n\ntext\n", "\n\ntext\n"),
    ("", ""),
    ("# A\n", "# A\n"),
])
def test_toc_examples(src, expected):
    out = toc(src)
    if expected is not None:
        assert out == expected


def test_toc_ignores_headings_in_code_and_html():
    out = toc("[[toc]]\n\n```\n# no\n```\n\n    # no\n\n<div>\n# no\n</div>\n\n# Yes\n")
    assert out.startswith("- [Yes](#yes)\n\n")


def test_toc_escapes_markup_in_link_text():
    out = toc("[[toc]]\n\n# a_b *c* [x]\n\n# 1*2*3 <t> \\* x\n")
    assert out.split("\n")[:2] == ["- [a\\_b c \\[x\\]](#a_b-c-x)", "- [123 \\* x](#123-x)"]
    html = MD.render(out.split("\n\n# ")[0])
    assert "a_b c [x]</a>" in html


@pytest.mark.parametrize("src", [
    "> [[toc]]\n\n# A\n", "- [[toc]]\n\n# A\n", "x [[toc]]\n\n# A\n", "[[toc]] x\n\n# A\n", "    [[toc]]\n\n# A\n",
    "```\n[[toc]]\n```\n\n# A\n", "`[[toc]]`\n\n# A\n", "\\[[toc]]\n\n# A\n", "[[ toc ]]\n\n# A\n", "[[TOC]]\n\n# A\n",
    "[[toc]]\n[[toc]]\n\n# A\n", "[[toc]](u)\n\n# A\n", "<div>\n[[toc]]\n</div>\n\n# A\n",
])
def test_non_placeholders_are_untouched(src):
    assert toc(src) == src


def test_toc_propagates_index_errors(assert_mdq_error):
    with assert_mdq_error("MDQ_DUPLICATE_ID", line=23):
        toc("[[toc]]\n\n# A ^x\n\ntext\n\n\n\n\n\n\n\ntext ^x\n", first_line=11)


def test_toc_is_idempotent_and_unchanged_without_placeholder():
    src = "# A\n\n## B\n"
    assert toc(src) == src
    once = toc("[[toc]]\n\n# A\n")
    assert toc(once) == once


def test_toc_includes_imported_headings():
    files = {"a.md": "# Imported\n\n## Sub\n", "b.md": "# Imported\n"}
    root = "[[toc]]\n\n# Root\n\n![[a.md]]\n\n![[b.md]]\n"
    expanded = mi.import_expand(root, "main.md", lambda p: files.get(p))["text"]
    out = toc(expanded)
    assert out.startswith("- [Root](#root)\n- [Imported](#imported)\n  - [Sub](#sub)\n- [Imported](#imported-2)\n\n")


def test_toc_links_resolve_against_the_headings_of_the_document():
    src = "[[toc]]\n\n# One\n## Two\n## Two\n# 日本語\n"
    html = MD.render(toc(src).split("\n\n# One")[0])
    hrefs = re.findall(r'href="([^"]*)"', html)
    assert hrefs == ["#one", "#two", "#two-2", "#%E6%97%A5%E6%9C%AC%E8%AA%9E"]


def test_toc_argument_errors(assert_mdq_error):
    with pytest.raises(TypeError):
        toc(1)
    with pytest.raises(TypeError):
        toc("a", "1")
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        toc("a" * (16 * 1024 * 1024 + 1))


WORDS = st.sampled_from(["a", "b", "c", "aa", "x y", "a b"])


@given(st.lists(st.tuples(st.integers(1, 6), WORDS), min_size=0, max_size=10))
def test_toc_matches_a_tree_oracle_rendered_by_markdown_it(heads):
    src = "[[toc]]\n\n" + "".join("#" * lv + " " + w + "\n\n" for lv, w in heads)
    out = toc(src)
    toc_md = out.split("\n\n#")[0] if heads else out
    html = MD.render(toc_md)
    # oracle: depth by the stack rule, slugs by counting
    seen = {}; expected = []; stack = []
    for lv, w in heads:
        while stack and stack[-1] >= lv:
            stack.pop()
        base = w.replace(" ", "-")
        n = seen.get(base, 0) + 1
        seen[base] = n
        slug = base if n == 1 else f"{base}-{n}"
        expected.append((len(stack), w, slug))
        stack.append(lv)
    got = []
    depth = 0
    for m in re.finditer(r"<ul>|</ul>|<a href=\"([^\"]*)\">([^<]*)</a>", html):
        if m.group(0) == "<ul>":
            depth += 1
        elif m.group(0) == "</ul>":
            depth -= 1
        else:
            got.append((depth - 1, m.group(2), m.group(1)[1:]))
    # the oracle's counting dedupe differs from slug_dedupe only when a base like "a-2" is also a heading
    if len({w.replace(" ", "-") for _, w in heads}) == len(heads) or all(not re.search(r"-\d+$", w) for _, w in heads):
        assert got == expected
