"""Conformance fixtures (TEST_PLAN 3.3, G56): every case in conformance/cases/*.json runs through the library
(`mdq.compile`) and, for parity, through the CLI (`mdq build --format json`, in-process); a handful also through a
real `mdq` subprocess. A case with "xfail" is a strict xfail tied to a documented spec gap.
"""
import json
import subprocess
import sys

import pytest

import mdq
from mdq.cli import main
from cases_loader import ROOT, load_cases  # noqa: E402  (tests/conformance/cases_loader.py)

pytestmark = pytest.mark.conformance
jsonschema = pytest.importorskip("jsonschema")
CASES = load_cases()
FIXTURE_SCHEMA = jsonschema.Draft202012Validator(json.loads((ROOT / "conformance" / "fixture.schema.json").read_text()))
CLI_SCHEMA = jsonschema.Draft202012Validator(json.loads((ROOT / "python" / "mdq" / "cli.schema.json").read_text()))


def params(cases):
    out = []
    for c in cases:
        marks = [pytest.mark.xfail(strict=True, reason=c["xfail"])] if "xfail" in c else []
        out.append(pytest.param(c, id=c["id"], marks=marks))
    return out


def check_expectation(case, markdown=None, diag=None):
    want = case["expect"]
    if "output" in want:
        assert diag is None, f"unexpected error {diag}"
        assert markdown == want["output"]
    else:
        assert diag is not None, f"expected {want['diagnostics'][0]['code']}, got output {markdown!r}"
        w = want["diagnostics"][0]
        for k in ("code", "path", "line", "column"):
            if k in w:
                assert diag[k] == w[k], (k, diag, w)


def write_files(case, root):
    for name, text in case["files"].items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))


def cli_args(case, root):
    o = case.get("options", {})
    args = ["build", case["entry"], "--root", str(root), "--format", "json"]
    for k, v in o.get("inputs", {}).items():
        args += ["--input", f"{k}={'true' if v is True else 'false' if v is False else v}"]
    if "profile" in o:
        args += ["--profile", o["profile"]]
    if "calc_blocks" in o:
        args += ["--calc-blocks", o["calc_blocks"]]
    return args


def test_fixture_ids_are_unique_and_valid():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "duplicate fixture id"
    for c in CASES:
        FIXTURE_SCHEMA.validate({k: v for k, v in c.items() if k != "_file"})


@pytest.mark.parametrize("case", params(CASES))
def test_library(case):
    o = case.get("options", {})
    try:
        r = mdq.compile(case["entry"], dict(case["files"]), **o)
    except mdq.MdqError as e:
        check_expectation(case, diag={"code": e.code, "path": e.path, "line": e.line, "column": e.column})
        return
    check_expectation(case, markdown=r.markdown)


@pytest.mark.parametrize("case", params(CASES))
def test_cli_parity(case, tmp_path, capsys):
    write_files(case, tmp_path)
    code = main(cli_args(case, tmp_path))
    obj = json.loads(capsys.readouterr().out)
    CLI_SCHEMA.validate(obj)
    if obj["ok"]:
        assert code == 0
        check_expectation(case, markdown=obj["markdown"])
    else:
        assert code != 0 and obj["diagnostics"], obj
        check_expectation(case, diag=obj["diagnostics"][0])


SUBPROCESS_CASES = [c for c in CASES if "xfail" not in c][:6]


@pytest.mark.parametrize("case", params(SUBPROCESS_CASES))
def test_cli_subprocess(case, tmp_path):
    write_files(case, tmp_path)
    r = subprocess.run([sys.executable, "-m", "mdq", *cli_args(case, tmp_path)], capture_output=True, text=True)
    obj = json.loads(r.stdout)
    CLI_SCHEMA.validate(obj)
    if obj["ok"]:
        check_expectation(case, markdown=obj["markdown"])
    else:
        check_expectation(case, diag=obj["diagnostics"][0])
