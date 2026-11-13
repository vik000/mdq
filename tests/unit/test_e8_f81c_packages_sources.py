"""Epic 8 / E8.1c (F16-F21, F25): packages in `compile` and the public source API. G52.

* `mdq.PackageFS(data, identities=None, passphrase=None, limits=None)` opens a `.mdq` (or decrypts a `.mdq.age`)
  with every E7 check; `mdq.compile(pkg)` compiles its entrypoint. The manifest's shared metadata is the LOWEST
  layer; its `security`/`requirements` are requests only. Compiling an encrypted package needs
  `allow_plaintext=True`; `evaluate` does not.
* `mdq.validate_sources(entry, fs)`: the document's declarations, offline.
* `mdq.test_source(entry, fs, name=..., transport=..., retrieved_at=..., host_policy=..., library_policy=...)`:
  one retrieval; network only if BOTH trusted layers allow it; every document request (manifest, metadata files)
  can only narrow it, most-restrictively. Returns a snapshot entry for `compile(snapshot=[...])`.
"""
import io
import unicodedata
import zipfile
from decimal import Decimal

import pytest
import pyrage
from pyrage import x25519

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

META, UNSUP, DENIED, SYNTAX, MISSING = ("MDQ_METADATA_ERROR", "MDQ_UNSUPPORTED_FEATURE", "MDQ_SECURITY_DENIED",
                                        "MDQ_INVALID_SYNTAX", "MDQ_MISSING_IMPORT")
UNRES, BAD, LIMIT = "MDQ_SOURCE_UNRESOLVED", "MDQ_SOURCE_INVALID", "MDQ_RESOURCE_LIMIT"
TS = "2026-11-13T12:00:00Z"
URL = "https://example.invalid/stocks/AAPL"
ALLOW = {"network": "allow"}
MANIFEST = "format: mdq\nversion: '0.1'\nentrypoint: document.md\nfeatures: [core, package]\n"
IDENT = x25519.Identity.generate()


def zpkg(manifest=MANIFEST, files=(("document.md", "# Hi\n"),)):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.yaml", manifest)
        for name, data in files:
            z.writestr(name, data)
    return buf.getvalue()


class Recording:
    def __init__(self, files):
        self.files, self.reads = dict(files), []

    def read(self, path):
        self.reads.append(path)
        return self.files.get(path)


# ---------------------------------------------------------------- PackageFS
def test_package_fs_attributes():
    pkg = mdq.PackageFS(zpkg(MANIFEST + "title: T\nrequirements:\n  network: optional\n",
                             (("document.md", "x\n"), ("assets/", ""), ("assets/a.png", b"\x89PNG"))))
    assert pkg.entrypoint == "document.md" and pkg.encrypted is False
    assert pkg.manifest == {"format": "mdq", "version": "0.1", "entrypoint": "document.md", "features": ["core", "package"]}
    assert pkg.metadata == {"title": "T"}
    assert pkg.requests == {"requirements": {"network": "optional"}, "security": {"network": "request"}}
    assert pkg.files == ("manifest.yaml", "document.md", "assets/a.png") and pkg.directories == ("assets/",)
    assert pkg.read("assets/a.png") == b"\x89PNG" and pkg.read("assets/") is None and pkg.read("nope") is None


def test_package_fs_rejects_bad_packages(assert_mdq_error):
    with assert_mdq_error(DENIED):
        mdq.PackageFS(zpkg(files=(("document.md", "x"), ("../evil", "x"))))
    with assert_mdq_error(META):
        mdq.PackageFS(zpkg(MANIFEST.replace("features: [core, package]", "features: [core]")))
    with assert_mdq_error(MISSING):
        mdq.PackageFS(zpkg(files=()))
    with assert_mdq_error(LIMIT):
        mdq.PackageFS(zpkg(), limits={"max_entries": 1})
    with pytest.raises(TypeError):
        mdq.PackageFS(bytearray(zpkg()))


# ---------------------------------------------------------------- compile a package
def test_compile_package_entrypoint_and_layers():
    files = (("document.md", "---\nc: frontmatter\n---\n{{document.a}} {{document.b}} {{document.c}}\n"),
             (".mdq.yaml", "b: project\nc: project\n"))
    pkg = mdq.PackageFS(zpkg(MANIFEST + "a: manifest\nb: manifest\nc: manifest\n", files))
    r = mdq.compile(pkg)
    assert r.markdown == "manifest project frontmatter\n"
    assert dict(r.provenance) == {"/a": "manifest.yaml", "/b": ".mdq.yaml", "/c": "document.md"}
    assert mdq.compile(None, pkg) == r


