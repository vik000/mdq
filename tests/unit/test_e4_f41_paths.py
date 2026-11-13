"""Epic 4 / E4.1 (F04): `path_normalise`, `path_identity`, `path_confine`.

`path_normalise(base, rel)` resolves a reference against the importing file's directory
(`base`, root-relative, "" = root) with no file system access: `/` separators only, `.` and `..`
resolved, anything that leaves the root, is absolute, or has a scheme or drive letter is
MDQ_SECURITY_DENIED. `path_identity` is the NFC form used to compare paths. `path_confine`
resolves a root-relative path on disk, follows symlinks, and refuses anything that ends up
outside the root (MDQ_SECURITY_DENIED); a missing file is MDQ_MISSING_IMPORT. G32.
"""
import os
import unicodedata

import pytest
from hypothesis import given, strategies as st

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

norm = mi.path_normalise


@pytest.mark.parametrize("base,rel,expected", [
    ("", "a.md", "a.md"),
    ("", "a/b.md", "a/b.md"),
    ("", "./a.md", "a.md"),
    ("", "a/./b.md", "a/b.md"),
    ("", "a/../b.md", "b.md"),
    ("", "a/b/../../c.md", "c.md"),
    ("chapters", "intro.md", "chapters/intro.md"),
    ("chapters", "../index.md", "index.md"),
    ("chapters", "./images/flow.png", "chapters/images/flow.png"),
    ("a/b/c", "../../x.md", "a/x.md"),
    ("a/b", "../b/../b/y.md", "a/b/y.md"),
    ("./a/../b", "x.md", "b/x.md"),
    ("", "a/.", "a"),
    ("", "...", "..."),
    ("", "..a/a..", "..a/a.."),
    ("", "a.md#frag", "a.md#frag"),
    ("", "a?q=1", "a?q=1"),
    ("", "%2e%2e/x", "%2e%2e/x"),
    ("", "A.md", "A.md"),
    ("", "dir with space/é€😀.md", "dir with space/é€😀.md"),
    ("", "./a:b.md", "a:b.md"),
    ("", "a/b:c", "a/b:c"),
    ("", " lead.md", " lead.md"),
    ("", "trail .md", "trail .md"),
])
def test_valid(base, rel, expected):
    assert norm(base, rel) == expected


@pytest.mark.parametrize("rel", [
    "../x", "..", "a/../../x", "a/b/../../../x", "./../x", "a/..//..",
])
def test_traversal_is_denied(rel, assert_mdq_error):
    code = "MDQ_SECURITY_DENIED" if rel != "a/..//.." else "MDQ_INVALID_SYNTAX"
    with assert_mdq_error(code):
        norm("", rel)


def test_traversal_relative_to_base(assert_mdq_error):
    assert norm("a", "../x") == "x"
    with assert_mdq_error("MDQ_SECURITY_DENIED"):
        norm("a", "../../x")
    with assert_mdq_error("MDQ_SECURITY_DENIED"):
        norm("a/b", "../../../x")


@pytest.mark.parametrize("rel", [
    "/etc/passwd", "/", "//host/share/x", "///x", "C:/x", "c:x", "C:\\x", "https://example.com/a.md",
    "http://a/b", "file:///etc/passwd", "mailto:a@b", "data:text/plain,x", "a+b.c-d:x", "x:y",
])
def test_absolute_and_scheme_are_denied(rel, assert_mdq_error):
    with assert_mdq_error("MDQ_SECURITY_DENIED"):
        norm("", rel)


@pytest.mark.parametrize("rel", [
    "", "a\\b", "\\x", "\\\\server\\share", "a\x00b", "a\nb", "a\tb", "a\x7fb", "a/", "a//b", "a/b/", "./", ".", "a/..", "./.",
])
def test_invalid_syntax(rel, assert_mdq_error):
    with assert_mdq_error("MDQ_INVALID_SYNTAX"):
        norm("", rel)


@pytest.mark.parametrize("base", ["/x", "..", "a/../..", "https://x", "a\\b", "a//b", "a\x00"])
def test_bad_base(base, assert_mdq_error):
    with pytest.raises(mdq.MdqError):
        norm(base, "x.md")


def test_limits(assert_mdq_error):
    assert norm("", "a" * 255) == "a" * 255
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        norm("", "a" * 256)
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        norm("", "é" * 128)  # 256 bytes
    assert norm("", "/".join(["a" * 100] * 40)) == "/".join(["a" * 100] * 40)  # 4039 bytes
    with assert_mdq_error("MDQ_RESOURCE_LIMIT"):
        norm("", "/".join(["a" * 100] * 41))


def test_type_errors():
    for bad in ((None, "a"), ("", None), (b"", "a"), ("", b"a")):
        with pytest.raises(TypeError):
            norm(*bad)


segments = st.sampled_from(["a", "b", "c d", "é", ".", "..", "", "...", "x.md"])


def oracle(base, rel):
    """Independent resolution: returns ('ok', path) or ('deny'|'syntax', None)."""
    def walk(stack, p):
        if p == "":
            return None, "syntax"
        if p.startswith("/"):
            return None, "deny"
        parts = p.split("/")
        out = list(stack)
        for k, g in enumerate(parts):
            if g == "":
                return None, "syntax"
            if g == ".":
                continue
            if g == "..":
                if not out:
                    return None, "deny"
                out.pop()
            else:
                out.append(g)
        return out, None
    stack = []
    if base != "":
        stack, e = walk([], base)
        if e:
            return e, None
    out, e = walk(stack, rel)
    if e:
        return e, None
    return ("ok", "/".join(out)) if out else ("syntax", None)


