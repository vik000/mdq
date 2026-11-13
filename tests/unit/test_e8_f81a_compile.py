"""Epic 8 / E8.1a (F25): `mdq.compile` and `mdq.evaluate` for one document from a file system. G50.

Pipeline (spec 9): decode + frontmatter; metadata cascade (root `.mdq.yaml`, each `.mdq.yaml` down to the
entry's directory, frontmatter, sidecar `<stem>.meta.yaml`, host `inputs` on top as `variables.*`); scope
`document.*` + every `variables:` entry as a top-level name + reserved `calc.*`; `:::if`/`:::for` and `{{...}}`
per file with `{{calc.*}}` deferred, imports of already-evaluated files; calc blocks (G11: bare names = earlier
assignments, then numeric entries under `variables:`); `{{calc.*}}`; block-ID markers removed; export fallbacks
(compile only); ```calc blocks removed unless `calc_blocks="keep"`. Sources are never modified.
"""
import hashlib
import os
from decimal import Decimal
from pathlib import Path, PurePosixPath

import pytest

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

META, UNSUP, DENIED, SYNTAX, MISSING = ("MDQ_METADATA_ERROR", "MDQ_UNSUPPORTED_FEATURE", "MDQ_SECURITY_DENIED",
                                        "MDQ_INVALID_SYNTAX", "MDQ_MISSING_IMPORT")
CALC, UNDEF, LIMIT = "MDQ_CALC_ERROR", "MDQ_UNDEFINED_VARIABLE", "MDQ_RESOURCE_LIMIT"
FENCE = "```"


def calc(*lines):
    return FENCE + "calc\n" + "".join(l + "\n" for l in lines) + FENCE + "\n"


class Recording:
    """A file system that logs every read (to prove what is and is not read)."""

    def __init__(self, files):
        self.files, self.reads = dict(files), []

    def read(self, path):
        self.reads.append(path)
        return self.files.get(path)


def md(entry, files, **kw):
    return mdq.compile(entry, files, **kw).markdown


# ---------------------------------------------------------------- basics and result types
def test_spec_example_calc_block():
    doc = "---\nvariables:\n  price: 120 EUR\n---\n" + calc("quantity = 4", "subtotal = price * quantity",
                                                             "tax = subtotal * 21%", "total = subtotal + tax") + \
        "\nTotal: {{calc.total}}\n"
    r = mdq.compile("doc.md", {"doc.md": doc})
    assert r.markdown == "\nTotal: 580.8 EUR\n"
    assert isinstance(r, mdq.Result) and isinstance(r.calc[0], mdq.CalcBlock)
    total = r.calc[0].results[-1]
    assert total == mdq.CalcValue("total", 9, 1, "money", Decimal("580.8"), None, "EUR", "580.8 EUR")
    assert isinstance(total.value, Decimal)
    assert r.calc[0].path == "doc.md" and r.calc[0].line == 5
    assert r.calc[0].source == "quantity = 4\nsubtotal = price * quantity\ntax = subtotal * 21%\ntotal = subtotal + tax"


def test_plain_markdown_passes_through():
    text = "# Title\n\nSome *text* and `code`.\n\n- a\n- b\n"
    assert md("a.md", {"a.md": text}) == text


def test_results_are_frozen_and_deterministic():
    files = {"a.md": calc("x = 1 / 3") + "x: {{calc.x}}\n"}
    a, b = mdq.compile("a.md", files), mdq.compile("a.md", files)
    assert a == b and a.markdown == "x: 0.3333333333333333333333333333\n"
    with pytest.raises(Exception):
        a.markdown = "x"  # frozen dataclass


def test_sources_are_never_modified(tmp_path):
    files = {"a.md": "---\nt: x\n---\n# {{document.t}} ^id\n\n" + calc("y = 2") + "{{calc.y}}\n"}
    snapshot = dict(files)
    mdq.compile("a.md", files)
    assert files == snapshot
    (tmp_path / "a.md").write_text(files["a.md"])
    (tmp_path / "a.meta.yaml").write_text("s: 1\n")
    before = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    out = mdq.compile("a.md", mdq.DirectoryFS(tmp_path))
    after = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    assert before == after and out.markdown == "# x\n\n2\n"


