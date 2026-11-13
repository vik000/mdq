"""Epic 8 / E8.3 (F26): the `mdq` command line. G54.

`mdq build|check|sources ENTRY [--root DIR | --package FILE] ...` and `mdq --version`. Offline (no command fetches);
`-o` never overwrites a file the command read. Exit codes: 0 ok, 1 invalid input, 2 usage, 3 unsupported feature,
4 denied, 5 resource limit. `--format json` prints exactly one object, validated against `mdq/cli.schema.json`.
"""
import importlib.resources
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import zipfile
from decimal import Decimal

import pytest
import pyrage
from pyrage import x25519

import mdq
from mdq import _internal as mi
from mdq.cli import main

pytestmark = pytest.mark.unit
jsonschema = pytest.importorskip("jsonschema")
SCHEMA = json.loads(importlib.resources.files("mdq").joinpath("cli.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
FENCE = "```"
DOC = "---\nvariables:\n  q: 4\n---\n# Hi\n\n" + FENCE + "calc\nx = q * 2 EUR\n" + FENCE + "\nTotal {{calc.x}}\n"
MANIFEST = "format: mdq\nversion: '0.1'\nentrypoint: document.md\nfeatures: [core, package]\n"


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "r.md").write_text(DOC)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(argv, capsys):
    code = main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def run_json(argv, capsys):
    code, out, err = run(argv + ["--format", "json"], capsys)
    obj = json.loads(out)  # exactly one JSON document on stdout
    VALIDATOR.validate(obj)
    return code, obj


def zpkg(files=(("document.md", "# Pkg {{document.t}}\n"),), manifest=MANIFEST + "t: x\n"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", manifest)
        for n, d in files:
            z.writestr(n, d)
    return buf.getvalue()


# ---------------------------------------------------------------- version, help, usage
def test_version(capsys):
    code, out, _ = run(["--version"], capsys)
    v = mdq.version_info()
    assert code == 0 and out.strip() == (f"mdq {v['package']} (syntax {v['syntax']}, manifest {v['manifest']}, "
                                         f"unit catalogue {v['unit_catalogue']}, spec {v['spec']})")


def test_usage_errors(capsys, root):
    assert run([], capsys)[0] == 2
    for argv in (["build", "--bogus"], ["frobnicate"], ["build", "x.md", "--root", ".", "--package", "p.mdq"],
                 ["build", "x.md", "--format", "xml"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2
    capsys.readouterr()
    code, out, err = run(["build"], capsys)
    assert code == 2 and "ENTRY is required" in err
    assert run(["build", "docs/r.md", "--root", "missing-dir"], capsys)[0] == 2
    assert run(["build", "docs/r.md", "--identity", "k.txt"], capsys)[0] == 2


def test_help_lists_exit_codes(capsys):
    with pytest.raises(SystemExit):
        main(["build", "--help"])
    out = capsys.readouterr().out
    assert "exit codes:" in out and "4  denied capability" in out


# ---------------------------------------------------------------- build
def test_build_to_stdout_matches_the_library(capsys, root):
    code, out, err = run(["build", "docs/r.md"], capsys)
    assert code == 0 and err == "" and out == mdq.compile("docs/r.md", mdq.DirectoryFS(".")).markdown
    assert out == "# Hi\n\nTotal 8 EUR\n"


def test_build_options(capsys, root):
    assert run(["build", "docs/r.md", "--calc-blocks", "keep"], capsys)[1].count(FENCE + "calc") == 1
    (root / "m.md").write_text("$x$\n")
    assert run(["build", "m.md", "--profile", "gfm"], capsys)[1] == "$x$\n"
    assert run(["build", "m.md"], capsys)[1] == "`x`\n"
    code, out, _ = run(["build", "docs/r.md", "--input", "q=10"], capsys)
    assert out.endswith("Total 20 EUR\n")


def test_build_to_a_file(capsys, root):
    code, out, err = run(["build", "docs/r.md", "-o", "out.md"], capsys)
    assert code == 0 and out == "" and (root / "out.md").read_text() == "# Hi\n\nTotal 8 EUR\n"
    assert run(["build", "docs/r.md", "-o", "out.md"], capsys)[0] == 0  # previous output may be replaced
    assert run(["build", "docs/r.md", "-o", "nodir/out.md"], capsys)[0] == 2


@pytest.mark.parametrize("target", ["docs/r.md", "docs/../docs/r.md", ".mdq.yaml", "docs/part.md", "docs/data.yaml",
                                    "docs/r.meta.yaml"])
def test_never_overwrites_a_source(capsys, root, target):
    (root / ".mdq.yaml").write_text("k: 1\n")
    (root / "docs" / "r.meta.yaml").write_text("s: 1\n")
    (root / "docs" / "data.yaml").write_text("v: 2\n")
    (root / "docs" / "part.md").write_text("part\n")
    (root / "docs" / "r.md").write_text("---\nvariables:\n  d: ./data.yaml\n---\n{{d.v}}\n\n![[part.md]]\n")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    code, out, err = run(["build", "docs/r.md", "-o", target], capsys)
    assert code == 2 and "source file" in err
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


# ---------------------------------------------------------------- exit codes and diagnostics
@pytest.mark.parametrize("doc,exit_code,code", [
    ("![[nope.md]]\n", 1, "MDQ_MISSING_IMPORT"),
    (FENCE + "calc\nx = 1 / 0\n" + FENCE + "\n", 1, "MDQ_CALC_ERROR"),
    ("---\nvariables:\n  q:\n    type: number\n---\nx\n", 1, "MDQ_SOURCE_UNRESOLVED"),
    ("{{nope}}\n", 1, "MDQ_UNDEFINED_VARIABLE"),
    (":::mystery\nx\n:::\n", 3, "MDQ_UNSUPPORTED_FEATURE"),
    ("![[../../outside.md]]\n", 4, "MDQ_SECURITY_DENIED"),
])
def test_exit_codes(capsys, root, doc, exit_code, code):
    (root / "e.md").write_text(doc)
    rc, out, err = run(["build", "e.md"], capsys)
    assert rc == exit_code and out == "" and f": {code}: " in err and err.startswith("e.md")
    rc, obj = run_json(["build", "e.md"], capsys)
    assert rc == exit_code and obj["ok"] is False and obj["diagnostics"][0]["code"] == code
    assert obj["diagnostics"][0]["path"] == "e.md"


def test_resource_limit_exit_code(capsys, root):
    (root / "items.json").write_text(json.dumps({"xs": list(range(10_001))}))
    (root / "l.md").write_text("---\nvariables:\n  d: ./items.json\n---\n:::for {each=\"d.xs\" as=\"i\"}\n{{i}}\n:::\n")
    assert run(["build", "l.md"], capsys)[0] == 5


# ---------------------------------------------------------------- JSON output
def test_json_build_check_sources(capsys, root):
    rc, obj = run_json(["build", "docs/r.md"], capsys)
    assert rc == 0 and obj["ok"] is True and obj["markdown"] == "# Hi\n\nTotal 8 EUR\n"
    v = obj["calc"][0]["results"][0]
    assert v["value"] == "8" and isinstance(v["value"], str) and v["currency"] == "EUR"
    rc, obj = run_json(["check", "docs/r.md"], capsys)
    assert rc == 0 and "markdown" not in obj and obj["calc"][0]["line"] == 7
    (root / "s.md").write_text("---\nvariables:\n  p:\n    type: number\n    source:\n      kind: http\n"
                               "      url: https://example.invalid/p\n      format: json\n      select: /p\n  h:\n"
                               "    type: string\n---\nx\n")
    rc, obj = run_json(["sources", "s.md"], capsys)
    assert rc == 0 and [d["name"] for d in obj["declarations"]] == ["p", "h"]
    assert obj["declarations"][0]["url"] == "https://example.invalid/p" and obj["declarations"][1]["source"] is None


def test_json_usage_error(capsys, root):
    rc, obj = run_json(["build", "docs/r.md", "--input", "novalue"], capsys)
    assert rc == 2 and obj == {"ok": False, "diagnostics": [], "usage_error": "--input needs NAME=VALUE, got 'novalue'"}


def test_json_decimals_keep_their_digits(capsys, root):
    (root / "d.md").write_text(FENCE + "calc\nx = 1 / 3\n" + FENCE + "\n")
    rc, obj = run_json(["check", "d.md", "--input", "big=1234567890.123456789012345678"], capsys)
    assert obj["calc"][0]["results"][0]["value"] == "0.3333333333333333333333333333"


def test_schema_rejects_wrong_shapes():
    for bad in ({"ok": True}, {"ok": False, "diagnostics": []}, {"ok": True, "diagnostics": [], "extra": 1},
                {"ok": False, "diagnostics": [{"code": "MDQ_X", "message": "m", "path": None, "line": None, "column": None}]},
                {"ok": True, "diagnostics": [], "calc": [{"path": "a", "line": 1, "source": "", "results": [
                    {"name": "x", "line": 1, "column": 1, "type": "decimal", "value": 1.5, "unit": None,
                     "currency": None, "display": "1.5"}]}]}):
        assert not VALIDATOR.is_valid(bad), bad


# ---------------------------------------------------------------- text output of check and sources
def test_check_and_sources_text(capsys, root):
    code, out, _ = run(["check", "docs/r.md"], capsys)
    assert code == 0 and out == "ok: 1 calc blocks, 1 results, 0 declared values\n"
    (root / "s.md").write_text("---\nvariables:\n  h:\n    type: number\n    unit: EUR\n---\nx\n")
    assert run(["sources", "s.md"], capsys)[1] == "h\tnumber\tEUR\thost\t-\n"


# ---------------------------------------------------------------- inputs, snapshots, FX
def test_input_parsing(capsys, root):
    # (text first: a value at the start of a line is escaped by G26, e.g. `-1.5` -> `\\-1.5`)
    (root / "i.md").write_text("v {{a}}|{{b}}|{{c}}|{{d}}\n")
    out = run(["build", "i.md", "--input", "a=-1.50", "--input", "b=true", "--input", "c=12 km", "--input", "d=007"], capsys)[1]
    assert out == "v -1.5|true|12 km|007\n"
    assert run(["build", "i.md", "--input", "a=1", "--input", "a=2"], capsys)[0] == 2
    assert run(["build", "i.md", "--input", "=1"], capsys)[0] == 2


def test_snapshot_file(capsys, root, fake_transport):
    decl = ("variables:\n  p:\n    type: number\n    unit: USD\n    source:\n      kind: http\n"
            "      url: 'https://example.invalid/p'\n      format: json\n      select: /p\n")
    (root / "s.md").write_text("---\n" + decl + "---\nP {{p}}\n")
    t = fake_transport({"https://example.invalid/p": fake_transport.reply(body=b'{"p": 1.25}')})
    entry = mdq.test_source("s.md", mdq.DirectoryFS(root), name="p", transport=t, retrieved_at="2026-11-13T12:00:00Z",
                            host_policy={"network": "allow"}, library_policy={"network": "allow"})
    # snapshot files hold JSON numbers (read back as exact Decimal); 1.25 is exact in binary, so float is safe here
    (root / "snap.json").write_text(json.dumps([entry], default=float))
    assert run(["build", "s.md", "--snapshot", "snap.json"], capsys)[1] == "P 1.25 USD\n"
    assert run(["build", "s.md"], capsys)[0] == 1  # no snapshot: unresolved, never fetched
    (root / "bad.json").write_text("{not json")
    assert run(["build", "s.md", "--snapshot", "bad.json"], capsys)[0] == 2
    assert run(["build", "s.md", "--snapshot", "missing.json"], capsys)[0] == 2


def test_fx_options(capsys, root):
    (root / "fx.md").write_text("---\nvariables:\n  p: 100 EUR\n---\n" + FENCE + "calc\nu = p to USD\n" + FENCE + "\nv {{calc.u}}\n")
    obs = [{"id": "o", "from": "EUR", "to": "USD", "rate": "1.1", "observed_at": "2026-11-12T00:00:00Z", "provider": "t"}]
    (root / "obs.json").write_text(json.dumps(obs))
    assert run(["build", "fx.md"], capsys)[0] == 1
    out = run(["build", "fx.md", "--observations", "obs.json", "--valuation", "2026-11-13T00:00:00Z"], capsys)[1]
    assert out == "v 110 USD\n"


# ---------------------------------------------------------------- packages
def test_plain_package(capsys, root):
    (root / "p.mdq").write_bytes(zpkg())
    assert run(["build", "--package", "p.mdq"], capsys)[1] == "# Pkg x\n"
    assert run(["build", "--package", "p.mdq", "-o", "p.mdq"], capsys)[0] == 2
    assert (root / "p.mdq").read_bytes() == zpkg()
    rc, obj = run_json(["sources", "--package", "p.mdq"], capsys)
    assert rc == 0 and obj["declarations"] == []


def test_encrypted_package(capsys, root):
    ident = x25519.Identity.generate()
    (root / "p.mdq.age").write_bytes(pyrage.encrypt(zpkg(), [ident.to_public()]))
    (root / "key.txt").write_text(f"# created for a test\n{ident}\n")
    assert run(["build", "--package", "p.mdq.age"], capsys)[0] == 4  # no credentials
    assert run(["build", "--package", "p.mdq.age", "--identity", "key.txt"], capsys)[0] == 4  # no plaintext permission
    assert run(["build", "--package", "p.mdq.age", "--identity", "key.txt", "--allow-plaintext"], capsys)[1] == "# Pkg x\n"
    assert run(["check", "--package", "p.mdq.age", "--identity", "key.txt"], capsys)[0] == 0
    (root / "pw.mdq.age").write_bytes(mi.age_encrypt(zpkg(), bytes(48), passphrase="s3cret", work_factor=4))
    (root / "pw.txt").write_text("s3cret\nignored second line\n")
    assert run(["build", "--package", "pw.mdq.age", "--passphrase-file", "pw.txt", "--allow-plaintext"], capsys)[0] == 0
    code, _, err = run(["build", "--package", "pw.mdq.age", "--passphrase-file", "key.txt", "--allow-plaintext"], capsys)
    assert code == 4 and "s3cret" not in err


# ---------------------------------------------------------------- offline
def test_cli_never_opens_a_socket(capsys, root, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the CLI tried to open a network connection")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for cmd in ("build", "check", "sources"):
        assert run([cmd, "docs/r.md"], capsys)[0] == 0


# ---------------------------------------------------------------- real processes
def _proc(args, cwd):
    return subprocess.run(args, capture_output=True, text=True, cwd=cwd)


def test_python_dash_m(root):
    r = _proc([sys.executable, "-m", "mdq", "build", "docs/r.md", "--format", "json"], root)
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    VALIDATOR.validate(obj)
    assert obj["markdown"] == "# Hi\n\nTotal 8 EUR\n"
    assert _proc([sys.executable, "-m", "mdq", "build", "missing.md"], root).returncode == 1


def test_console_script(root):
    exe = shutil.which("mdq", path=os.path.dirname(sys.executable)) or shutil.which("mdq")
    if exe is None:
        pytest.skip("the `mdq` console script is not installed (run: pip install -e .)")
    r = _proc([exe, "--version"], root)
    assert r.returncode == 0 and r.stdout.startswith("mdq ")
    r = _proc([exe, "check", "docs/r.md", "--format", "json"], root)
    assert r.returncode == 0 and VALIDATOR.is_valid(json.loads(r.stdout))
