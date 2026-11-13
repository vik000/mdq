"""Epic 8 / E8.4 (F26): wheel smoke test. G55.

`scripts/wheel_smoke.py` builds the release wheel, installs it into a clean venv per interpreter (offline,
`--no-index`) and uses it from outside the repository: import, versions, typing files, CLI, a package, age.
The full run is the L8 release check (slow on a cold build) and is opt-in:

    MDQ_WHEEL_SMOKE=1 pytest tests/unit/test_e8_f84_wheel.py
    MDQ_WHEEL_SMOKE=1 MDQ_WHEEL_PYTHONS=/path/python3.9:/path/python3.13 pytest ...   # more interpreters

Interpreters whose architecture differs from the wheel are reported as SKIP by the script, not as failures.
"""
import importlib.util
import os
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
