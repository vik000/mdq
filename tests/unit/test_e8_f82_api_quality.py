"""Epic 8 / E8.2 (F25): exception hierarchy, typing, Decimal fidelity, thread safety, memory. G53.

* Every stable code raises its own subclass of `mdq.MdqError`; `ReferenceFailure` and `SourceError` group related
  codes; `.code`, `.path`, `.line`, `.column` are always set; `except mdq.MdqError` still catches everything.
* The package ships `py.typed`; `mypy --strict` passes on it and on typed host code; `stubtest` matches the stub of
  the compiled module.
* Numbers come back as `decimal.Decimal`, never float; floats are refused; 28-digit values survive unchanged.
* 8 threads compiling the same document give identical results; 10,000 compiles keep memory bounded.
"""
import concurrent.futures
import os
import resource
import subprocess
import sys
import textwrap
from decimal import Decimal
from pathlib import Path

import pytest

import mdq
from mdq import _internal as mi

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
HIERARCHY = {
    "MDQ_UNDEFINED_VARIABLE": ("UndefinedVariableError", "MdqError"),
    "MDQ_MISSING_IMPORT": ("MissingImportError", "ReferenceFailure"),
    "MDQ_IMPORT_CYCLE": ("ImportCycleError", "ReferenceFailure"),
    "MDQ_AMBIGUOUS_REFERENCE": ("AmbiguousReferenceError", "ReferenceFailure"),
    "MDQ_UNRESOLVED_REFERENCE": ("UnresolvedReferenceError", "ReferenceFailure"),
    "MDQ_DUPLICATE_ID": ("DuplicateIdError", "ReferenceFailure"),
    "MDQ_METADATA_ERROR": ("MetadataError", "MdqError"),
    "MDQ_INVALID_SYNTAX": ("InvalidSyntaxError", "MdqError"),
    "MDQ_UNSUPPORTED_FEATURE": ("UnsupportedFeatureError", "MdqError"),
    "MDQ_SECURITY_DENIED": ("SecurityDeniedError", "MdqError"),
    "MDQ_CALC_ERROR": ("CalcError", "MdqError"),
    "MDQ_RESOURCE_LIMIT": ("ResourceLimitError", "MdqError"),
    "MDQ_SOURCE_UNRESOLVED": ("SourceUnresolvedError", "SourceError"),
    "MDQ_SOURCE_INVALID": ("SourceInvalidError", "SourceError"),
    "MDQ_SOURCE_FETCH_FAILED": ("SourceFetchFailedError", "SourceError"),
    "MDQ_SOURCE_TYPE_ERROR": ("SourceTypeError", "SourceError"),
}


# ---------------------------------------------------------------- exception hierarchy
def test_every_code_has_a_class():
    assert set(HIERARCHY) == set(mi.diagnostic_codes())


@pytest.mark.parametrize("code", sorted(HIERARCHY))
def test_code_raises_its_class(code):
    cls_name, parent = HIERARCHY[code]
    cls, base = getattr(mdq, cls_name), getattr(mdq, parent)
    assert issubclass(cls, base) and issubclass(cls, mdq.MdqError) and issubclass(mdq.MdqError, Exception)
    with pytest.raises(cls) as info:
        mi._diagnostic_probe(code, "a message")
    e = info.value
    assert type(e) is cls and (e.code, e.path, e.line, e.column) == (code, "probe.md", 3, 7)
    assert str(e) == "a message" and cls_name in mdq.__all__


def test_families_and_catch_all():
    assert issubclass(mdq.ReferenceFailure, mdq.MdqError) and issubclass(mdq.SourceError, mdq.MdqError)
    assert not issubclass(mdq.ReferenceFailure, ReferenceError)  # Python's built-in is not shadowed
    assert not issubclass(mdq.SourceTypeError, TypeError)  # argument TypeErrors stay distinct from source errors
    for code in HIERARCHY:
        try:
            mi._diagnostic_probe(code)
        except mdq.MdqError as e:
            assert e.code == code
    with pytest.raises(ValueError):
        mi._diagnostic_probe("MDQ_NOPE")


def test_real_failures_raise_the_specific_classes():
    with pytest.raises(mdq.MissingImportError):
        mdq.compile("nope.md", {})
    with pytest.raises(mdq.CalcError):
        mdq.compile("d.md", {"d.md": "```calc\nx = 1 / 0\n```\n"})
    with pytest.raises(mdq.UndefinedVariableError):
        mdq.compile("d.md", {"d.md": "{{nope}}\n"})
    with pytest.raises(mdq.SecurityDeniedError):
        mdq.compile("../x.md", {})
    with pytest.raises(mdq.SourceUnresolvedError):
        mdq.compile("d.md", {"d.md": "---\nvariables:\n  q:\n    type: number\n---\nx\n"})
    with pytest.raises(mdq.InvalidSyntaxError):
        mi.utf8_decode(b"\xff")


def test_argument_errors_are_not_mdq_errors():
    with pytest.raises(TypeError) as info:
        mdq.compile("d.md", {"d.md": "x"}, inputs={"a": 1.5})
    assert not isinstance(info.value, mdq.MdqError)


# ---------------------------------------------------------------- Decimal fidelity
DIGITS28 = Decimal("1234567890.123456789012345678")