# ---------------------------------------------------------------- metadata cascade
def test_cascade_order_and_provenance():
    files = {
        ".mdq.yaml": "a: project\nb: project\nc: project\nd: project\ne: project\n",
        "x/.mdq.yaml": "b: dir-x\nc: dir-x\nd: dir-x\ne: dir-x\n",
        "x/y/.mdq.yaml": "c: dir-y\nd: dir-y\ne: dir-y\n",
        "x/y/doc.md": "---\nd: frontmatter\ne: frontmatter\n---\n{{document.a}} {{document.b}} {{document.c}} "
                      "{{document.d}} {{document.e}}\n",
        "x/y/doc.meta.yaml": "e: sidecar\n",
    }
    r = mdq.compile("x/y/doc.md", files)
    assert r.markdown == "project dir-x dir-y frontmatter sidecar\n"
    prov = dict(r.provenance)
    assert prov == {"/a": ".mdq.yaml", "/b": "x/.mdq.yaml", "/c": "x/y/.mdq.yaml", "/d": "x/y/doc.md", "/e": "x/y/doc.meta.yaml"}


def test_inputs_are_the_top_layer():
    files = {"d.md": "---\nvariables:\n  n: 1\n---\n{{n}}\n", "d.meta.yaml": "variables:\n  n: 2\n"}
    assert md("d.md", files) == "2\n"
    r = mdq.compile("d.md", files, inputs={"n": Decimal("3")})
    assert r.markdown == "3\n" and dict(r.provenance)["/variables/n"] == "<inputs>"


def test_sidecar_stem_and_unrelated_metadata_files():
    files = {"r.v2.md": "{{document.k}}\n", "r.v2.meta.yaml": "k: yes-sidecar\n", "r.meta.yaml": "k: wrong\n",
             "other/.mdq.yaml": "k: not-on-the-path\n"}
    assert md("r.v2.md", files) == "yes-sidecar\n"


def test_metadata_errors_carry_the_file(assert_mdq_error):
    with assert_mdq_error(META, path="x/.mdq.yaml", line=2):
        mdq.compile("x/d.md", {"x/d.md": "hi\n", "x/.mdq.yaml": "a: 1\na: 2\n"})
    with assert_mdq_error(META, path="d.md", line=3):
        mdq.compile("d.md", {"d.md": "---\na: 1\na: 2\n---\nx\n"})
    with assert_mdq_error(SYNTAX, path="d.meta.yaml"):
        mdq.compile("d.md", {"d.md": "x\n", "d.meta.yaml": b"a: \xff\n"})


@pytest.mark.parametrize("meta,code", [
    ("mdq: '0.2'", UNSUP), ("mdq: 0.1", META), ("mdq: [x]", META),
    ("features: [core, bogus]", UNSUP), ("features: core", META), ("features: [1]", META),
])
def test_version_and_features(assert_mdq_error, meta, code):
    with assert_mdq_error(code):
        mdq.compile("d.md", {"d.md": f"---\n{meta}\n---\nx\n"})


def test_all_profiles_supported():
    assert md("d.md", {"d.md": "---\nmdq: '0.1'\nfeatures: [core, rich, calc, package]\n---\nx\n"}) == "x\n"
    assert set(mi.supported_profiles()) == {"core", "rich", "calc", "package"}


# ---------------------------------------------------------------- namespaces
def test_namespaces():
    doc = ("---\ntitle: T\nvariables:\n  price: 2.5\n  proj:\n    name: Apollo\n    year: 1969\n---\n"
           "{{document.title}} {{price}} {{proj.name}} {{proj.year}} {{document.variables.price}}\n")
    assert md("d.md", {"d.md": doc}) == "T 2.5 Apollo 1969 2.5\n"


def test_frontmatter_keys_are_not_top_level_names(assert_mdq_error):
    with assert_mdq_error(UNDEF, path="d.md", line=4):
        mdq.compile("d.md", {"d.md": "---\ntitle: T\n---\n{{title}}\n"})


@pytest.mark.parametrize("name", ["document", "calc"])
def test_reserved_namespaces(assert_mdq_error, name):
    with assert_mdq_error(META):
        mdq.compile("d.md", {"d.md": f"---\nvariables:\n  {name}: 1\n---\nx\n"})


@pytest.mark.parametrize("value", ["./data/project.yaml", "../p.yml", "/abs.json", "./X.YAML"])
def test_variable_files_not_supported_yet(assert_mdq_error, value):
    with assert_mdq_error(UNSUP):
        mdq.compile("d.md", {"d.md": f"---\nvariables:\n  project: {value}\n---\nx\n"})


def test_variables_must_be_a_mapping(assert_mdq_error):
    with assert_mdq_error(META):
        mdq.compile("d.md", {"d.md": "---\nvariables: [a]\n---\nx\n"})


