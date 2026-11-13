"""Epic 4 / E4.4 (F07): `import_expand(text, path, read, max_depth=64, max_expansions=10000, max_bytes=16 MiB)`.

`![[path]]`, `![[path#Heading]]` and `![[path#^id]]` stand alone in a paragraph at column 1 and are
replaced by the target's body (frontmatter removed), its first matching heading section, or one block,
verbatim (markers and heading levels kept). `read(path)` gets a root-relative path and returns the
file text or None; each path is read at most once. Returns {"text", "map"}; map entries are
{out_start, out_end (0-based, exclusive), path, src_start (1-based file line)}. G35.
"""
import time

import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit


class FS:
    def __init__(self, files):
        self.files, self.reads = files, []

    def __call__(self, path):
        self.reads.append(path)
        return self.files.get(path)


def run(root, files=None, path="main.md", **kw):
    fs = FS(dict(files or {}))
    return mi.import_expand(root, path, fs, **kw)["text"], fs


def err(code, root, files=None, path="main.md", **kw):
    with pytest.raises(mdq.MdqError) as e:
        run(root, files, path, **kw)
    assert e.value.code == code, str(e.value)
    return e.value


# ---------------------------------------------------------------- basics
def test_no_embeds_passthrough_and_no_reads():
    src = "# T\n\ntext\r\nmore\rend"
    out, fs = run(src)
    assert out == src and fs.reads == []


def test_root_frontmatter_removed():
    assert run("---\na: 1\n---\n# T\n")[0] == "# T\n"


def test_full_import_strips_frontmatter():
    out, fs = run("a\n\n![[b.md]]\n\nz\n", {"b.md": "---\nx: 1\n---\n# B\nbody\n"})
    assert out == "a\n\n# B\nbody\n\nz\n" and fs.reads == ["b.md"]


def test_import_at_end_without_newline_and_unterminated_target():
    assert run("a\n\n![[b.md]]", {"b.md": "tail"})[0] == "a\n\ntail"
    assert run("![[b.md]]\n\nz", {"b.md": "tail"})[0] == "tail\n\nz"


def test_trailing_space_after_embed_and_crlf():
    assert run("![[b.md]]  \r\n\r\nz\r\n", {"b.md": "x\r\n"})[0] == "x\r\n\r\nz\r\n"


def test_empty_target():
    assert run("a\n\n![[b.md]]\n\nz\n", {"b.md": ""})[0] == "a\n\n\nz\n"


B = "# B\nintro ^i\n\n## C\nc text\n\n### D\nd text\n\n## E\ne text\n\n# F\nf text\n"


def test_section_stops_at_equal_or_higher_level():
    out, _ = run("![[b.md#C]]\n", {"b.md": B})
    assert out == "## C\nc text\n\n### D\nd text\n\n"
    assert run("![[b.md#D]]\n", {"b.md": B})[0] == "### D\nd text\n\n"
    assert run("![[b.md#B]]\n", {"b.md": B})[0] == B[:B.index("# F")]
    assert run("![[b.md#F]]\n", {"b.md": B})[0] == "# F\nf text\n"


def test_section_first_duplicate_wins():
    f = "# T\n\n## Dup\nfirst\n\n## Dup\nsecond\n"
    assert run("![[f.md#Dup]]\n", {"f.md": f})[0] == "## Dup\nfirst\n\n"


def test_section_normalisation_and_markup():
    f = "## *Caf\u00e9*  &amp; Bar\nx\n"
    assert run("![[f.md#Caf\u00e9 & Bar]]\n", {"f.md": f})[0] == f
    assert run("![[f.md#Cafe\u0301 & Bar]]\n", {"f.md": f})[0] == f


def test_block_import_keeps_marker_and_only_that_block():
    out, _ = run("![[b.md#^i]]\n", {"b.md": B})
    assert out == "intro ^i\n"


def test_block_import_on_heading_is_heading_only():
    f = "## Sec ^s\ntext\n"
    assert run("![[f.md#^s]]\n", {"f.md": f})[0] == "## Sec ^s\n"