def test_decimals_round_trip_and_never_float():
    doc = "---\nvariables:\n  x: 0.1\n---\n```calc\ny = x * 3\nz = big + 0\n```\n{{big}} {{calc.y}} {{calc.z}}\n"
    r = mdq.compile("d.md", {"d.md": doc}, inputs={"big": DIGITS28})
    assert r.markdown.split("\n")[-2].replace("\\", "") == f"{DIGITS28} 0.3 {DIGITS28}"
    values = [v.value for b in r.calc for v in b.results]
    assert values == [Decimal("0.3"), DIGITS28] and all(type(v) is Decimal for v in values)
    assert r.metadata["variables"]["x"] == Decimal("0.1") and type(r.metadata["variables"]["x"]) is Decimal


@pytest.mark.parametrize("call", [
    lambda: mdq.compile("d.md", {"d.md": "x"}, inputs={"a": 0.1}),
    lambda: mdq.compile("d.md", {"d.md": "x"}, inputs={"a": {"b": [1.0]}}),
    lambda: mdq.compile("d.md", {"d.md": "x"}, observations=[{"rate": 1.1}]),
    lambda: mi.dec_add(0.1, "1"),
])
def test_floats_are_refused(call):
    with pytest.raises(TypeError):
        call()


def test_28_significant_digits_half_even():
    r = mdq.evaluate("d.md", {"d.md": "```calc\na = 2 / 3\nb = 1234567.1 / 3\n```\n"})
    a, b = (v.value for v in r.calc[0].results)
    assert a == Decimal("0.6666666666666666666666666667") and b == Decimal("411522.3666666666666666666667")


# ---------------------------------------------------------------- thread safety and memory
DOC = {"d.md": "---\nvariables:\n  q: 4\n---\n# T ^h\n\n```calc\nx = q * 2.5 EUR\n```\nv: {{calc.x}} {{q}} ==hi==\n\n"
               "![[p.md]]\n", "p.md": "part {{document.variables.q}}\n"}


def test_eight_threads_give_identical_results():
    expected = mdq.compile("d.md", DOC)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: mdq.compile("d.md", DOC), range(200)))
    assert all(r == expected for r in results)


def test_threads_with_errors_and_successes_mixed():
    def job(i):
        try:
            return mdq.compile("d.md", DOC if i % 2 else {"d.md": "{{nope}}\n"}).markdown
        except mdq.UndefinedVariableError as e:
            return e.code
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        out = list(pool.map(job, range(64)))
    assert out[0::2] == ["MDQ_UNDEFINED_VARIABLE"] * 32 and len(set(out[1::2])) == 1


def _rss_bytes():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r if sys.platform == "darwin" else r * 1024


def test_memory_stays_bounded_over_10000_compiles():
    for _ in range(200):  # warm up allocators and caches
        mdq.compile("d.md", DOC)
    before = _rss_bytes()
    for _ in range(10_000):
        mdq.compile("d.md", DOC)
    for _ in range(1_000):
        try:
            mdq.compile("d.md", {"d.md": "{{nope}}\n"})
        except mdq.MdqError:
            pass
    assert _rss_bytes() - before < 32 * 1024 * 1024


# ---------------------------------------------------------------- typing
def test_package_is_typed():
    assert (Path(mdq.__file__).parent / "py.typed").exists()
    assert (Path(mdq.__file__).parent / "_native.pyi").exists()


def _run(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True, cwd=cwd or ROOT)


def test_mypy_strict_on_the_package():
    pytest.importorskip("mypy")
    r = _run("mypy")  # configured in pyproject.toml: strict, files = python/mdq
    assert r.returncode == 0, r.stdout + r.stderr


def test_mypy_strict_on_typed_host_code(tmp_path):
    pytest.importorskip("mypy")
    host = tmp_path / "host.py"
    host.write_text(textwrap.dedent('''
        from decimal import Decimal
        import mdq

        def run(text: str) -> str:
            try:
                r: mdq.Result = mdq.compile("d.md", {"d.md": text}, inputs={"q": Decimal("2")})
            except mdq.SourceError as e:
                return e.code
            except mdq.MdqError as e:
                return f"{e.code} at {e.path}:{e.line}"
            first: Decimal = r.calc[0].results[0].value
            return r.markdown + str(first)

        def data(fs: mdq.MemoryFS) -> int:
            ev: mdq.Evaluation = mdq.evaluate("d.md", fs)
            return len(ev.calc) + len(mdq.validate_sources("d.md", fs))
        '''))
    r = _run("mypy", "--strict", str(host))
    assert r.returncode == 0, r.stdout + r.stderr
    bad = tmp_path / "bad.py"
    bad.write_text("import mdq\nx: int = mdq.compile('d.md', {}).markdown\n")
    r = _run("mypy", "--strict", str(bad))
    assert r.returncode != 0 and "incompatible" in r.stdout.lower(), r.stdout  # mypy catches a misuse


@pytest.mark.parametrize("module", ["mdq._native", "mdq"])
def test_stubtest(module, tmp_path):
    pytest.importorskip("mypy")
    r = _run("mypy.stubtest", module, cwd=tmp_path)  # outside the repo, so the installed package is checked
    assert r.returncode == 0, r.stdout + r.stderr