# ---------------------------------------------------------------- calc (G11)
def test_calc_sees_variables_entries_only(assert_mdq_error):
    files = {"d.md": "---\ntitle: 5\nvariables:\n  qty: 4\n  dist: 12 km\n  money: 3 EUR\n  word: hello\n---\n"
                     + calc("a = qty * 2", "b = dist to m", "c = money * qty") + "{{calc.a}} {{calc.b}} {{calc.c}}\n"}
    assert md("d.md", files) == "8 12000 m 12 EUR\n"
    for expr in ("x = title", "x = word", "x = nope"):
        with assert_mdq_error(CALC, path="d.md"):
            mdq.compile("d.md", {"d.md": "---\ntitle: 5\nvariables:\n  word: hello\n---\n" + calc(expr)})


def test_calc_inputs_from_the_host():
    files = {"d.md": calc("t = price * n") + "t: {{calc.t}}\n"}
    assert md("d.md", files, inputs={"price": Decimal("2.50"), "n": 3}) == "t: 7.5\n"
    assert md("d.md", files, inputs={"price": "2 EUR", "n": 3}) == "t: 6 EUR\n"
    with pytest.raises(TypeError):
        mdq.compile("d.md", files, inputs={"price": 2.5, "n": 3})


def test_local_assignment_shadows_variables():
    files = {"d.md": "---\nvariables:\n  x: 10\n---\n" + calc("x = 1", "y = x + 1") + "{{calc.y}} {{x}}\n"}
    assert md("d.md", files) == "2 10\n"


def test_duplicate_export_across_blocks(assert_mdq_error):
    files = {"d.md": calc("a = 1") + "\n" + calc("a = 2")}
    with assert_mdq_error(CALC, path="d.md", line=6) as info:
        mdq.compile("d.md", files)
    assert "d.md:1" in str(info.value)


def test_calc_error_line_is_exact_after_removed_branches(assert_mdq_error):
    doc = ":::if {var=\"flag\" equals=\"no\"}\nlong\nexcluded\nbranch\n:::\n\n" + calc("a = 1", "b = a / 0")
    with assert_mdq_error(CALC, path="d.md", line=9):
        mdq.compile("d.md", {"d.md": doc}, inputs={"flag": "yes"})


def test_calc_inside_excluded_branch_is_not_evaluated():
    doc = ":::if {var=\"flag\" equals=\"on\"}\n" + calc("a = 1 / 0") + ":::\nok\n"
    r = mdq.compile("d.md", {"d.md": doc}, inputs={"flag": "off"})
    assert r.markdown == "ok\n" and r.calc == ()


def test_calc_inside_a_loop_exports_twice(assert_mdq_error):
    doc = "---\nvariables:\n  items: [1, 2]\n---\n:::for {each=\"items\" as=\"i\"}\n" + calc("a = 1") + ":::\n"
    with assert_mdq_error(CALC):
        mdq.compile("d.md", {"d.md": doc})


def test_calc_results_cannot_drive_conditions(assert_mdq_error):
    doc = calc("a = 1") + ":::if {var=\"calc.a\" equals=\"1\"}\nx\n:::\n"
    with assert_mdq_error(UNSUP, path="d.md", line=4):
        mdq.compile("d.md", {"d.md": doc})


def test_calc_in_quotes_or_lists_is_refused(assert_mdq_error):
    for doc in ("> ```calc\n> a = 1\n> ```\n", "- ```calc\n  a = 1\n  ```\n"):
        with assert_mdq_error(UNSUP, path="d.md", line=1):
            mdq.compile("d.md", {"d.md": doc})


def test_undefined_calc_reference_is_located(assert_mdq_error):
    with assert_mdq_error(UNDEF, path="d.md", line=5):
        mdq.compile("d.md", {"d.md": "---\nk: 1\n---\nx\n{{calc.missing}}\n"})


def test_calc_reference_inside_a_loop():
    doc = ("---\nvariables:\n  items: [a, b]\n---\n" + calc("n = 2") +
           ":::for {each=\"items\" as=\"i\"}\n{{i}}={{calc.n}}\n:::\n")
    assert md("d.md", {"d.md": doc}) == "a=2\nb=2\n"


@pytest.mark.parametrize("info", ["calc", "calc extra", "  calc"])
def test_calc_info_string(info):
    doc = f"{FENCE}{info}\nv = 7\n{FENCE}\n{{{{calc.v}}}}\n"
    assert md("d.md", {"d.md": doc}) == "7\n"


def test_tilde_fence_and_unclosed_fence():
    assert md("d.md", {"d.md": "~~~calc\nv = 7\n~~~\n{{calc.v}}\n"}) == "7\n"
    r = mdq.compile("d.md", {"d.md": "text\n\n```calc\nv = 7\n"})
    assert r.calc[0].results[0].value == 7 and r.markdown == "text\n\n"


