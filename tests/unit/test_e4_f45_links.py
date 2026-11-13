"""Epic 4 / E4.5 (F07): link rebasing (spec section 3.1, last paragraph).

- `rebase_link(dest, from_dir, to_dir)`: a destination written in a file of directory `from_dir`
  rewritten for output placed in `to_dir` (root-relative directories, "" = root). Absolute URLs,
  `scheme:`, `//host`, `/abs`, `#frag` and `?query` are untouched; query/fragment are kept; a result
  that would leave the root is MDQ_SECURITY_DENIED. Takes the DECODED destination.
- `rebase_links(text, from_dir, to_dir, first_line=1)`: rewrites inline links/images and one-line
  reference definitions in paragraphs and headings (not code, HTML, autolinks, footnote definitions).
- `import_expand(..., rebase=True)` rebases embedded files' links to the root file's directory. G36.
"""
import posixpath
import re

import pytest
from hypothesis import given, strategies as st
from markdown_it import MarkdownIt

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

rl = mi.rebase_link
rls = mi.rebase_links


# ---------------------------------------------------------------- rebase_link
@pytest.mark.parametrize("dest,f,t,expected", [
    ("images/flow.png", "chapters", "", "chapters/images/flow.png"),
    ("images/flow.png", "chapters", "chapters", "images/flow.png"),
    ("./a.png", "a/b", "a/b", "a.png"),
    ("../x.md", "a/b", "", "a/x.md"),
    ("../../x.md", "a/b", "a/b", "../../x.md"),
    ("../x.md", "a", "a/b/c", "../../../x.md"),
    ("../../x.md", "a/b/c", "a", "x.md"),
    ("x.md#h", "a", "", "a/x.md#h"),
    ("x.md?q=1#h", "a", "", "a/x.md?q=1#h"),
    ("x.md#a?b", "a", "", "a/x.md#a?b"),
    ("#h", "a", "", "#h"),
    ("?q=1", "a", "", "?q=1"),
    ("", "a", "", ""),
    ("sub/", "a", "", "a/sub/"),
    (".", "a", "", "a"),
    ("./", "a", "", "a/"),
    ("..", "a", "", "."),
    ("../", "a", "", "./"),
    ("./a:b.png", "", "", "./a:b.png"),
    ("a%20b%2Fc.png", "x", "", "x/a%20b%2Fc.png"),
    ("%2e%2e/x", "a", "b", "../a/%2e%2e/x"),
    ("a//b", "x", "", "x/a/b"),
    ("a b.png", "x", "", "x/a b.png"),
    ("\u00e9/\u4e2d.png", "d", "", "d/\u00e9/\u4e2d.png"),
    ("https://example.com/a.png", "a", "", "https://example.com/a.png"),
    ("HTTP://example.com", "a", "", "HTTP://example.com"),
    ("mailto:a@b.c", "a", "", "mailto:a@b.c"),
    ("tel:+34123", "a", "", "tel:+34123"),
    ("data:text/plain,hi", "a", "", "data:text/plain,hi"),
    ("C:/x/y.png", "a", "", "C:/x/y.png"),
    ("//host/x.png", "a", "", "//host/x.png"),
    ("/abs/x.png", "a", "", "/abs/x.png"),
])
def test_rebase_link_table(dest, f, t, expected):
    assert rl(dest, f, t) == expected


@pytest.mark.parametrize("dest,f,t", [("../x", "", "a"), ("../../x", "a", ""), ("a/../../x", "", ""), ("../x?q#h", "", "")])
def test_leaving_the_root_is_denied(dest, f, t, assert_mdq_error):
    with assert_mdq_error("MDQ_SECURITY_DENIED"):
        rl(dest, f, t)


@pytest.mark.parametrize("d,code", [("/abs", "MDQ_SECURITY_DENIED"), ("a\\b", "MDQ_INVALID_SYNTAX"), ("a/", "MDQ_INVALID_SYNTAX"),
                                    ("https:x", "MDQ_SECURITY_DENIED"), ("../x", "MDQ_SECURITY_DENIED")])
def test_bad_directories(d, code, assert_mdq_error):
    with assert_mdq_error(code):
        rl("a.png", d, "")
    with assert_mdq_error(code):
        rl("a.png", "", d)


def test_dot_directory_is_the_root():
    assert rl("a.png", ".", "") == "a.png"