def test_multi_line_block():
    f = "a\nb ^k\n\nz\n"
    assert run("![[f.md#^k]]\n", {"f.md": f})[0] == "a\nb ^k\n"


def test_expansion_is_verbatim_for_markers_and_levels():
    out, _ = run("# Top\n\n![[b.md#C]]\n", {"b.md": "## C ^cc\ntext\n"})
    assert out == "# Top\n\n## C ^cc\ntext\n"


# ---------------------------------------------------------------- paths
def test_relative_to_importing_file_and_nested():
    files = {
        "book/chapters/intro.md": "intro\n\n![[images.md]]\n\n![[../shared/x.md]]\n",
        "book/chapters/images.md": "IMG\n",
        "book/shared/x.md": "X\n",
    }
    out, fs = run("![[chapters/intro.md]]\n", files, path="book/index.md")
    assert out == "intro\n\nIMG\n\nX\n"
    assert fs.reads == ["book/chapters/intro.md", "book/chapters/images.md", "book/shared/x.md"]


def test_case_sensitive_paths():
    err("MDQ_MISSING_IMPORT", "![[B.md]]\n", {"b.md": "x"})


def test_each_path_read_once_even_when_imported_repeatedly():
    out, fs = run("![[b.md]]\n\n![[b.md]]\n\n![[b.md#^i]]\n", {"b.md": B})
    assert fs.reads == ["b.md"] and out.count("# B") == 2


def test_unreferenced_files_never_read():
    _, fs = run("![[a.md]]\n", {"a.md": "x\n", "other.md": "y\n"})
    assert fs.reads == ["a.md"]


def test_root_path_validated():
    with pytest.raises(mdq.MdqError) as e:
        mi.import_expand("x", "../x.md", FS({}))
    assert e.value.code == "MDQ_SECURITY_DENIED"


@pytest.mark.parametrize("ref,code", [
    ("../x.md", "MDQ_SECURITY_DENIED"), ("/x.md", "MDQ_SECURITY_DENIED"), ("https://x/y.md", "MDQ_SECURITY_DENIED"),
    ("a\\b.md", "MDQ_INVALID_SYNTAX"), ("a//b.md", "MDQ_INVALID_SYNTAX"), ("a/", "MDQ_INVALID_SYNTAX"),
])
def test_bad_paths_fail_at_the_embed_without_reading(ref, code):
    e = err(code, f"a\n\n![[{ref}]]\n", {})
    assert (e.path, e.line, e.column) == ("main.md", 3, 1)


# ---------------------------------------------------------------- failures and positions
def test_missing_import_position_counts_frontmatter():
    e = err("MDQ_MISSING_IMPORT", "---\nk: v\n---\ntext\n\n![[nope.md]]\n", {})
    assert (e.path, e.line, e.column) == ("main.md", 6, 1)


def test_missing_import_inside_nested_file_names_that_file():
    e = err("MDQ_MISSING_IMPORT", "![[a.md]]\n", {"a.md": "x\n\n![[gone.md]]\n"})
    assert (e.path, e.line) == ("a.md", 3)


def test_unresolved_heading_and_block_report_the_embed_site():
    for frag in ("Nope", "^nope"):
        e = err("MDQ_UNRESOLVED_REFERENCE", f"x\n\n![[b.md#{frag}]]\n", {"b.md": B})
        assert (e.path, e.line, e.column) == ("main.md", 3, 1)


def test_invalid_block_id_in_fragment():
    err("MDQ_INVALID_SYNTAX", "![[b.md#^1a]]\n", {"b.md": B})


def test_duplicate_id_in_target_found_when_indexing_it():
    err("MDQ_DUPLICATE_ID", "![[b.md#X]]\n", {"b.md": "# X\n\na ^k\n\nb ^k\n"})


def test_bad_frontmatter_in_target_names_target():
    e = err("MDQ_METADATA_ERROR", "![[b.md]]\n", {"b.md": "---\nunclosed: 1\n"})
    assert e.path == "b.md"


# ---------------------------------------------------------------- cycles
def test_cycle_self():
    e = err("MDQ_IMPORT_CYCLE", "text\n\n![[main.md]]\n", {})
    assert "main.md -> main.md" in str(e) and (e.path, e.line) == ("main.md", 3)


