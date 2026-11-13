"""Shared loading of the conformance fixtures and the rule ledger (TEST_PLAN 3.3, 3.5; G56)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = ROOT / "conformance" / "cases"
LEDGER = ROOT / "conformance" / "rules.yaml"


def load_cases():
    """Every fixture of every conformance/cases/*.json file (each file holds a JSON list), in file order."""
    cases = []
    for f in sorted(CASES_DIR.glob("*.json")):
        for case in json.loads(f.read_text(encoding="utf-8")):
            case = dict(case)
            case["_file"] = f.name
            cases.append(case)
    return cases


def load_ledger():
    """The rule ledger, read with the library's strict YAML loader (duplicate rule IDs fail)."""
    from mdq import _internal as mi
    return mi.yaml_load_strict(LEDGER.read_text(encoding="utf-8"))
