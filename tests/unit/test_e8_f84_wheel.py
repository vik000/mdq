"""Epic 8 / E8.4 (F26): wheel smoke test. G55.

`scripts/wheel_smoke.py` builds the release wheel, installs it into a clean venv per interpreter (offline,
`--no-index`) and uses it from outside the repository: import, versions, typing files, CLI, a package, age.
The full run is the L8 release check (slow on a cold build) and is opt-in:

    MDQ_WHEEL_SMOKE=1 pytest tests/unit/test_e8_f84_wheel.py
    MDQ_WHEEL_SMOKE=1 MDQ_WHEEL_PYTHONS=/path/python3.9:/path/python3.13 pytest ...   # more interpreters

Interpreters whose architecture differs from the wheel are reported as SKIP by the script, not as failures.
The sdist (cross-cutting fix, G55): it must carry every file the Rust core embeds with include_str!, at the
same relative path; the fast test checks that, the opt-in run also builds and tests a wheel from the sdist.
"""
import importlib.util
import os
import re
import shutil
import tarfile
import subprocess
import sys
from pathlib import Path

import pytest

import mdq

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "wheel_smoke.py"


def load_script():
    spec = importlib.util.spec_from_file_location("wheel_smoke", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_installed_version_matches_the_rust_workspace():
    assert load_script().workspace_version() == mdq.__version__ == mdq.version_info()["package"]


def test_package_data_files_exist_in_the_source_tree():
    pkg = ROOT / "python" / "mdq"
    for name in ("py.typed", "_native.pyi", "cli.schema.json", "cli.py", "__main__.py"):
        assert (pkg / name).is_file(), name
    assert 'mdq = "mdq.cli:main"' in (ROOT / "pyproject.toml").read_text()


@pytest.mark.skipif(os.environ.get("MDQ_WHEEL_SMOKE") != "1", reason="release check: set MDQ_WHEEL_SMOKE=1 to build and install the wheel")
def test_wheel_smoke():
    pythons = [sys.executable] + [p for p in os.environ.get("MDQ_WHEEL_PYTHONS", "").split(os.pathsep) if p]
    r = subprocess.run([sys.executable, str(SCRIPT), *pythons], capture_output=True, text=True, cwd=ROOT, timeout=1800)
    print(r.stdout)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ok    " + sys.executable in r.stdout


def embedded_files():
    """Every file the Rust sources embed (include_str!/include_bytes!), as repo-relative paths."""
    out = set()
    for rs in (ROOT / "crates").rglob("*.rs"):
        for rel in re.findall(r'include_(?:str|bytes)!\("([^"]+)"\)', rs.read_text()):
            out.add((rs.parent / rel).resolve().relative_to(ROOT).as_posix())
    return out


def test_embedded_files_exist():
    files = embedded_files()
    assert "spec/units.tsv" in files
    assert all((ROOT / f).is_file() for f in files)


def test_sdist_contains_every_embedded_file(tmp_path):
    if importlib.util.find_spec("maturin") is None and shutil.which("maturin") is None:
        pytest.skip("maturin is not installed")
    r = subprocess.run([sys.executable, "-m", "maturin", "sdist", "--out", str(tmp_path)], capture_output=True,
                       text=True, cwd=ROOT, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    (sdist,) = tmp_path.glob("mdq-*.tar.gz")
    with tarfile.open(sdist) as t:
        names = {n.split("/", 1)[1] for n in t.getnames() if "/" in n}
    missing = embedded_files() - names
    assert not missing, f"embedded files missing from the sdist: {missing}"
    for f in ("pyproject.toml", "Cargo.toml", "Cargo.lock", "python/mdq/cli.schema.json", "python/mdq/py.typed"):
        assert f in names, f


@pytest.mark.skipif(os.environ.get("MDQ_WHEEL_SMOKE") != "1", reason="release check: set MDQ_WHEEL_SMOKE=1 to build the wheel from the sdist")
def test_wheel_from_sdist_smoke():
    r = subprocess.run([sys.executable, str(SCRIPT), "--sdist", sys.executable], capture_output=True, text=True,
                       cwd=ROOT, timeout=1800)
    print(r.stdout)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "from the sdist" in r.stdout and "ok    " + sys.executable in r.stdout
