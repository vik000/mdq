"""Run every example and record (or check) its transcript in `<example>/expected/<name>.txt`.

    python examples/run_examples.py           # rewrite the expected transcripts
    python examples/run_examples.py --check   # compare; exit 1 and show a diff on any difference

A transcript is what a terminal would show: the command, its output (stdout, then stderr) and the exit
code. `tests/unit/test_examples.py` runs `--check`, so the examples can never silently go stale.
Run from anywhere; commands are recorded relative to the repository root.
"""
from __future__ import annotations

import contextlib
import difflib
import io
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# (example folder, transcript name, command) - commands exactly as a user would type them from the repo root.
RUNS = [
    ("01-basics", "build", "mdq build report.md --root examples/01-basics"),
    ("01-basics", "build-input", "mdq build report.md --root examples/01-basics --input reader=Ada"),
    ("02-calc", "invoice", "mdq build invoice.md --root examples/02-calc"),
    ("02-calc", "invoice-keep-calc", "mdq build invoice.md --root examples/02-calc --calc-blocks keep"),
    ("02-calc", "convert-no-rates", "mdq build convert.md --root examples/02-calc"),
    ("02-calc", "convert", "mdq build convert.md --root examples/02-calc --observations examples/02-calc/rates.json"
                           " --valuation 2026-11-14T00:00:00Z"),
    ("02-calc", "check-json", "mdq check invoice.md --root examples/02-calc --format json"),
    ("03-composition", "build", "mdq build book/book.md --root examples/03-composition"),
    ("04-conditionals", "public", "mdq build plan.md --root examples/04-conditionals"),
    ("04-conditionals", "internal", "mdq build plan.md --root examples/04-conditionals --input audience=internal"),
    ("05-rich", "commonmark", "mdq build notes.md --root examples/05-rich"),
    ("05-rich", "gfm", "mdq build notes.md --root examples/05-rich --profile gfm"),
    ("06-data-and-sources", "sources", "mdq sources report.md --root examples/06-data-and-sources"),
    ("06-data-and-sources", "no-snapshot", "mdq build report.md --root examples/06-data-and-sources --input quarter=Q4"),
    ("06-data-and-sources", "build", "mdq build report.md --root examples/06-data-and-sources --input quarter=Q4"
                                     " --snapshot examples/06-data-and-sources/snapshot.json"),
    ("07-package", "package", "mdq build --package examples/07-package/guide.mdq"),
    ("07-package", "encrypted-no-permission", "mdq build --package examples/07-package/guide.mdq.age"
                                              " --identity examples/07-package/key.txt"),
    ("07-package", "encrypted", "mdq build --package examples/07-package/guide.mdq.age"
                                " --identity examples/07-package/key.txt --allow-plaintext"),
    ("08-python-api", "tour", "python examples/08-python-api/example.py"),
]


def run_cli(argv: list, pkg_dir: Path) -> tuple:
    from mdq.cli import main
    argv = [a.replace("examples/07-package", str(pkg_dir)) for a in argv]
    out, err = io.StringIO(), io.StringIO()
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
    finally:
        os.chdir(cwd)
    return code, out.getvalue() + err.getvalue()


def transcript(command: str, pkg_dir: Path) -> str:
    argv = shlex.split(command)
    if argv[0] == "mdq":
        code, text = run_cli(argv[1:], pkg_dir)
    else:
        r = subprocess.run([sys.executable, *argv[1:]], capture_output=True, text=True, cwd=ROOT)
        code, text = r.returncode, r.stdout + r.stderr
    text = text.replace(str(pkg_dir), "examples/07-package")
    if text and not text.endswith("\n"):
        text += "\n"
    return f"$ {command}\n{text}[exit {code}]\n"


def main(argv: list) -> int:
    check = "--check" in argv
    sys.path.insert(0, str(HERE / "07-package"))
    import make_package  # noqa: E402  (examples/07-package/make_package.py)
    failures = 0
    with tempfile.TemporaryDirectory(prefix="mdq-examples-") as tmp:
        pkg_dir = Path(tmp)
        make_package.build(pkg_dir)  # packages are generated, not committed (encryption is randomised)
        for example, name, command in RUNS:
            path = HERE / example / "expected" / f"{name}.txt"
            got = transcript(command, pkg_dir)
            if check:
                want = path.read_text(encoding="utf-8") if path.exists() else ""
                if got != want:
                    failures += 1
                    print(f"DIFFERS: {path.relative_to(ROOT)}")
                    sys.stdout.writelines(difflib.unified_diff(want.splitlines(True), got.splitlines(True),
                                                               "expected", "actual"))
            else:
                path.parent.mkdir(exist_ok=True)
                path.write_text(got, encoding="utf-8")
    if check:
        print("all examples match" if not failures else f"{failures} example transcript(s) differ")
    else:
        print(f"wrote {len(RUNS)} transcripts")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
