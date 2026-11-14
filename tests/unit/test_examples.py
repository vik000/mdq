"""The examples in examples/ (README index, one folder per topic) keep producing their recorded output.

`examples/run_examples.py --check` runs every documented command and compares it with
`examples/<name>/expected/*.txt`; regenerate with `python examples/run_examples.py` after an intended change.
"""
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_examples_match_their_recorded_output():
    r = subprocess.run([sys.executable, str(ROOT / "examples" / "run_examples.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_example_is_listed_and_has_expected_output():
    readme = (ROOT / "examples" / "README.md").read_text()
    folders = sorted(p.name for p in (ROOT / "examples").iterdir() if p.is_dir() and p.name[:2].isdigit())
    assert folders == [f"0{i}" + folders[i - 1][2:] for i in range(1, len(folders) + 1)]  # numbered 01..
    for name in folders:
        assert f"[{name}]({name}/)" in readme, name
        assert list((ROOT / "examples" / name / "expected").glob("*.txt")), name


def test_snapshot_is_reproducible():
    sys.path.insert(0, str(ROOT / "examples" / "06-data-and-sources"))
    import record_snapshot
    assert record_snapshot.record() == (ROOT / "examples" / "06-data-and-sources" / "snapshot.json").read_text()
