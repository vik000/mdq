"""Epic 7 / E7.2 (F20): package manifest and package opening (package.md). G48.

`manifest_parse(text)` checks the package keys of `manifest.yaml` and returns
`{format, version, entrypoint, features, requests, metadata}`; `requests` has the E6.4 document-layer shape
(requests only, never grants) and `metadata` holds every other key untouched (shared metadata schema).
`package_open(data, limits=None)` = `zip_read` (E7.1) + `manifest_parse` + entrypoint checks, and returns the
manifest fields plus `entrypoint_member`, `files` and `directories`. There is no transport parameter: opening a
package never fetches.
"""
import io
import zipfile
from decimal import Decimal

import pytest

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

META, UNSUP, DENIED, SYNTAX, MISSING = ("MDQ_METADATA_ERROR", "MDQ_UNSUPPORTED_FEATURE", "MDQ_SECURITY_DENIED",
                                        "MDQ_INVALID_SYNTAX", "MDQ_MISSING_IMPORT")
SRC_INVALID, LIMIT = "MDQ_SOURCE_INVALID", "MDQ_RESOURCE_LIMIT"
ALLOW = {"network": "allow"}

SPEC_EXAMPLE = """format: mdq
version: '0.1'
entrypoint: document.md
features: [core, rich]
security:
  remote_imports: false
  network: deny
requirements:
  network: optional
"""
BASE = "format: mdq\nversion: '0.1'\nentrypoint: document.md\nfeatures: [core, package]\n"
DEFAULT_REQUESTS = {"requirements": {"network": "optional"}, "security": {"network": "request"}}


def man(extra="", base=BASE):
    return mi.manifest_parse(base + extra)