def test_package_keys_and_requests_are_not_metadata():
    pkg = mdq.PackageFS(zpkg(MANIFEST + "security:\n  network: deny\nrequirements:\n  network: optional\n"))
    meta = mdq.evaluate(pkg).metadata
    for key in ("format", "version", "entrypoint", "features", "security", "requirements"):
        assert key not in meta


def test_other_entry_inside_a_package_and_imports_and_data():
    files = (("document.md", "main\n"),
             ("sections/other.md", "---\nvariables:\n  p: ./p.yaml\nbibliography: ../refs.bib\n---\n"
                                   "{{p.name}} [@knuth84]\n\n![[part.md]]\n"),
             ("sections/p.yaml", "name: Apollo\n"), ("sections/part.md", "part\n"),
             ("refs.bib", "@article{knuth84, author={Knuth, Donald}, title={LP}, year={1984}}\n"))
    pkg = mdq.PackageFS(zpkg(files=files))
    out = mdq.compile("sections/other.md", pkg).markdown
    assert out.startswith("Apollo (Knuth 1984)\n\npart\n") and "## References" in out


def test_package_paths_are_matched_in_nfc():
    decomposed = unicodedata.normalize("NFD", "café.md")
    pkg = mdq.PackageFS(zpkg(MANIFEST.replace("document.md", "café.md"), ((decomposed, "x\n"),)))
    assert mdq.compile(pkg).markdown == "x\n"


def test_package_calc_and_inputs():
    files = (("document.md", "```calc\nt = price * 2\n```\nt: {{calc.t}}\n"),)
    pkg = mdq.PackageFS(zpkg(MANIFEST + "variables:\n  price: 3\n", files))
    assert mdq.compile(pkg).markdown == "t: 6\n"
    assert mdq.compile(pkg, inputs={"price": 5}).markdown == "t: 10\n"


# ---------------------------------------------------------------- encrypted packages
def sealed(data=None):
    return pyrage.encrypt(zpkg() if data is None else data, [IDENT.to_public()])


def test_encrypted_package_needs_plaintext_permission(assert_mdq_error):
    pkg = mdq.PackageFS(sealed(), identities=[str(IDENT)])
    assert pkg.encrypted is True
    with assert_mdq_error(DENIED):
        mdq.compile(pkg)
    with assert_mdq_error(DENIED):
        mdq.compile(pkg, allow_plaintext=False)
    assert mdq.compile(pkg, allow_plaintext=True).markdown == "# Hi\n"
    assert mdq.evaluate(pkg).metadata == {}
    with pytest.raises(TypeError):
        mdq.compile(pkg, allow_plaintext="yes")


def test_plain_package_needs_no_permission():
    assert mdq.compile(mdq.PackageFS(zpkg())).markdown == "# Hi\n"


def test_encrypted_package_keys(assert_mdq_error):
    with assert_mdq_error(DENIED):
        mdq.PackageFS(sealed())  # no credentials: nothing is decrypted
    with assert_mdq_error(DENIED):
        mdq.PackageFS(sealed(), identities=[str(x25519.Identity.generate())])
    ct = mi.age_encrypt(zpkg(), bytes(48), passphrase="pw", work_factor=4)
    assert mdq.compile(mdq.PackageFS(ct, passphrase="pw"), allow_plaintext=True).markdown == "# Hi\n"
    with assert_mdq_error(LIMIT):
        mdq.PackageFS(ct, passphrase="pw", max_work_factor=3)


# ---------------------------------------------------------------- validate_sources
NAMED = ("sources:\n  quotes:\n    kind: http\n    url: 'https://example.invalid/stocks/{sym}'\n    format: json\n"
         "    select: /price\n    arguments:\n      sym: AAPL\n")
VAR = "variables:\n  price:\n    type: number\n    unit: USD\n    source: quotes\n"
HOSTVAR = "variables:\n  qty:\n    type: number\n"


def test_validate_sources_across_layers():
    # the named source lives in .mdq.yaml, the variable in the frontmatter, another in the sidecar
    fs = Recording({".mdq.yaml": NAMED, "d.md": "---\n" + VAR + "---\nx\n", "d.meta.yaml": HOSTVAR})
    decls = mdq.validate_sources("d.md", fs)
    assert [d.name for d in decls] == ["price", "qty"]
    p = decls[0]
    assert isinstance(p, mdq.SourceDeclaration) and (p.type, p.unit, p.url) == ("number", "USD", URL)
    assert p.source["named"] == "quotes" and decls[1].source is None
    assert set(fs.reads) <= {"d.md", ".mdq.yaml", "d.meta.yaml"}


