"""Wheel smoke test (E8.4, G55): build the wheel, install it into a clean venv per Python, use it.

    python scripts/wheel_smoke.py [--sdist] [PYTHON ...]      # default: the running interpreter

`--sdist`: build the source distribution, unpack it, build the wheel FROM it (proves the sdist is complete,
e.g. carries `spec/units.tsv`, which the core embeds from outside its crate), then test that wheel.

For each interpreter: create a fresh venv, install the wheel with `--no-index` (no network, no source tree),
then, from a temporary directory outside the repository:
  * import mdq; the version equals the Rust workspace version; `version_info()` is complete;
  * the installed package holds `py.typed`, `_native.pyi`, `cli.schema.json` (and no tests or Rust sources);
  * `mdq --version`, `mdq build` (calc, imports, metadata) and `mdq check --format json` work;
  * the library compiles a package and round-trips age encryption (the crypto is in the release build).
Exits 0 when every check passed, 1 otherwise. Writes nothing into the repository except the built wheel's
temporary directory, which is removed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHECK = textwrap.dedent(r'''
    import io, json, os, sys, zipfile
    from pathlib import Path
    import mdq
    from mdq import _internal as mi

    expected_version, repo = sys.argv[1], sys.argv[2]
    pkg_dir = Path(mdq.__file__).parent
    assert not str(pkg_dir).startswith(repo), f"imported from the source tree: {pkg_dir}"
    assert mdq.__version__ == expected_version, (mdq.__version__, expected_version)
    v = mdq.version_info()
    assert set(v) == {"package", "spec", "syntax", "manifest", "unit_catalogue"}, v
    for name in ("py.typed", "_native.pyi", "cli.schema.json", "cli.py", "__main__.py"):
        assert (pkg_dir / name).is_file(), name
    assert not any(p.suffix == ".rs" for p in pkg_dir.rglob("*")), "Rust sources in the wheel"

    # a package, compiled by the library
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.yaml", "format: mdq\nversion: '0.1'\nentrypoint: document.md\nfeatures: [core, calc, package]\nt: Pkg\n")
        z.writestr("document.md", "# {{document.t}}\n\n```calc\nx = 2 * 3 km\n```\nx: {{calc.x}}\n")
    data = buf.getvalue()
    assert mdq.compile(mdq.PackageFS(data)).markdown == "# Pkg\n\nx: 6 km\n"

    # age round trip with the release-built crypto
    sk = mi.age_identity(bytes(range(1, 33)))
    sealed = mi.age_encrypt(data, os.urandom(mi.age_random_len(1)), [mi.age_recipient(sk)])
    assert mdq.compile(mdq.PackageFS(sealed, identities=[sk]), allow_plaintext=True).markdown == "# Pkg\n\nx: 6 km\n"

    # the exception hierarchy and Decimal results survive packaging
    try:
        mdq.compile("nope.md", {})
    except mdq.MissingImportError as e:
        assert isinstance(e, mdq.ReferenceFailure) and e.code == "MDQ_MISSING_IMPORT"
    r = mdq.evaluate("d.md", {"d.md": "```calc\na = 1 / 3\n```\n"})
    assert str(r.calc[0].results[0].value) == "0.3333333333333333333333333333"
    print("library ok")
''')


def run(cmd, **kw):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd))} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")
    return r


def workspace_version() -> str:
    m = re.search(r'^\[workspace\.package\][^\[]*?^version\s*=\s*"([^"]+)"', (ROOT / "Cargo.toml").read_text(), re.M | re.S)
    if not m:
        raise RuntimeError("no [workspace.package] version in Cargo.toml")
    return m.group(1)


def build_wheel(out: Path) -> Path:
    run([sys.executable, "-m", "maturin", "build", "--release", "--out", out], cwd=ROOT)
    wheels = list(out.glob("mdq-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel, found {wheels}")
    names = zipfile.ZipFile(wheels[0]).namelist()
    for needed in ("mdq/py.typed", "mdq/_native.pyi", "mdq/cli.schema.json", "mdq/cli.py"):
        if needed not in names:
            raise RuntimeError(f"{needed} is missing from the wheel")
    if not any(n.endswith("entry_points.txt") for n in names):
        raise RuntimeError("the wheel has no entry_points.txt (console script)")
    if any(n.startswith("tests/") or n.endswith(".rs") for n in names):
        raise RuntimeError("the wheel contains tests or Rust sources")
    return wheels[0]


def build_from_sdist(out: Path) -> Path:
    import tarfile
    sd = out / "sdist"
    run([sys.executable, "-m", "maturin", "sdist", "--out", sd], cwd=ROOT)
    tars = list(sd.glob("mdq-*.tar.gz"))
    if len(tars) != 1:
        raise RuntimeError(f"expected one sdist, found {tars}")
    with tarfile.open(tars[0]) as t:
        names = t.getnames()
        if not any(n.endswith("/spec/units.tsv") for n in names):
            raise RuntimeError("the sdist does not contain spec/units.tsv")
        if sys.version_info >= (3, 12):
            t.extractall(sd, filter="data")
        else:
            t.extractall(sd)
    src = next(p for p in sd.iterdir() if p.is_dir())
    run([sys.executable, "-m", "pip", "wheel", "--quiet", "--no-deps", "--no-build-isolation", "-w", out, "."], cwd=src)
    wheels = list(out.glob("mdq-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel built from the sdist, found {wheels}")
    return wheels[0]


def smoke(python: str, wheel: Path, version: str, tmp: Path) -> None:
    env_dir = tmp / f"venv-{Path(python).name}"
    run([python, "-m", "venv", env_dir])
    bindir = env_dir / ("Scripts" if os.name == "nt" else "bin")
    py, mdq_exe = bindir / "python", bindir / "mdq"
    run([py, "-m", "pip", "install", "--quiet", "--no-index", wheel])
    work = tmp / f"work-{Path(python).name}"
    (work / "docs").mkdir(parents=True)
    (work / ".mdq.yaml").write_text("variables:\n  qty: 4\n")
    (work / "docs" / "r.md").write_text("# Report ^top\n\n```calc\nt = qty * 2.5 EUR\n```\nTotal {{calc.t}}\n\n![[part.md]]\n")
    (work / "docs" / "part.md").write_text("Part for {{qty}} items.\n")
    clean_env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
    run([py, "-c", CHECK, version, str(ROOT)], cwd=work, env=clean_env)
    out = run([mdq_exe, "--version"], cwd=work, env=clean_env).stdout
    if not out.startswith(f"mdq {version} "):
        raise RuntimeError(f"unexpected --version output: {out!r}")
    md = run([mdq_exe, "build", "docs/r.md"], cwd=work, env=clean_env).stdout
    if md != "# Report\n\nTotal 10 EUR\n\nPart for 4 items.\n":
        raise RuntimeError(f"unexpected build output: {md!r}")
    obj = json.loads(run([mdq_exe, "check", "docs/r.md", "--format", "json"], cwd=work, env=clean_env).stdout)
    if not (obj["ok"] and obj["calc"][0]["results"][0]["value"] == "10"):
        raise RuntimeError(f"unexpected check output: {obj!r}")
    r = subprocess.run([str(mdq_exe), "build", "docs/missing.md"], cwd=work, env=clean_env, capture_output=True, text=True)
    if r.returncode != 1 or "MDQ_MISSING_IMPORT" not in r.stderr:
        raise RuntimeError(f"unexpected failure behaviour: {r.returncode} {r.stderr!r}")


def main(argv: list) -> int:
    from_sdist = "--sdist" in argv
    pythons = [a for a in argv if a != "--sdist"] or [sys.executable]
    version = workspace_version()
    failures = 0
    with tempfile.TemporaryDirectory(prefix="mdq-wheel-") as t:
        tmp = Path(t)
        if from_sdist:
            wheel = build_from_sdist(tmp / "dist")
            print(f"built {wheel.name} from the sdist")
        else:
            wheel = build_wheel(tmp / "dist")
            print(f"built {wheel.name}")
        wheel_arch = "arm64" if "arm64" in wheel.name or "aarch64" in wheel.name else (
            "x86_64" if "x86_64" in wheel.name else None)
        tested = 0
        for python in pythons:
            try:
                ver, arch = run([python, "-c", "import platform, sys; print('%d.%d' % sys.version_info[:2], "
                                               "platform.machine())"]).stdout.split()
                if wheel_arch and arch.replace("aarch64", "arm64") != wheel_arch:
                    # e.g. an x86_64 Python under Rosetta on Apple Silicon: it cannot load this wheel
                    print(f"SKIP  {python} (Python {ver}, {arch}): the wheel is for {wheel_arch}")
                    continue
                smoke(python, wheel, version, tmp)
                tested += 1
                print(f"ok    {python} (Python {ver}, {arch})")
            except Exception as e:  # report every interpreter, then fail
                failures += 1
                print(f"FAIL  {python}: {e}")
    if tested == 0 and failures == 0:
        print("FAIL  no interpreter could be tested")
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