def pkg(files, method=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for name, data in files:
            z.writestr(name, data)
    return buf.getvalue()


def good_pkg(manifest=BASE, extra_files=()):
    return pkg([("manifest.yaml", manifest), ("document.md", "# Title\n\nHello.\n"), *extra_files])


# ---------------------------------------------------------------- manifest: valid
def test_minimal_manifest():
    assert man() == {"format": "mdq", "version": "0.1", "entrypoint": "document.md", "features": ["core", "package"],
                     "requests": DEFAULT_REQUESTS, "metadata": {}}


def test_spec_example_with_the_package_profile():
    m = mi.manifest_parse(SPEC_EXAMPLE.replace("[core, rich]", "[core, rich, package]"))
    assert m["features"] == ["core", "rich", "package"]
    assert m["requests"] == {"requirements": {"network": "optional"}, "security": {"network": "none"}}
    assert m["metadata"] == {}


@pytest.mark.xfail(strict=True, reason="G48: the package.md example manifest lists features [core, rich] without 'package'")
def test_spec_example_verbatim():
    mi.manifest_parse(SPEC_EXAMPLE)


def test_shared_metadata_passes_through_untouched_and_in_order():
    m = man("title: Report\nvariables:\n  price: 10.50\n  name: x\nsources:\n  api:\n    kind: http\nrules:\n"
            "  undefined_variables: error\nmdq: '0.1'\nencrypted: true\n")
    assert list(m["metadata"]) == ["title", "variables", "sources", "rules", "mdq", "encrypted"]
    assert m["metadata"]["variables"] == {"price": Decimal("10.5"), "name": "x"}
    assert m["metadata"]["sources"] == {"api": {"kind": "http"}}
    # a manifest saying "encrypted" claims nothing: it is just a metadata value
    assert m["metadata"]["encrypted"] is True and set(m) == {"format", "version", "entrypoint", "features", "requests", "metadata"}


def test_package_keys_may_come_in_any_order():
    m = mi.manifest_parse("features: [package]\nnote: x\nentrypoint: a.md\nversion: '0.1'\nformat: mdq\n")
    assert (m["entrypoint"], m["features"], m["metadata"]) == ("a.md", ["package"], {"note": "x"})


@pytest.mark.parametrize("ep,want", [("./document.md", "document.md"), ("a/../document.md", "document.md"),
                                     ("sections/intro.md", "sections/intro.md"), ("café.md", "café.md")])
def test_entrypoint_is_normalised(ep, want):
    assert man(base=BASE.replace("document.md", ep))["entrypoint"] == want


# ---------------------------------------------------------------- manifest: required keys
@pytest.mark.parametrize("key", ["format", "version", "entrypoint", "features"])
def test_required_keys(assert_mdq_error, key):
    text = "".join(line + "\n" for line in BASE.splitlines() if not line.startswith(key + ":"))
    with assert_mdq_error(META) as info:
        mi.manifest_parse(text)
    assert key in str(info.value)


@pytest.mark.parametrize("line,code", [
    ("format: mdzip", UNSUP), ("format: MDQ", UNSUP), ("format: ''", UNSUP), ("format: 1", META), ("format: [mdq]", META),
    ("format: null", META),
])
def test_format(assert_mdq_error, line, code):
    with assert_mdq_error(code):
        mi.manifest_parse(BASE.replace("format: mdq", line))


@pytest.mark.parametrize("line,code", [
    ("version: '0.2'", UNSUP), ("version: '1.0'", UNSUP), ("version: '0.1.0'", UNSUP), ("version: ''", UNSUP),
    ("version: 0.1", META), ("version: 1", META), ("version: true", META), ("version: ['0.1']", META),
])
def test_version(assert_mdq_error, line, code):
    with assert_mdq_error(code) as info:
        mi.manifest_parse(BASE.replace("version: '0.1'", line))
    if line == "version: 0.1":
        assert "quoted" in str(info.value)


@pytest.mark.parametrize("ep,code", [
    ("../x.md", DENIED), ("a/../../x.md", DENIED), ("/abs.md", DENIED), ("C:doc.md", DENIED), ("https://x/doc.md", DENIED),
    ("a\\\\b.md", SYNTAX), ("''", SYNTAX), ("a/", SYNTAX), (".", SYNTAX),
    ("doc.txt", META), ("doc.MD", META), ("doc.md.age", META), ("manifest.yaml", META),
    ("1", META), ("[doc.md]", META), ("null", META),
])
def test_bad_entrypoint(assert_mdq_error, ep, code):
    with assert_mdq_error(code):
        mi.manifest_parse(BASE.replace("entrypoint: document.md", f"entrypoint: {ep}"))


@pytest.mark.parametrize("line,code", [
    ("features: []", META), ("features: [core]", META), ("features: [core, rich, calc]", META),
    ("features: package", META), ("features: {package: true}", META), ("features: [package, 1]", META),
    ("features: [package, package]", META), ("features: [package, Core]", UNSUP), ("features: [package, mdzip]", UNSUP),
    ("features: null", META),
])
def test_features(assert_mdq_error, line, code):
    with assert_mdq_error(code):
        mi.manifest_parse(BASE.replace("features: [core, package]", line))


def test_unknown_profile_is_named():
    with pytest.raises(mdq.MdqError) as info:
        mi.manifest_parse(BASE.replace("[core, package]", "[package, bogus]"))
    assert info.value.code == UNSUP and "bogus" in str(info.value)


def test_all_known_profiles_accepted():
    assert man(base=BASE.replace("[core, package]", "[core, rich, calc, package]"))["features"] == ["core", "rich", "calc", "package"]


def test_package_profile_is_supported():
    assert "package" in mi.supported_profiles()
    assert mi.check_required(["package"], "0.1") is None


# ---------------------------------------------------------------- manifest: YAML
@pytest.mark.parametrize("text,line", [
    (BASE + "title: a\ntitle: b\n", 6),          # duplicate key
    (BASE + "x: [unclosed\n", 5),
    (BASE + "  bad indent: 1\n", None),
    (BASE + "x: 'unterminated\n", 5),
])
def test_invalid_yaml(assert_mdq_error, text, line):
    with assert_mdq_error(META, line=line):
        mi.manifest_parse(text)


@pytest.mark.parametrize("text", ["", "- a\n- b\n", "just text\n", "null\n"])
def test_manifest_must_be_a_mapping(assert_mdq_error, text):
    with assert_mdq_error(META):
        mi.manifest_parse(text)


def test_duplicate_package_key(assert_mdq_error):
    with assert_mdq_error(META):
        mi.manifest_parse(BASE + "format: mdq\n")


# ---------------------------------------------------------------- security and requirements: requests only
@pytest.mark.parametrize("sec,net", [
    ("security:\n  network: request\n", "request"), ("security:\n  network: none\n", "none"),
    ("security:\n  network: deny\n", "none"), ("security:\n  remote_imports: false\n", "request"),
    ("security:\n  remote_imports: false\n  network: deny\n", "none"), ("security: null\n", "request"),
    ("security: {}\n", "request"),
])
def test_security_spellings(sec, net):
    assert man(sec)["requests"]["security"] == {"network": net}


@pytest.mark.parametrize("sec,code", [
    ("security:\n  remote_imports: true\n", UNSUP),
    ("security:\n  remote_imports: 'false'\n", META), ("security:\n  remote_imports: null\n", META),
    ("security:\n  network: allow\n", META), ("security:\n  network: grant\n", META), ("security:\n  network: true\n", META),
    ("security:\n  private_endpoints: [db.internal]\n", META), ("security:\n  max_bytes: 99999999\n", META),
    ("security: deny\n", META), ("security: [network]\n", META),
])
def test_security_cannot_grant_or_carry_other_keys(assert_mdq_error, sec, code):
    with assert_mdq_error(code):
        man(sec)


@pytest.mark.parametrize("req,want", [
    ("requirements:\n  network: required\n", {"network": "required"}),
    ("requirements:\n  network: none\n", {"network": "none"}),
    ("requirements:\n  hosts: [api.example.org, '*.example.com']\n", {"network": "optional", "hosts": ["api.example.org", "*.example.com"]}),
    ("requirements:\n  hosts: []\n", {"network": "optional", "hosts": []}),
    ("requirements: null\n", {"network": "optional"}),
])
def test_requirements(req, want):
    assert man(req)["requests"]["requirements"] == want


@pytest.mark.parametrize("req,code", [
    ("requirements:\n  network: allow\n", SRC_INVALID), ("requirements:\n  network: 1\n", SRC_INVALID),
    ("requirements:\n  hosts: api.example.org\n", SRC_INVALID), ("requirements:\n  hosts: ['*.org']\n", SRC_INVALID),
    ("requirements:\n  hosts: [1]\n", SRC_INVALID), ("requirements:\n  private_endpoints: [x.example]\n", SRC_INVALID),
    ("requirements:\n  timeout: 60\n", SRC_INVALID),
    ("requirements:\n  hosts: [" + ", ".join(f"h{i}.example.org" for i in range(257)) + "]\n", LIMIT),
    ("requirements: required\n", META), ("requirements: [network]\n", META),
])
def test_bad_requirements(assert_mdq_error, req, code):
    with assert_mdq_error(code):
        man(req)


def test_requests_feed_the_policy_engine_and_never_grant():
    m = man("requirements:\n  network: required\n  hosts: [api.example.org]\nsecurity:\n  network: request\n")
    doc = m["requests"]
    assert mi.policy_intersect(None, None, doc)["network"] == "deny"   # no trusted layer: still denied
    assert mi.policy_intersect(ALLOW, None, doc)["network"] == "deny"
    p = mi.policy_intersect(ALLOW, ALLOW, doc)
    assert p["network"] == "allow" and p["network_required"] is True and p["hosts"] == ["api.example.org"]
    # the manifest can only narrow: hosts intersect with the trusted allowlist
    narrow = mi.policy_intersect({**ALLOW, "hosts": ["other.example.org"]}, ALLOW, doc)
    assert narrow["hosts"] == []
    # security: deny / none switches the network off even when both trusted layers allow it
    off = man("security:\n  network: deny\nrequirements:\n  network: required\n")["requests"]
    p = mi.policy_intersect(ALLOW, ALLOW, off)
    assert p["network"] == "deny" and p["network_required"] is True


# ---------------------------------------------------------------- package_open
def test_open_valid_package():
    z = good_pkg(BASE + "title: Report\n", [("sections/", ""), ("sections/a.md", "A"), ("assets/logo.png", b"\x89PNG")])
    p = mi.package_open(z)
    assert {k: p[k] for k in ("format", "version", "entrypoint", "features")} == \
        {"format": "mdq", "version": "0.1", "entrypoint": "document.md", "features": ["core", "package"]}
    assert p["requests"] == DEFAULT_REQUESTS and p["metadata"] == {"title": "Report"}
    assert p["entrypoint_member"] == "document.md"
    assert list(p["files"]) == ["manifest.yaml", "document.md", "sections/a.md", "assets/logo.png"]
    assert p["files"]["assets/logo.png"] == b"\x89PNG" and p["files"]["document.md"] == b"# Title\n\nHello.\n"
    assert p["directories"] == ["sections/"]


def test_open_stored_package_and_nested_entrypoint():
    z = pkg([("manifest.yaml", BASE.replace("document.md", "./sections/main.md")), ("sections/main.md", "x")], zipfile.ZIP_STORED)
    p = mi.package_open(z)
    assert p["entrypoint"] == "sections/main.md" and p["entrypoint_member"] == "sections/main.md"


def test_entrypoint_matched_in_nfc():
    decomposed = "café.md"
    z = pkg([("manifest.yaml", BASE.replace("document.md", "café.md")), (decomposed, "x")])
    p = mi.package_open(z)
    assert p["entrypoint"] == "café.md" and p["entrypoint_member"] == decomposed


@pytest.mark.parametrize("files", [
    [("document.md", "x")],                                   # no manifest at all
    [("sub/manifest.yaml", BASE), ("document.md", "x")],      # not at the root
    [("Manifest.yaml", BASE), ("document.md", "x")],          # names are case-sensitive
    [("manifest.yml", BASE), ("document.md", "x")],
    [("manifest.yaml/", ""), ("document.md", "x")],           # a directory, not a file
])
def test_missing_root_manifest(assert_mdq_error, files):
    with assert_mdq_error(META):
        mi.package_open(pkg(files))


def test_manifest_errors_carry_the_path_and_line(assert_mdq_error):
    with assert_mdq_error(META, path="manifest.yaml", line=6):
        mi.package_open(good_pkg(BASE + "title: a\ntitle: b\n"))
    with assert_mdq_error(UNSUP, path="manifest.yaml"):
        mi.package_open(good_pkg(BASE.replace("'0.1'", "'9.9'")))
    with assert_mdq_error(DENIED, path="manifest.yaml"):
        mi.package_open(good_pkg(BASE.replace("document.md", "../document.md")))


def test_manifest_must_be_utf8(assert_mdq_error):
    with assert_mdq_error(META, path="manifest.yaml"):
        mi.package_open(pkg([("manifest.yaml", BASE.encode() + b"title: caf\xe9\n"), ("document.md", "x")]))
    # one leading BOM is dropped (G28)
    p = mi.package_open(pkg([("manifest.yaml", b"\xef\xbb\xbf" + BASE.encode()), ("document.md", "x")]))
    assert p["format"] == "mdq"


@pytest.mark.parametrize("files", [
    [("manifest.yaml", BASE)],                                               # entrypoint absent
    [("manifest.yaml", BASE), ("Document.md", "x")],                         # wrong case
    [("manifest.yaml", BASE), ("sections/document.md", "x")],                # wrong directory
    [("manifest.yaml", BASE.replace("document.md", "sections.md")), ("sections.md/", "")],
])
def test_entrypoint_must_be_a_member(assert_mdq_error, files):
    with assert_mdq_error(MISSING, path="manifest.yaml"):
        mi.package_open(pkg(files))


def test_entrypoint_must_be_utf8(assert_mdq_error):
    with assert_mdq_error(SYNTAX, path="document.md", line=2):
        mi.package_open(pkg([("manifest.yaml", BASE), ("document.md", b"# ok\nbad \xff byte\n")]))


def test_zip_errors_and_limits_pass_through(assert_mdq_error):
    with assert_mdq_error(DENIED):
        mi.package_open(pkg([("manifest.yaml", BASE), ("document.md", "x"), ("../evil", "x")]))
    with assert_mdq_error(SYNTAX):
        mi.package_open(good_pkg()[:-1])
    z = good_pkg()
    with assert_mdq_error(LIMIT):
        mi.package_open(z, {"max_entries": 1})
    assert mi.package_open(z, {"max_entries": 2})["entrypoint"] == "document.md"
    with pytest.raises(ValueError):
        mi.package_open(z, {"max_files": 1})


def test_opening_never_fetches():
    # sources in the manifest are only metadata at open time; package_open has no transport parameter at all
    sources = ("sources:\n  rates:\n    kind: http\n    url: https://api.example.org/rates\n    format: json\n"
               "variables:\n  eur:\n    type: number\n    source: rates\n    select: /eur\n")
    p = mi.package_open(good_pkg(BASE + "requirements:\n  network: required\n" + sources))
    assert p["metadata"]["sources"]["rates"]["url"] == "https://api.example.org/rates"
    sig = mi.package_open.__text_signature__ or ""
    assert "transport" not in sig and "data" in sig


@pytest.mark.parametrize("data", ["PK", bytearray(b"PK"), None])
def test_data_must_be_bytes(data):
    with pytest.raises(TypeError):
        mi.package_open(data)


def test_manifest_parse_needs_str():
    with pytest.raises(TypeError):
        mi.manifest_parse(BASE.encode())