@pytest.mark.parametrize("args", [(1, "a", "b"), ("a", 1, "b"), ("a", "b", None)])
def test_rebase_link_type_errors(args):
    with pytest.raises(TypeError):
        rl(*args)


# ---------------------------------------------------------------- rebase_links: pinned
@pytest.mark.parametrize("src,expected", [
    ("![Diagram](images/flow.png)\n", "![Diagram](chapters/images/flow.png)\n"),
    ("[a](b.md)\n", "[a](chapters/b.md)\n"),
    ("[a](b.md \"title\")\n", "[a](chapters/b.md \"title\")\n"),
    ("[a](b.md 'title')\n", "[a](chapters/b.md 'title')\n"),
    ("[a](b.md (title))\n", "[a](chapters/b.md (title))\n"),
    ("[a]( b.md )\n", "[a]( chapters/b.md )\n"),
    ("[a](b.md#h)\n", "[a](chapters/b.md#h)\n"),
    ("[a](#h)\n", "[a](#h)\n"),
    ("[a](<my file.md>)\n", "[a](<chapters/my file.md>)\n"),
    ("[a](<b.md>)\n", "[a](<chapters/b.md>)\n"),
    ("[a](x\\_y.md)\n", "[a](chapters/x_y.md)\n"),
    ("[a](a&amp;b.md)\n", "[a](chapters/a&b.md)\n"),
    ("[a](a&amp;b.md)\n", "[a](chapters/a&b.md)\n"),
    ("[a](https://x.y/z)\n", "[a](https://x.y/z)\n"),
    ("[a](mailto:x@y.z)\n", "[a](mailto:x@y.z)\n"),
    ("[a]()\n", "[a]()\n"),
    ("[a](<>)\n", "[a](<>)\n"),
    ("[![img](i.png)](p.md)\n", "[![img](chapters/i.png)](chapters/p.md)\n"),
    ("[a [b](c.md) d](e.md)\n", "[a [b](chapters/c.md) d](e.md)\n"),
    ("one [a](x.md) two ![b](y.png) three\n", "one [a](chapters/x.md) two ![b](chapters/y.png) three\n"),
    ("# Head [a](x.md)\n", "# Head [a](chapters/x.md)\n"),
    ("Setext [a](x.md)\n===\n", "Setext [a](chapters/x.md)\n===\n"),
    ("> quoted [a](x.md)\n", "> quoted [a](chapters/x.md)\n"),
    ("- item [a](x.md)\n", "- item [a](chapters/x.md)\n"),
    ("a [t](x.md\n\"title\") z\n", "a [t](chapters/x.md\n\"title\") z\n"),
    ("[a](x.md)\r\n[b](y.md)\r\n", "[a](chapters/x.md)\r\n[b](chapters/y.md)\r\n"),
    # reference definitions
    ("[r]: img/a.png\n", "[r]: chapters/img/a.png\n"),
    ("[r]: img/a.png \"T\"\n", "[r]: chapters/img/a.png \"T\"\n"),
    ("[r]: <img a.png>\n", "[r]: <chapters/img a.png>\n"),
    ("[r]: a.png\n[s]: b.png\n", "[r]: chapters/a.png\n[s]: chapters/b.png\n"),
    ("[r]: a.png\ntext [r]\n", "[r]: chapters/a.png\ntext [r]\n"),
    ("[r]: https://x.y\n", "[r]: https://x.y\n"),
    ("[a][r] [b][] [c]\n\n[r]: x.md\n", "[a][r] [b][] [c]\n\n[r]: chapters/x.md\n"),
    # not links
    ("text\n[r]: a.png\n", "text\n[r]: a.png\n"),
    ("[^1]: see a/b.md\n", "[^1]: see a/b.md\n"),
    ("[r]: a.png extra\n", "[r]: a.png extra\n"),
    ("`[a](x.md)`\n", "`[a](x.md)`\n"),
    ("``[a](x.md) ` ``\n", "``[a](x.md) ` ``\n"),
    ("\\[a](x.md)\n", "\\[a](x.md)\n"),
    ("[a\\](x.md)\n", "[a\\](x.md)\n"),
    ("[a] (x.md)\n", "[a] (x.md)\n"),
    ("[a][b](x.md)\n", "[a][b](chapters/x.md)\n"),
    ("```\n[a](x.md)\n```\n", "```\n[a](x.md)\n```\n"),
    ("    [a](x.md)\n", "    [a](x.md)\n"),
    ("<div>\n[a](x.md)\n</div>\n", "<div>\n[a](x.md)\n</div>\n"),
    ("<img src=\"x.png\">\n", "<img src=\"x.png\">\n"),
    ("<https://a.b/c(d)>\n", "<https://a.b/c(d)>\n"),
    ("[[wiki.md]] and ![[emb.md]]\n", "[[wiki.md]] and ![[emb.md]]\n"),
    ("plain text\n", "plain text\n"),
])
def test_rebase_links_pinned(src, expected):
    assert rls(src, "chapters", "") == expected