def test_cycle_two_files():
    e = err("MDQ_IMPORT_CYCLE", "![[a.md]]\n", {"a.md": "![[main.md]]\n"})
    assert "main.md -> a.md -> main.md" in str(e)


def test_cycle_three_files():
    err("MDQ_IMPORT_CYCLE", "![[a.md]]\n", {"a.md": "![[b.md]]\n", "b.md": "![[c.md]]\n", "c.md": "![[a.md]]\n"})


def test_section_that_contains_its_own_import_is_a_cycle():
    f = "# H1\n\n![[f.md#H1]]\n\n# H2\n"
    err("MDQ_IMPORT_CYCLE", "![[f.md#H1]]\n", {"f.md": f})


def test_section_without_the_import_is_not_a_cycle():
    f = "# H1\n\n![[f.md#H2]]\n\n# H2\nplain\n"
    out, _ = run("![[f.md#H1]]\n", {"f.md": f})
    assert out == "# H1\n\n# H2\nplain\n\n"


def test_whole_file_importing_its_own_other_section_is_fine():
    f = "# A\nx\n\n# B\n\n![[f.md#A]]\n"
    assert run("![[f.md]]\n", {"f.md": f})[0] == "# A\nx\n\n# B\n\n# A\nx\n\n"


def test_whole_file_importing_its_section_that_imports_the_file_is_a_cycle():
    f = "# A\n\n![[f.md]]\n"
    err("MDQ_IMPORT_CYCLE", "![[f.md]]\n", {"f.md": f})


def test_same_file_twice_in_sequence_is_not_a_cycle():
    assert run("![[a.md]]\n\n![[a.md]]\n", {"a.md": "x\n"})[0] == "x\n\nx\n"


# ---------------------------------------------------------------- limits
def chain(n):
    files = {f"f{i}.md": f"![[f{i + 1}.md]]\n" for i in range(n)}
    files[f"f{n}.md"] = "end\n"
    return files


def test_depth_limit():
    assert run("![[f0.md]]\n", chain(5), max_depth=7)[0] == "end\n"
    e = err("MDQ_RESOURCE_LIMIT", "![[f0.md]]\n", chain(5), max_depth=4)
    assert "deeper" in str(e)


def test_default_depth_is_64():
    assert run("![[f0.md]]\n", chain(62))[0] == "end\n"
    err("MDQ_RESOURCE_LIMIT", "![[f0.md]]\n", chain(80))


def test_expansion_bomb_fails_fast():
    files = {f"f{i}.md": f"![[f{i + 1}.md]]\n\n![[f{i + 1}.md]]\n" for i in range(24)}
    files["f24.md"] = "leaf\n"
    t = time.time()
    e = err("MDQ_RESOURCE_LIMIT", "![[f0.md]]\n", files, max_depth=100)
    assert time.time() - t < 5
    assert "imports expanded" in str(e)


def test_output_byte_limit():
    files = {"b.md": "x" * 100 + "\n"}
    root = "![[b.md]]\n\n" * 10
    err("MDQ_RESOURCE_LIMIT", root, files, max_bytes=500)
    assert len(run(root, files, max_bytes=2000)[0]) > 1000


def test_max_expansions():
    files = {"b.md": "x\n"}
    err("MDQ_RESOURCE_LIMIT", "![[b.md]]\n\n![[b.md]]\n\n![[b.md]]\n", files, max_expansions=2)
    run("![[b.md]]\n\n![[b.md]]\n", files, max_expansions=2)


# ---------------------------------------------------------------- what is (not) an embed
@pytest.mark.parametrize("src", [
    "`![[b.md]]`\n",
    "``![[b.md]]``\n",
    "```\n![[b.md]]\n```\n",
    "~~~\n![[b.md]]\n~~~\n",
    "    ![[b.md]]\n",
    "<div>\n![[b.md]]\n</div>\n",
    "\\![[b.md]]\n",
    "!\\[[b.md]]\n",
    "![[b.md\n",
    "![[b.md]\n",
    "![[ab[c.md]]\n",
    "[[b.md]]\n",
    "[[b.md#H]]\n",
    "![b.md](b.md)\n",
    "<span title=\"![[b.md]]\">x</span>\n",
])
def test_not_an_embed_stays_literal_and_is_never_read(src):
    out, fs = run(src, {"b.md": "X\n"})
    assert out == src and fs.reads == []


