"""Epic 8 / E8.1d (spec 3, 3.1): reference links, block anchors, duplicate block IDs. G57 (BUG-1..3 of G56).

* `[[path.md]]`, `[[path.md#Heading]]`, `[[path.md#^id]]`, `|Label` (empty path = this file) resolve against their
  OWN source file. A target rendered in the output becomes `[Label](#anchor)` (first rendered occurrence); a
  target not in the output becomes a link to the referenced file, `[Label](file.md#anchor)`, relative to the
  entry's directory. Unknown heading/block: MDQ_UNRESOLVED_REFERENCE; missing file: MDQ_MISSING_IMPORT.
* Block IDs become `<a id="..."></a>` anchors at the start of their block (after `# ` for headings); an ID repeated
  across files gets `id-2`...; generated heading slugs (links and TOC) never take a block anchor's name.
* Block IDs must be unique per source document (MDQ_DUPLICATE_ID).
"""
import re

import pytest
from markdown_it import MarkdownIt

import mdq

pytestmark = pytest.mark.unit
MD = MarkdownIt("commonmark")


def md(files, entry="main.md", **kw):
    return mdq.compile(entry, files, **kw).markdown


def assert_links_land(markdown):
    """Every internal link `#x` in the rendered HTML points at an id that exists, or at a heading slug."""
    html = MD.render(markdown)
    ids = set(re.findall(r'id="([^"]+)"', html))
    heads = [re.sub(r"<[^>]+>", "", h) for h in re.findall(r"<h[1-6]>(.*?)</h[1-6]>", html)]
    slugs = set(mdq._internal.slug_dedupe([mdq._internal.slugify(h) for h in heads], sorted(ids)))
    for href in re.findall(r'href="#([^"]+)"', html):
        assert href in ids or href in slugs, (href, ids, slugs)


# ---------------------------------------------------------------- same file and rendered targets
def test_same_file_heading_and_block():
    out = md({"main.md": "# Intro\n\nSee [[#Intro]] and [[#^p1|the para]].\n\nPara one ^p1\n"})
    assert out == '# Intro\n\nSee [Intro](#intro) and [the para](#p1).\n\n<a id="p1"></a>Para one\n'
    assert_links_land(out)


def test_explicit_own_path_equals_empty_path():
    a = md({"main.md": "# A\n\n[[main.md#A]]\n"})
    b = md({"main.md": "# A\n\n[[#A]]\n"})
    assert a == b == "# A\n\n[A](#a)\n"


def test_embedded_target_links_locally():
    out = md({"main.md": "![[b.md]]\n\nSee [[b.md#X|the X]] and [[b.md]].\n", "b.md": "# X\n"})
    assert out == "# X\n\nSee [the X](#x) and [b.md](#x).\n"


def test_first_rendered_occurrence_and_deduplicated_slugs():
    files = {"main.md": "# Setup\n\n![[b.md]]\n\n![[b.md]]\n\n[[b.md#Setup]] [[#Setup]]\n", "b.md": "# Setup\n"}
    out = md(files)
    assert out.endswith("[Setup](#setup-2) [Setup](#setup)\n")  # b.md's first rendered Setup is the 2nd heading
    assert_links_land(out)


def test_references_resolve_against_their_own_file():
    files = {"main.md": "# Intro\n\n![[sub/b.md]]\n", "sub/b.md": "# Intro\n\nBack to [[#Intro]] and [[../main.md#Intro]].\n"}
    out = md(files)
    assert out == "# Intro\n\n# Intro\n\nBack to [Intro](#intro-2) and [Intro](#intro).\n"


def test_imported_files_are_found_relative_to_the_referencing_file():
    files = {"main.md": "![[a/x.md]]\n", "a/x.md": "See [[y.md#Y]].\n\n![[y.md]]\n", "a/y.md": "# Y\n"}
    assert md(files) == "See [Y](#y).\n\n# Y\n"