def test_same_directory_returns_text_unchanged():
    src = "[a](./x.md) [b](  y.md)\n"
    assert rls(src, "a", "a") == src


def test_destination_needing_angle_form_gets_it():
    assert rls("[a](x.md)\n", "my dir", "") == "[a](<my dir/x.md>)\n"
    assert rls("[a](x.md)\n", "a(b)", "") == "[a](<a(b)/x.md>)\n"


def test_angle_chars_are_escaped():
    assert rls("[a](<x>.md>)\n", "d", "") != ""  # not a link: unchanged
    assert rls("[a](<x\\>y.md>)\n", "d", "") == "[a](<d/x\\>y.md>)\n"


def test_rebased_text_renders_the_same_links():
    md = MarkdownIt("commonmark")
    src = "[a](<my file.md>) ![i](a&amp;b.png) [c](x\\_y.md#h)\n"
    out = rls(src, "d", "")
    hrefs = lambda s: [t.attrs.get("href") or t.attrs.get("src") for p in md.parse(s) if p.children for t in p.children
                       if t.type in ("link_open", "image")]
    assert hrefs(out) == ["d/my%20file.md", "d/a&b.png", "d/x_y.md#h"]


def test_error_position_and_first_line(assert_mdq_error):
    with assert_mdq_error("MDQ_SECURITY_DENIED", line=12, column=9):
        rls("text\n\nfoo [a](../x.md)\n", "", "a", first_line=10)
    with assert_mdq_error("MDQ_SECURITY_DENIED", line=2, column=6):
        rls("\n[r]: ../x\n", "", "a")


def test_rebase_links_type_errors():
    with pytest.raises(TypeError):
        rls(1, "a", "b")
    with pytest.raises(TypeError):
        rls("a", "a", "b", "1")


# ---------------------------------------------------------------- differential against markdown-it
DESTS = ["a.png", "b/c.md", "../d.md#x", "e/", "?q=1", "#h", "https://x.y/z", "./f.md", "g.md?a=1#b", "mailto:u@v.w"]
PIECES = [
    lambda d: f"[t]({d})", lambda d: f"![i]({d})", lambda d: f"[t]({d} \"ti tle\")", lambda d: f"`[t]({d})`",
    lambda d: f"\\[t]({d})", lambda d: f"[a [b]({d}) c](z.md)", lambda d: f"[t][r{DESTS.index(d)}]",
    lambda d: f"word {d} word", lambda d: f"[![i]({d})]({d})",
]


def hrefs(src):
    md = MarkdownIt("commonmark")
    out = []
    for blk in md.parse(src):
        for t in blk.children or []:
            if t.type == "link_open" and t.markup != "autolink":
                out.append(t.attrs["href"])
            elif t.type == "image":
                out.append(t.attrs["src"])
    return out


def expect(dest, f, t):
    if dest == "" or dest[0] in "#?/" or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", dest):
        return dest
    m = re.match(r"([^?#]*)(.*)", dest)
    path, suffix = m.group(1), m.group(2)
    ab = posixpath.normpath(posixpath.join(f or ".", path))
    rel = posixpath.relpath(ab, t or ".")
    if path.endswith("/") and not rel.endswith("/"):
        rel += "/"
    return rel + suffix


@given(st.lists(st.tuples(st.integers(0, len(PIECES) - 1), st.sampled_from(DESTS)), min_size=1, max_size=6),
       st.sampled_from([("a/b", ""), ("a/b", "a"), ("a", "a/b/c"), ("", "x"), ("a/b", "a/b"), ("p/q", "r/s")]),
       st.sampled_from(["\n", "\n\n", " "]))
