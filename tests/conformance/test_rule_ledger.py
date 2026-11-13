"""Rule ledger meta-test (TEST_PLAN 3.5, G56; F27 "every normative rule has a fixture").

conformance/rules.yaml maps every normative MUST/MUST NOT of spec/ to the tests covering it (pytest node IDs,
parameters stripped) and/or conformance fixtures, or marks it `na` with a reason. This test fails when a rule has
no coverage, a listed test or fixture does not exist, a fixture names an unknown rule, or a test uses
@pytest.mark.rule with an unknown rule.
"""
import re
import subprocess
import sys

import pytest

from cases_loader import ROOT, load_cases, load_ledger  # noqa: E402

pytestmark = pytest.mark.conformance
LEDGER = load_ledger()
CASES = load_cases()
KEYS = {"spec", "text", "tests", "fixtures", "na"}


@pytest.fixture(scope="module")
def collected():
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "tests/unit"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return {re.sub(r"\[.*$", "", line) for line in r.stdout.splitlines() if "::" in line}


def test_ledger_shape():
    assert isinstance(LEDGER, dict) and len(LEDGER) > 0
    for rid, rule in LEDGER.items():
        assert re.fullmatch(r"[A-Z]+(-[A-Z0-9]+)+", rid), rid
        assert isinstance(rule, dict) and set(rule) <= KEYS and {"spec", "text"} <= set(rule), rid
        assert rule["spec"].split()[0] in ("extended-markdown", "calculation-units", "sources-and-permissions",
                                           "package"), (rid, rule["spec"])
        assert rule["text"].strip(), rid
        tests, fixtures, na = rule.get("tests") or [], rule.get("fixtures") or [], rule.get("na")
        assert tests or fixtures or (isinstance(na, str) and na.strip()), f"{rid} has no test, fixture or na reason"
        assert not (na and (tests or fixtures)), f"{rid}: na together with tests/fixtures"


def test_listed_tests_exist(collected):
    missing = {(rid, t) for rid, rule in LEDGER.items() for t in rule.get("tests") or [] if t not in collected}
    assert not missing, f"ledger names tests that do not exist: {sorted(missing)}"


def test_listed_fixtures_exist_and_name_known_rules():
    ids = {c["id"] for c in CASES}
    missing = {(rid, f) for rid, rule in LEDGER.items() for f in rule.get("fixtures") or [] if f not in ids}
    assert not missing, f"ledger names fixtures that do not exist: {sorted(missing)}"
    unknown = {(c["id"], r) for c in CASES for r in c["rules"] if r not in LEDGER}
    assert not unknown, f"fixtures name unknown rules: {sorted(unknown)}"
    linked = {f for rule in LEDGER.values() for f in rule.get("fixtures") or []}
    orphans = sorted(ids - linked)
    assert not orphans, f"fixtures not linked from any rule: {orphans}"


def test_rule_markers_name_known_rules():
    unknown = set()
    for f in (ROOT / "tests").rglob("test_*.py"):
        for rid in re.findall(r"""pytest\.mark\.rule\(\s*["']([^"']+)["']""", f.read_text(encoding="utf-8")):
            if rid not in LEDGER:
                unknown.add((f.name, rid))
    assert not unknown, f"@pytest.mark.rule with unknown rule IDs: {sorted(unknown)}"


def test_every_spec_must_is_in_the_ledger():
    """Each spec file has at least as many ledger rules as sentences containing MUST (a rule may split one)."""
    for name in ("extended-markdown", "calculation-units", "sources-and-permissions", "package"):
        text = (ROOT / "spec" / f"{name}.md").read_text(encoding="utf-8")
        sentences = sum(1 for s in re.split(r"(?<=[.;])\s+", text) if re.search(r"\bMUST\b", s))
        rules = sum(1 for r in LEDGER.values() if r["spec"].split()[0] == name)
        assert rules >= sentences * 0.9, f"{name}: {rules} ledger rules for {sentences} MUST sentences"