# ---------------------------------------------------------------- targets not in the output
def test_absent_targets_link_the_referenced_file():
    files = {"main.md": "See [[docs/other.md#Set Up]], [[docs/other.md]] and [[docs/other.md#^b1|b]].\n",
             "docs/other.md": "# Intro\n\n# Set Up\n\nThe block ^b1\n"}
    assert md(files) == ("See [Set Up](docs/other.md#set-up), [docs/other.md](docs/other.md) and "
                         "[b](docs/other.md#b1).\n")


def test_absent_target_path_is_relative_to_the_entry_directory():
    files = {"guide/main.md": "[[../ref/api.md#Calls]] [[sub/x.md]]\n", "ref/api.md": "# Calls\n", "guide/sub/x.md": "x\n"}
    assert md(files, entry="guide/main.md") == "[Calls](../ref/api.md#calls) [sub/x.md](sub/x.md)\n"


def test_absent_target_anchor_is_the_file_s_own_slug():
    files = {"main.md": "[[o.md#A]]\n", "o.md": "Para ^a\n\n# A\n"}  # in o.md, `a` is taken by the block ID
    assert md(files) == "[A](o.md#a-2)\n"


def test_absent_file_with_awkward_name_is_bracketed():
    assert md({"main.md": "[[my doc.md]]\n", "my doc.md": "x\n"}) == "[my doc.md](<my doc.md>)\n"


# ---------------------------------------------------------------- labels
def test_labels():
    files = {"main.md": "# Café *bold*\n\n[[#Café bold|*my* label]] [[#Café bold]] [[#^z]]\n\nz ^z\n"}
    out = md(files)
    assert "[*my* label](#café-bold)".replace("é", "%C3%A9") in out
    assert "[Café bold](#caf%C3%A9-bold)" in out and "[z](#z)" in out


def test_default_label_is_escaped():
    out = md({"main.md": "# a_b *c*\n\n[[#a_b c]]\n"})
    assert out.endswith("[a\\_b c](#a_b-c)\n")


# ---------------------------------------------------------------- errors
@pytest.mark.parametrize("files,code,line,col", [
    ({"main.md": "x\n\nSee [[b.md#Nope]].\n", "b.md": "# H\n"}, "MDQ_UNRESOLVED_REFERENCE", 3, 5),
    ({"main.md": "[[#^missing]]\n"}, "MDQ_UNRESOLVED_REFERENCE", 1, 1),
    ({"main.md": "ok [[nofile.md#X]]\n"}, "MDQ_MISSING_IMPORT", 1, 4),
    ({"main.md": "[[../out.md]]\n"}, "MDQ_SECURITY_DENIED", 1, 1),
    ({"main.md": "[[x.md|]]\n", "x.md": "x"}, "MDQ_INVALID_SYNTAX", 1, 1),
    ({"main.md": "# Title [[#Title]]\n"}, "MDQ_UNSUPPORTED_FEATURE", 1, 9),
])
def test_reference_errors_are_located(assert_mdq_error, files, code, line, col):
    with assert_mdq_error(code, path="main.md", line=line, column=col):
        mdq.compile("main.md", files)


def test_errors_inside_imported_files_name_that_file(assert_mdq_error):
    with assert_mdq_error("MDQ_UNRESOLVED_REFERENCE", path="p.md", line=2):
        mdq.compile("main.md", {"main.md": "![[p.md]]\n", "p.md": "x\n[[#Nope]]\n"})


def test_excluded_branches_are_not_resolved():
    doc = ':::if {var="on" equals="yes"}\n[[missing.md]]\n:::\n\nok\n'
    assert md({"main.md": doc}, inputs={"on": "no"}) == "\nok\n"


def test_text_between_inline_html_tags_is_markdown():
    # CommonMark processes text between inline tags; only text inside a tag is skipped (as for embeds, G35)
    assert md({"main.md": "<span>[[x.md]]</span>\n", "x.md": "x"}) == "<span>[x.md](x.md)</span>\n"