def test_rebased_links_match_posixpath_oracle(items, dirs, sep):
    f, t = dirs
    if f == t:
        assert rls("[a](./x.md)", f, t) == "[a](./x.md)"  # same directory: text returned as written
        return
    body = sep.join(PIECES[i](d) for i, d in items)
    defs = "".join(f"\n\n[r{k}]: {d}" for k, d in enumerate(DESTS))
    src = body + defs + "\n"
    try:
        out = rls(src, f, t)
    except mdq.MdqError as e:
        assert e.code == "MDQ_SECURITY_DENIED"
        assert any(posixpath.normpath(posixpath.join(f or ".", re.match(r"[^?#]*", d).group(0) or ".")).startswith("..")
                   for _, d in items + [(0, d) for d in DESTS]
                   if d and d[0] not in "#?/" and not re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", d))
        return
    assert hrefs(out) == [expect(h, f, t) for h in hrefs(src)], (src, out)


# ---------------------------------------------------------------- import_expand integration
class FS:
    def __init__(self, files):
        self.files, self.reads = files, []

    def __call__(self, p):
        self.reads.append(p)
        return self.files.get(p)


def expand(root, files, path="index.md", **kw):
    return mi.import_expand(root, path, FS(files), **kw)["text"]


def test_spec_example_chapters_intro_into_index():
    files = {"chapters/intro.md": "# Intro\n\n![Diagram](images/flow.png)\n"}
    assert expand("![[chapters/intro.md]]\n", files) == "# Intro\n\n![Diagram](chapters/images/flow.png)\n"


def test_root_links_are_untouched():
    assert expand("[a](x.md)\n\n![[b/c.md]]\n", {"r/b/c.md": "z\n"}, path="r/index.md") == "[a](x.md)\n\nz\n"


def test_imported_links_are_relative_to_the_root_files_directory():
    files = {"lib/a.md": "[x](y.md)\n"}
    assert expand("![[../lib/a.md]]\n", files, path="doc/index.md") == "[x](../lib/y.md)\n"


def test_nested_imports_rebase_from_their_own_directory():
    files = {
        "a/one.md": "[l1](p.md)\n\n![[b/two.md]]\n",
        "a/b/two.md": "[l2](q.md) ![i](../r.png)\n",
    }
    out = expand("![[a/one.md]]\n", files)
    assert out == "[l1](a/p.md)\n\n[l2](a/b/q.md) ![i](a/r.png)\n"


def test_section_and_block_embeds_are_rebased_too():
    files = {"c/f.md": "# T\n\n## S\n[a](x.md) ^k\n\n## U\n[b](y.md)\n"}
    assert expand("![[c/f.md#S]]\n", files) == "## S\n[a](c/x.md) ^k\n\n"
    assert expand("![[c/f.md#^k]]\n", files) == "[a](c/x.md) ^k\n"


def test_code_in_imported_files_is_verbatim():
    files = {"c/f.md": "```\n[a](x.md)\n```\n\n`[b](y.md)`\n"}
    assert expand("![[c/f.md]]\n", files) == files["c/f.md"]


def test_reference_definitions_in_imported_files_are_rebased():
    files = {"c/f.md": "[a][r]\n\n[r]: img/a.png \"T\"\n"}
    assert expand("![[c/f.md]]\n", files) == "[a][r]\n\n[r]: c/img/a.png \"T\"\n"


def test_rebase_off_keeps_text_verbatim():
    files = {"c/f.md": "[a](x.md)\n"}
    assert expand("![[c/f.md]]\n", files, rebase=False) == "[a](x.md)\n"


def test_asset_leaving_the_root_fails_at_the_link(assert_mdq_error):
    files = {"c/f.md": "line\n\nsee [a](../../x.md)\n"}
    with assert_mdq_error("MDQ_SECURITY_DENIED", path="c/f.md", line=3, column=9):
        expand("![[c/f.md]]\n", files)


def test_link_outside_the_selected_section_is_not_examined():
    files = {"c/f.md": "# A\nfine\n\n# B\n[bad](../../x.md)\n"}
    assert expand("![[c/f.md#A]]\n", files) == "# A\nfine\n\n"


def test_rebase_must_be_bool():
    with pytest.raises(TypeError):
        mi.import_expand("a", "a.md", FS({}), rebase="yes")


def test_source_map_unaffected_by_rebasing():
    files = {"c/f.md": "---\nk: 1\n---\n[a](x.md)\n\nmore\n"}
    r = mi.import_expand("top\n\n![[c/f.md]]\n", "index.md", FS(files))
    assert r["text"] == "top\n\n[a](c/x.md)\n\nmore\n"
    assert [(m["path"], m["src_start"]) for m in r["map"]] == [("index.md", 1), ("c/f.md", 4)]