@pytest.mark.parametrize("src", [
    "see ![[b.md]] here\n",
    "![[b.md]] trailing\n",
    "![[b.md]] ![[c.md]]\n",
    "![[b.md]]\n![[c.md]]\n",
    "text\n![[b.md]]\n",
    "![[b.md]]\ntext\n",
    "> ![[b.md]]\n",
    "- ![[b.md]]\n",
    "1. item\n\n   ![[b.md]]\n",
    "# ![[b.md]]\n",
    "  ![[b.md]]\n",
    "![[b.md|label]]\n",
    "![[]]\n",
    "![[#H]]\n",
    "![[b.md#]]\n",
])
def test_misplaced_or_malformed_embed_fails_without_reading(src):
    fs = FS({"b.md": "X\n", "c.md": "Y\n"})
    with pytest.raises(mdq.MdqError) as e:
        mi.import_expand(src, "main.md", fs)
    assert e.value.code == "MDQ_INVALID_SYNTAX" and fs.reads == []


def test_invalid_syntax_position():
    with pytest.raises(mdq.MdqError) as e:
        mi.import_expand("a\n\nsee ![[b.md]] here\n", "main.md", FS({}))
    assert (e.value.path, e.value.line, e.value.column) == ("main.md", 3, 5)


def test_embed_after_code_span_with_backticks_is_found():
    out, _ = run("`a`\n\n![[b.md]]\n", {"b.md": "X\n"})
    assert out == "`a`\n\nX\n"


# ---------------------------------------------------------------- host callback behaviour
def test_reader_exception_propagates():
    class Boom(Exception):
        pass

    def read(p):
        raise Boom(p)

    with pytest.raises(Boom):
        mi.import_expand("![[b.md]]\n", "main.md", read)


def test_reader_bad_return_type():
    with pytest.raises(TypeError):
        mi.import_expand("![[b.md]]\n", "main.md", lambda p: b"bytes")


@pytest.mark.parametrize("args", [(1, "a", len), ("a", 1, len), ("a", "a", None), ("a", "a", "x")])
def test_type_errors(args):
    with pytest.raises(TypeError):
        mi.import_expand(*args)


@pytest.mark.parametrize("kw", [{"max_depth": "1"}, {"max_expansions": "1"}, {"max_bytes": "1"}, {"max_depth": 1.5}])
def test_limit_type_errors(kw):
    with pytest.raises(TypeError):
        mi.import_expand("a", "a.md", FS({}), **kw)


# ---------------------------------------------------------------- source map
def map_check(root, files, path="main.md"):
    fs = FS(dict(files))
    r = mi.import_expand(root, path, fs)
    out_lines = r["text"].splitlines(keepends=True)
    srcs = dict(files); srcs[path] = root
    prev_end = 0
    for m in r["map"]:
        assert m["out_start"] >= prev_end and m["out_end"] > m["out_start"]
        prev_end = m["out_end"]
        body = mi.frontmatter_split(srcs[m["path"]])
        # source lines are 1-based file lines
        file_lines = srcs[m["path"]].splitlines(keepends=True)
        for k in range(m["out_end"] - m["out_start"]):
            assert out_lines[m["out_start"] + k] == file_lines[m["src_start"] - 1 + k], (m, k)
    covered = sum(m["out_end"] - m["out_start"] for m in r["map"])
    assert covered == len(out_lines)
    return r


def test_source_map_points_to_original_lines():
    files = {"b.md": "---\nk: v\n---\n# B\nbody\n\n## C\nc text\n\n## E\ne\n", "c.md": "one\ntwo\n"}
    root = "---\nt: 1\n---\n# Main\n\n![[b.md#C]]\n\n![[c.md]]\n\nend\n"
    r = map_check(root, files)
    assert r["map"][0] == {"out_start": 0, "out_end": 2, "path": "main.md", "src_start": 4}
    assert r["map"][1]["path"] == "b.md" and r["map"][1]["src_start"] == 7
    assert r["map"][-1]["path"] == "main.md"