# ---------------------------------------------------------------- not references
@pytest.mark.parametrize("text", [
    "`[[x.md]]` in code\n", "```\n[[x.md]]\n```\n", "<a title=\"[[x.md]]\">t</a>\n", "\\[[x.md]]\n", "\\![[x.md]]\n",
    "[[x\n", "[[a [b] c]]\n",
])
def test_not_references(text):
    assert md({"main.md": text}) == text


def test_toc_directive_keeps_its_meaning():
    out = md({"main.md": "# A\n\n[[toc]]\n\n## B\n\n[[#B]]\n"})
    assert out == "# A\n\n- [A](#a)\n  - [B](#b)\n\n## B\n\n[B](#b)\n"


# ---------------------------------------------------------------- block anchors
def test_block_anchor_positions_and_rendering():
    out = md({"main.md": "# Intro ^top\n\nPara ^p\n\nline one\nline two ^q\n\n> quoted ^r\n"})
    assert out == ('# <a id="top"></a>Intro\n\n<a id="p"></a>Para\n\n<a id="q"></a>line one\nline two\n\n'
                   '> <a id="r"></a>quoted\n')
    html = MD.render(out)
    assert html.count('<a id="') == 4 and "<h1><a id=\"top\"></a>Intro</h1>" in html and "<p>" in html


def test_block_ids_repeated_across_files_get_distinct_anchors():
    files = {"main.md": "![[a.md]]\n\n![[b.md]]\n", "a.md": "In a ^p1\n\n[[#^p1|to a]]\n", "b.md": "In b ^p1\n\n[[#^p1|to b]]\n"}
    out = md(files)
    assert out == '<a id="p1"></a>In a\n\n[to a](#p1)\n\n<a id="p1-2"></a>In b\n\n[to b](#p1-2)\n'
    assert_links_land(out)


def test_generated_slugs_never_take_a_block_anchor():
    out = md({"main.md": "Para ^a\n\n# A\n\n[[toc]]\n\n[[#A]]\n"})
    assert out == '<a id="a"></a>Para\n\n# A\n\n- [A](#a-2)\n\n[A](#a-2)\n'
    assert_links_land(out)


# ---------------------------------------------------------------- duplicate block IDs (per source document)
def test_duplicate_block_ids_in_one_document(assert_mdq_error):
    with assert_mdq_error("MDQ_DUPLICATE_ID", path="main.md", line=3):
        mdq.compile("main.md", {"main.md": "a ^p1\n\nb ^p1\n"})
    with assert_mdq_error("MDQ_DUPLICATE_ID", path="p.md", line=3):
        mdq.compile("main.md", {"main.md": "![[p.md]]\n", "p.md": "# A ^x\n\n# B ^x\n"})


def test_duplicates_are_checked_on_the_source_even_in_excluded_branches(assert_mdq_error):
    doc = 'a ^p1\n\n:::if {var="on" equals="yes"}\nb ^p1\n:::\n'
    with assert_mdq_error("MDQ_DUPLICATE_ID", path="main.md", line=4):
        mdq.compile("main.md", {"main.md": doc}, inputs={"on": "no"})


# ---------------------------------------------------------------- diagnostic paths (G56 findings)
@pytest.mark.parametrize("files,path", [
    ({"d.md": "---\nmdq: '0.2'\n---\nx\n"}, "d.md"),
    ({"d.md": "x\n", ".mdq.yaml": "features: [bogus]\n"}, ".mdq.yaml"),
    ({"d.md": "x\n", "d.meta.yaml": "variables:\n  calc: 1\n"}, "d.meta.yaml"),
])
def test_version_feature_and_namespace_errors_name_their_file(files, path):
    with pytest.raises(mdq.MdqError) as e:
        mdq.compile("d.md", files)
    assert e.value.path == path
