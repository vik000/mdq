# MDQ library — instructions for Claude Code

Rust core exposed to Python via PyO3/maturin. Spec: `spec/` (normative). Plan: `docs/TEST_PLAN.md`.
The owner is a Python developer learning Rust: explain Rust code and idioms clearly.

## Rules
- All logic in `crates/mdq-core` (no pyo3). `crates/mdq-python` = thin wrappers only.
- Every non-trivial core function is exposed in `mdq._internal` and tested from Python.
- All tests are Python (`tests/`), one file per feature (`test_e<epic>_f<NN>_<name>.py`), never overwrite existing test files.
- Every code path ends in: valid output, explicitly ignored, explicitly repaired, or explicitly rejected with a diagnostic. No silent corruption.
- Never `panic = "abort"`. A Rust panic must surface as `PanicException` (a test failure).
- Spec ambiguity: do not invent behaviour. Add `xfail(strict=True)` tests tagged with the gap ID (docs/TEST_PLAN.md §6) and ask.
- One feature at a time; the owner confirms tests pass locally before the next.
- Core never touches network, clock or environment.

## Commands
```
python -m venv .venv && source .venv/bin/activate
pip install maturin && pip install -e '.[dev]'   # or: maturin develop
pytest -q
```