def test_source_map_nested():
    files = {"a.md": "A1\n\n![[b.md]]\n\nA2\n", "b.md": "B1\nB2\n"}
    r = map_check("![[a.md]]\n", files)
    assert [(m["path"], m["src_start"]) for m in r["map"]] == [("a.md", 1), ("b.md", 1), ("a.md", 4)]


# ---------------------------------------------------------------- laziness with containers
def test_excluded_if_branch_never_reads():
    root = ':::if {var="mode" equals="full"}\n![[full.md]]\n:::\n\n![[always.md]]\n'
    files = {"full.md": "FULL\n", "always.md": "ALWAYS\n"}
    ev = mi.container_eval(root, {"mode": "short"})
    fs = FS(files)
    assert mi.import_expand(ev, "main.md", fs)["text"] == "\nALWAYS\n"
    assert fs.reads == ["always.md"]
    fs2 = FS(files)
    ev2 = mi.container_eval(root, {"mode": "full"})
    assert mi.import_expand(ev2, "main.md", fs2)["text"] == "FULL\n\nALWAYS\n"


# ---------------------------------------------------------------- differential
C = "# C\ntext c\n\n## H\nsection c\n\n## J\nmore\n"
FILES = {"c.md": C}
BLOCKS = ["para", "# Head", "![[c.md]]", "![[c.md#H]]", "![[c.md#^cb]]", "![[b.md]]", "```\n![[c.md]]\n```", "text ^zz"]
FILES["c.md"] = C.replace("text c", "text c ^cb")
B_BLOCKS = ["bpara", "## BH", "![[c.md#J]]", "![[c.md#H]]"]


def oracle(text, files, base_files):
    import re
    out, fence = [], False
    for ln in text.split("\n"):
        if ln.startswith("```"):
            fence = not fence
        m = re.fullmatch(r"!\[\[([^\]#]+)(?:#(.+))?\]\]", ln)
        if m and not fence:
            body = files[m.group(1)]
            lines = body.split("\n")[:-1]
            if m.group(2) is None:
                chunk = lines
            elif m.group(2).startswith("^"):
                idx = next(i for i, l in enumerate(lines) if l.endswith(" " + m.group(2)))
                chunk = [lines[idx]]
            else:
                i0 = next(i for i, l in enumerate(lines) if re.fullmatch(r"#+ " + re.escape(m.group(2)), l))
                lvl = len(lines[i0].split(" ")[0])
                i1 = next((i for i in range(i0 + 1, len(lines)) if re.match(r"#{1,%d} " % lvl, lines[i])), len(lines))
                chunk = lines[i0:i1]
            sub = oracle("\n".join(chunk) + "\n", files, base_files)
            out.extend(sub.split("\n")[:-1])
        else:
            out.append(ln)
    return "\n".join(out) if out and out[-1] == "" else "\n".join(out)


@given(st.lists(st.sampled_from(BLOCKS), min_size=1, max_size=6), st.lists(st.sampled_from(B_BLOCKS), min_size=1, max_size=4))
def test_expansion_matches_line_oracle(blocks, bblocks):
    files = dict(FILES)
    files["b.md"] = "\n\n".join(bblocks) + "\n"
    root = "\n\n".join(blocks) + "\n"
    fs = FS(files)
    r = mi.import_expand(root, "main.md", fs)
    assert r["text"] == oracle(root, files, files), (root, files["b.md"])
    # every output line is attributable to the right source line
    srcs = dict(files); srcs["main.md"] = root
    out_lines = r["text"].splitlines(keepends=True)
    for m in r["map"]:
        file_lines = srcs[m["path"]].splitlines(keepends=True)
        for k in range(m["out_end"] - m["out_start"]):
            assert out_lines[m["out_start"] + k] == file_lines[m["src_start"] - 1 + k]
    assert sorted(set(fs.reads)) == sorted(fs.reads)