def test_validate_sources_in_a_package_and_errors(assert_mdq_error):
    pkg = mdq.PackageFS(zpkg(MANIFEST + NAMED + VAR))
    assert [d.name for d in mdq.validate_sources(pkg)] == ["price"]
    with assert_mdq_error(BAD, path=".mdq.yaml"):
        mdq.validate_sources("d.md", {"d.md": "x\n", ".mdq.yaml": "variables:\n  p:\n    type: complex\n"})
    with assert_mdq_error(META):
        mdq.validate_sources("d.md", {"d.md": "---\nvariables: [1]\n---\n"})


# ---------------------------------------------------------------- test_source
def files_with(extra_fm="", extra_root=""):
    return {".mdq.yaml": NAMED + extra_root, "d.md": "---\n" + VAR + extra_fm + "---\nPrice {{price}}\n"}


def run(fs, transport, entry="d.md", **kw):
    args = dict(name="price", transport=transport, retrieved_at=TS, host_policy=ALLOW, library_policy=ALLOW)
    args.update(kw)
    return mdq.test_source(entry, fs, **args)


def test_test_source_round_trip(fake_transport):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 187.5}')})
    entry = run(files_with(), t)
    assert entry["value"] == Decimal("187.5") and entry["unit"] == "USD" and entry["retrieved_at"] == TS
    assert entry["source"]["named"] == "quotes" and len(t.gets) == 1
    r = mdq.compile("d.md", files_with(), snapshot=[entry])
    assert r.markdown == "Price 187.5 USD\n" and r.values[0].source["key"] == entry["source"]["key"]


@pytest.mark.parametrize("host,library", [(None, ALLOW), (ALLOW, None), ({"network": "deny"}, ALLOW), (None, None)])
def test_both_trusted_layers_must_allow(fake_transport, assert_mdq_error, host, library):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}')})
    with assert_mdq_error(DENIED):
        run(files_with(), t, host_policy=host, library_policy=library)
    assert t.calls == []


@pytest.mark.parametrize("fm,root", [
    ("security:\n  network: none\n", ""),                              # frontmatter switches the network off
    ("", "security:\n  network: none\n"),                              # ... or the project file does
    ("requirements:\n  hosts: [other.example]\n", ""),                 # host allowlist without this host
    ("requirements:\n  network: none\n", "requirements:\n  network: required\n"),
])
def test_document_requests_only_narrow(fake_transport, assert_mdq_error, fm, root):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}')})
    with assert_mdq_error(DENIED):
        run(files_with(fm, root), t)
    assert t.gets == []


def test_a_request_cannot_widen_another(fake_transport, assert_mdq_error):
    # the sidecar asks for network, the frontmatter said none: the most restrictive wins, whatever the order
    fs = files_with("security:\n  network: none\n")
    fs["d.meta.yaml"] = "security:\n  network: request\nrequirements:\n  network: required\n"
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}')})
    with assert_mdq_error(DENIED) as info:
        run(fs, t)
    assert "requires" in str(info.value) and t.calls == []


def test_manifest_requests_apply_to_packages(fake_transport, assert_mdq_error):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 2}')})
    ok = mdq.PackageFS(zpkg(MANIFEST + NAMED + VAR + "requirements:\n  hosts: [example.invalid]\n"))
    assert run(ok, t, entry=None)["value"] == Decimal("2")
    narrowed = mdq.PackageFS(zpkg(MANIFEST + NAMED + VAR + "requirements:\n  hosts: [elsewhere.example]\n"))
    with assert_mdq_error(DENIED):
        run(narrowed, t, entry=None)
    off = mdq.PackageFS(zpkg(MANIFEST + NAMED + VAR + "security:\n  network: deny\n"))
    with assert_mdq_error(DENIED):
        run(off, t, entry=None)


def test_test_source_argument_and_declaration_errors(fake_transport, assert_mdq_error):
    t = fake_transport({URL: fake_transport.reply(body=b'{"price": 1}')})
    with assert_mdq_error(DENIED):
        run(files_with(), t, allow_network=False)
    with assert_mdq_error(UNRES):
        run(files_with(), t, name="nope")
    fs = files_with()
    fs["d.meta.yaml"] = HOSTVAR
    with assert_mdq_error(UNRES):
        run(fs, t, name="qty")  # host-supplied: nothing to fetch
    with pytest.raises(ValueError):
        run(files_with(), t, retrieved_at="2026-11-13 12:00")
    with pytest.raises(TypeError):
        run(files_with(), "not callable")
    with pytest.raises(TypeError):
        run(files_with(), t, host_policy="allow")
    with assert_mdq_error(BAD, path="d.md"):
        run(files_with("security:\n  network: maybe\n"), t)
    assert t.calls == []


def test_test_source_is_not_collected_by_pytest():
    assert mdq.test_source.__test__ is False


def test_compile_never_takes_a_transport():
    import inspect
    for f in (mdq.compile, mdq.evaluate, mdq.validate_sources, mdq.PackageFS):
        assert "transport" not in inspect.signature(f).parameters