@given(st.lists(segments, max_size=4).map("/".join), st.lists(segments, min_size=1, max_size=6).map("/".join))
def test_differential_and_idempotence(base, rel):
    kind, expected = oracle(base, rel)
    try:
        got = norm(base, rel)
    except mdq.MdqError as e:
        assert kind in ("deny", "syntax") and e.code == ("MDQ_SECURITY_DENIED" if kind == "deny" else "MDQ_INVALID_SYNTAX"), (base, rel, e)
        return
    assert kind == "ok" and got == expected, (base, rel, got)
    assert all(g not in ("", ".", "..") for g in got.split("/"))
    assert norm("", got) == got


# ---------------------------------------------------------------- identity
def test_identity_is_nfc():
    nfd = unicodedata.normalize("NFD", "é/ñ.md")
    nfc = unicodedata.normalize("NFC", "é/ñ.md")
    assert nfd != nfc
    assert mi.path_identity(nfd) == nfc == mi.path_identity(nfc)
    assert mi.path_identity("a/B.md") == "a/B.md"  # case is kept
    assert mi.path_identity("") == ""
    with pytest.raises(TypeError):
        mi.path_identity(b"x")


@given(st.text(max_size=20))
def test_identity_matches_unicodedata(s):
    assert mi.path_identity(s) == unicodedata.normalize("NFC", s)


# ---------------------------------------------------------------- confine (real file system)
confine = mi.path_confine
needs_symlinks = pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="symlinks")


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("a")
    (root / "sub" / "b.md").write_text("b")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("s")
    return root, outside


def test_confine_finds_files(tree):
    root, _ = tree
    assert os.path.samefile(confine(str(root), "a.md"), root / "a.md")
    assert os.path.samefile(confine(str(root), "sub/b.md"), root / "sub" / "b.md")
    assert os.path.samefile(confine(str(root), "./sub/../a.md"), root / "a.md")
    assert os.path.isabs(confine(str(root), "a.md"))


def test_confine_errors(tree, assert_mdq_error):
    root, outside = tree
    with assert_mdq_error("MDQ_MISSING_IMPORT"):
        confine(str(root), "nope.md")
    with assert_mdq_error("MDQ_MISSING_IMPORT"):
        confine(str(root), "sub")  # a directory is not a file
    with assert_mdq_error("MDQ_MISSING_IMPORT"):
        confine(str(root), "a.md/x")
    with assert_mdq_error("MDQ_SECURITY_DENIED"):
        confine(str(root), "../outside/secret.md")
    with assert_mdq_error("MDQ_SECURITY_DENIED"):
        confine(str(root), str(outside / "secret.md"))
    with assert_mdq_error("MDQ_SECURITY_DENIED"):
        confine(str(root / "missing"), "a.md")
    with assert_mdq_error("MDQ_SECURITY_DENIED"):
        confine(str(root / "a.md"), "x")
    with assert_mdq_error("MDQ_INVALID_SYNTAX"):
        confine(str(root), "a//b")


@needs_symlinks
def test_symlink_escape_is_denied(tree, assert_mdq_error):
    root, outside = tree
    (root / "link.md").symlink_to(outside / "secret.md")
    (root / "dirlink").symlink_to(outside, target_is_directory=True)
    (root / "sub" / "up").symlink_to(root / ".." / "outside", target_is_directory=True)
    for rel in ("link.md", "dirlink/secret.md", "sub/up/secret.md"):
        with assert_mdq_error("MDQ_SECURITY_DENIED"):
            confine(str(root), rel)


@needs_symlinks
def test_symlinks_inside_the_root_are_fine(tree):
    root, _ = tree
    (root / "alias.md").symlink_to(root / "a.md")
    (root / "subalias").symlink_to(root / "sub", target_is_directory=True)
    assert os.path.samefile(confine(str(root), "alias.md"), root / "a.md")
    assert os.path.samefile(confine(str(root), "subalias/b.md"), root / "sub" / "b.md")
    assert confine(str(root), "alias.md") == os.path.realpath(root / "a.md")


@needs_symlinks
def test_root_itself_may_be_a_symlink(tree, tmp_path):
    root, _ = tree
    (tmp_path / "rootlink").symlink_to(root, target_is_directory=True)
    assert os.path.samefile(confine(str(tmp_path / "rootlink"), "a.md"), root / "a.md")


@needs_symlinks
def test_broken_and_looping_symlinks(tree, assert_mdq_error):
    root, _ = tree
    (root / "broken.md").symlink_to(root / "gone.md")
    (root / "loop1").symlink_to(root / "loop2")
    (root / "loop2").symlink_to(root / "loop1")
    with assert_mdq_error("MDQ_MISSING_IMPORT"):
        confine(str(root), "broken.md")
    with pytest.raises(mdq.MdqError):
        confine(str(root), "loop1")


@needs_symlinks
def test_prefix_sibling_is_not_inside(tmp_path, assert_mdq_error):
    root = tmp_path / "root"
    sibling = tmp_path / "root-evil"
    root.mkdir()
    sibling.mkdir()
    (sibling / "x.md").write_text("x")
    (root / "l.md").symlink_to(sibling / "x.md")
    with assert_mdq_error("MDQ_SECURITY_DENIED"):
        confine(str(root), "l.md")


def test_unicode_names_nfc_and_nfd(tmp_path):
    root = tmp_path
    nfd = unicodedata.normalize("NFD", "é.md")
    nfc = unicodedata.normalize("NFC", "é.md")
    (root / nfd).write_text("x")
    assert os.path.samefile(confine(str(root), nfc), root / nfd)
    assert os.path.samefile(confine(str(root), nfd), root / nfd)
    (root / "ñ.md").write_text("y")
    assert os.path.samefile(confine(str(root), unicodedata.normalize("NFD", "ñ.md")), root / "ñ.md")