def test_other_code_blocks_are_untouched():
    doc = "```python\nx = {{calc.nope}}\n```\n\n```calculator\nnot calc\n```\n"
    assert md("d.md", {"d.md": doc}) == doc


# ---------------------------------------------------------------- calc_blocks and evaluate
def test_calc_blocks_keep_and_remove():
    doc = "before\n\n" + calc("a = 1") + "\nafter {{calc.a}}\n"
    assert md("d.md", {"d.md": doc}) == "before\n\n\nafter 1\n"
    kept = mdq.compile("d.md", {"d.md": doc}, calc_blocks="keep")
    assert kept.markdown == doc.replace("{{calc.a}}", "1") and kept.calc[0].results[0].display == "1"
    with pytest.raises(ValueError):
        mdq.compile("d.md", {"d.md": doc}, calc_blocks="hide")


def test_evaluate_returns_data_only():
    doc = "---\nt: x\n---\n" + calc("a = 2 * 3 kg") + ":::unknown\nbody\n:::\n"
    with pytest.raises(mdq.MdqError) as e:
        mdq.compile("d.md", {"d.md": doc})
    assert e.value.code == UNSUP  # export refuses the unhandled container
    ev = mdq.evaluate("d.md", {"d.md": doc})
    assert isinstance(ev, mdq.Evaluation) and not hasattr(ev, "markdown")
    assert ev.metadata == {"t": "x"} and ev.calc[0].results[0].display == "6 kg"


# ---------------------------------------------------------------- imports
def test_imports_use_the_root_metadata_and_calc():
    # every calc block has its own scope (spec 8.1): `b` cannot use the root block's `a`, only `{{calc.a}}` can
    files = {"d.md": "---\nt: Root\n---\n" + calc("a = 5") + "\n![[parts/p.md]]\n",
             "parts/p.md": "---\nt: ignored\n---\nIn {{document.t}}: {{calc.a}} {{calc.b}}\n\n" + calc("b = 10")}
    r = mdq.compile("d.md", files)
    assert r.markdown == "\nIn Root: 5 10\n\n"
    assert [(b.path, b.line) for b in r.calc] == [("d.md", 4), ("parts/p.md", 6)]


def test_excluded_branch_never_reads_the_import():
    fs = Recording({"d.md": ":::if {var=\"on\" equals=\"yes\"}\n![[secret.md]]\n:::\n\nx\n", "secret.md": "s\n"})
    assert mdq.compile("d.md", fs, inputs={"on": "no"}).markdown == "\nx\n"
    assert "secret.md" not in fs.reads
    mdq.compile("d.md", fs, inputs={"on": "yes"})
    assert "secret.md" in fs.reads


def test_import_errors_carry_the_imported_path(assert_mdq_error):
    with assert_mdq_error(MISSING, path="d.md", line=1):
        mdq.compile("d.md", {"d.md": "![[nope.md]]\n"})
    with assert_mdq_error(CALC, path="p.md", line=3):
        mdq.compile("d.md", {"d.md": "![[p.md]]\n", "p.md": "x\n" + calc("a = 1 / 0")})
    with assert_mdq_error(UNDEF, path="p.md", line=2):
        mdq.compile("d.md", {"d.md": "![[p.md]]\n", "p.md": "x\n{{nope}}\n"})


def test_block_ids_are_removed():
    files = {"d.md": "# Title ^top\n\nPara one ^p1\n\nline\n^p2\n\n![[o.md#^q]]\n", "o.md": "Quoted ^q\n"}
    assert md("d.md", files) == "# Title\n\nPara one\n\nline\n\n\nQuoted\n"


# ---------------------------------------------------------------- export
def test_rich_fallbacks_and_profiles():
    doc = "A ==mark== and $x$.\n\n> [!NOTE]\n> Hi\n"
    cm = md("d.md", {"d.md": doc})
    assert "==" not in cm and "`x`" in cm and "[!NOTE]" not in cm
    gfm = md("d.md", {"d.md": doc}, profile="gfm")
    assert "$x$" in gfm
    with pytest.raises(ValueError):
        mdq.compile("d.md", {"d.md": doc}, profile="html")


def test_export_errors_are_located(assert_mdq_error):
    with assert_mdq_error(UNSUP, path="d.md", line=4):
        mdq.compile("d.md", {"d.md": "---\nk: 1\n---\n:::mystery\nx\n:::\n"})


# ---------------------------------------------------------------- entry and file systems
def test_entry_forms(assert_mdq_error):
    files = {"a/b.md": "x\n"}
    assert md(PurePosixPath("a/b.md"), files) == "x\n" and md("./a/b.md", files) == "x\n"
    with assert_mdq_error(MISSING, path="a/c.md"):
        mdq.compile("a/c.md", files)
    with assert_mdq_error(DENIED):
        mdq.compile("../b.md", files)
    with assert_mdq_error(SYNTAX, path="bad.md", line=1):
        mdq.compile("bad.md", {"bad.md": b"\xff"})
    with pytest.raises(TypeError):
        mdq.compile(b"a/b.md", files)


def test_file_system_kinds():
    files = {"a.md": "x\n"}

    class Custom:
        def read(self, path):
            return files.get(path)

    for fs in (files, mdq.MemoryFS(files), Custom()):
        assert md("a.md", fs) == "x\n"
    for bad in (None, 5, "a.md", object()):
        with pytest.raises(TypeError):
            mdq.compile("a.md", bad)
    with pytest.raises(TypeError):
        mdq.MemoryFS({"a.md": 5})


def test_read_callback_errors_propagate():
    class Boom:
        def read(self, path):
            raise RuntimeError("disk on fire")

    with pytest.raises(RuntimeError, match="disk on fire"):
        mdq.compile("a.md", Boom())

    class WrongType:
        def read(self, path):
            return 5

    with pytest.raises(TypeError):
        mdq.compile("a.md", WrongType())


def test_directory_fs(tmp_path, assert_mdq_error):
    (tmp_path / "docs").mkdir()
    (tmp_path / ".mdq.yaml").write_text("k: root\n")
    (tmp_path / "docs" / "d.md").write_text("{{document.k}}\n\n![[part.md]]\n")
    (tmp_path / "docs" / "part.md").write_text("part\n")
    assert mdq.compile("docs/d.md", mdq.DirectoryFS(tmp_path)).markdown == "root\n\npart\n"
    outside = tmp_path.parent / f"outside-{os.getpid()}.md"
    outside.write_text("secret\n")
    try:
        (tmp_path / "docs" / "link.md").symlink_to(outside)
        (tmp_path / "docs" / "e.md").write_text("![[link.md]]\n")
        with assert_mdq_error(DENIED):
            mdq.compile("docs/e.md", mdq.DirectoryFS(tmp_path))
    finally:
        outside.unlink()
    with pytest.raises(ValueError):
        mdq.DirectoryFS(tmp_path / "missing")


# ---------------------------------------------------------------- limits and arguments
def test_limits(assert_mdq_error):
    doc = "---\nvariables:\n  items: [1, 2, 3]\n---\n:::for {each=\"items\" as=\"i\"}\n{{i}}\n:::\n"
    assert md("d.md", {"d.md": doc}, limits={"max_iterations": 3}) == "1\n2\n3\n"
    with assert_mdq_error(LIMIT):
        mdq.compile("d.md", {"d.md": doc}, limits={"max_iterations": 2})
    chain = {"d.md": "![[p.md]]\n", "p.md": "![[q.md]]\n", "q.md": "q\n"}
    assert md("d.md", chain) == "q\n"
    with assert_mdq_error(LIMIT):
        mdq.compile("d.md", chain, limits={"max_depth": 2})
    for bad, exc in (({"max_loops": 1}, ValueError), ({"max_depth": 0}, ValueError), ({"max_depth": True}, TypeError),
                     ({"max_depth": 1.0}, TypeError), ([1], TypeError)):
        with pytest.raises(exc):
            mdq.compile("d.md", {"d.md": "x\n"}, limits=bad)


def test_inputs_types():
    with pytest.raises(TypeError):
        mdq.compile("d.md", {"d.md": "x\n"}, inputs=[("a", 1)])
    with pytest.raises(TypeError):
        mdq.compile("d.md", {"d.md": "x\n"}, inputs={1: 1})
    assert md("d.md", {"d.md": "{{a}} {{b}} {{c}}\n"}, inputs={"a": True, "b": None, "c": "x"}) == "true  x\n"


@pytest.mark.xfail(strict=True, reason="G50 open: container fences are block boundaries (G31), but after :::if/:::for "
                                       "evaluation the neighbouring paragraphs merge")
def test_container_boundaries_survive_evaluation():
    from markdown_it import MarkdownIt
    out = md("d.md", {"d.md": ":::if {var=\"a\" equals=\"1\"}\nA\n:::\nB\n"}, inputs={"a": 1})
    assert MarkdownIt("commonmark").render(out) == "<p>A</p>\n<p>B</p>\n"
