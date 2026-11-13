"""The `mdq` command line (E8.3, G54): a thin host program over the library (D5).

    mdq build ENTRY [--root DIR | --package FILE] [-o FILE] [options]   Markdown to stdout or a file
    mdq check ENTRY [...]                                             evaluate, report diagnostics only
    mdq sources ENTRY [...]                                           list declared variables (offline)
    mdq --version

Offline: no command fetches anything (`test_source` is a library call). Sources are never written:
`-o` refuses any file the command has read. Exit codes: 0 ok, 1 invalid input, 2 usage, 3 unsupported
feature, 4 denied, 5 resource limit. `--format json` prints one object (schema: `mdq/cli.schema.json`).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Union

import mdq

EXIT_OK, EXIT_INVALID, EXIT_USAGE, EXIT_UNSUPPORTED, EXIT_DENIED, EXIT_LIMIT = 0, 1, 2, 3, 4, 5
EXIT_FOR_CODE = {"MDQ_UNSUPPORTED_FEATURE": EXIT_UNSUPPORTED, "MDQ_SECURITY_DENIED": EXIT_DENIED,
                 "MDQ_RESOURCE_LIMIT": EXIT_LIMIT}
EPILOG = """exit codes:
  0  success
  1  invalid input (syntax, metadata, references, variables, calc, sources)
  2  usage error (bad command line, unreadable option file, output would overwrite a source)
  3  unsupported feature
  4  denied capability (MDQ_SECURITY_DENIED)
  5  resource limit
"""
_NUMBER = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?")


class UsageError(Exception):
    """A problem with the command line itself (exit code 2)."""


class _Recording:
    """Wraps a file system and remembers which paths were read (to protect sources from `-o`)."""

    def __init__(self, fs: Any) -> None:
        self.fs: Any = fs
        self.read_paths: List[str] = []

    def read(self, path: str) -> Any:
        self.read_paths.append(path)
        return self.fs.read(path)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mdq", description="MDQ extended Markdown processor (offline).",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="store_true", help="show the package, syntax, manifest and unit catalogue versions")
    sub = p.add_subparsers(dest="command")
    for name, help_text in (("build", "compile a document to portable Markdown"),
                            ("check", "evaluate a document and report diagnostics (no output file)"),
                            ("sources", "list the declared variables of a document (offline)")):
        c = sub.add_parser(name, help=help_text, epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
        c.add_argument("entry", nargs="?", help="root-relative path of the document (default with --package: its entrypoint)")
        where = c.add_mutually_exclusive_group()
        where.add_argument("--root", default=None, help="directory the document and its files live in (default: .)")
        where.add_argument("--package", metavar="FILE", help="a .mdq package or a .mdq.age encrypted package")
        c.add_argument("--identity", metavar="FILE", action="append", default=[],
                       help="age identity file (AGE-SECRET-KEY-1... lines) for .mdq.age; repeatable")
        c.add_argument("--passphrase-file", metavar="FILE", help="file whose first line is the .mdq.age passphrase")
        c.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: text)")
        if name != "sources":
            c.add_argument("--input", metavar="NAME=VALUE", action="append", default=[],
                           help="host value for variables.NAME (number, true/false, or text); repeatable")
            c.add_argument("--snapshot", metavar="FILE", help="JSON list of recorded source values (from test_source)")
            c.add_argument("--observations", metavar="FILE", help="JSON list of FX observations")
            c.add_argument("--valuation", metavar="TIME", help="FX valuation time (ISO 8601 with offset)")
        if name == "build":
            c.add_argument("-o", "--output", metavar="FILE", help="write the Markdown here instead of stdout")
            c.add_argument("--profile", choices=("commonmark", "gfm"), default="commonmark")
            c.add_argument("--calc-blocks", choices=("remove", "keep"), default="remove")
            c.add_argument("--allow-plaintext", action="store_true",
                           help="allow plaintext output of an encrypted package")
    return p


def _read_file(path: str, what: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise UsageError(f"cannot read {what} {path!r}: {e.strerror}") from None


def _json_file(path: Optional[str], what: str) -> Any:
    if path is None:
        return None
    try:
        return json.loads(_read_file(path, what).decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise UsageError(f"{what} {path!r} is not valid JSON: {e}") from None


def _identities(paths: Sequence[str]) -> List[str]:
    out = []
    for path in paths:
        for line in _read_file(path, "identity file").decode("utf-8", "replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def _inputs(pairs: Sequence[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or not name:
            raise UsageError(f"--input needs NAME=VALUE, got {pair!r}")
        if name in out:
            raise UsageError(f"--input {name} is given twice")
        if _NUMBER.fullmatch(value):
            out[name] = Decimal(value)
        elif value in ("true", "false"):
            out[name] = value == "true"
        else:
            out[name] = value
    return out


def _target(args: argparse.Namespace) -> Any:
    """(entry, file system) for the command."""
    if args.package:
        pw = None
        if args.passphrase_file:
            lines = _read_file(args.passphrase_file, "passphrase file").decode("utf-8", "replace").splitlines()
            pw = lines[0] if lines else ""
        pkg = mdq.PackageFS(_read_file(args.package, "package"), identities=_identities(args.identity) or None,
                            passphrase=pw)
        return args.entry, _Recording(pkg), pkg
    if args.identity or args.passphrase_file:
        raise UsageError("--identity and --passphrase-file only apply to --package")
    if args.entry is None:
        raise UsageError("an ENTRY is required (or --package FILE)")
    root = args.root or "."
    if not os.path.isdir(root):
        raise UsageError(f"--root {root!r} is not a directory")
    return args.entry, _Recording(mdq.DirectoryFS(root)), None


def _plain(v: Any) -> Any:
    """JSON-ready value: Decimal as its exact text, tuples as lists."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def _diag(e: mdq.MdqError) -> Dict[str, Any]:
    return {"code": e.code, "message": str(e), "path": e.path, "line": e.line, "column": e.column}


def _protect_sources(output: str, root: Optional[str], package_path: Optional[str], read_paths: Sequence[str]) -> None:
    """Refuse `-o` when it names a file the command read (the document, its metadata, imports, ...)."""
    out = os.path.realpath(output)
    sources = [os.path.realpath(package_path)] if package_path else []
    if root is not None:
        sources += [os.path.realpath(os.path.join(root, p)) for p in read_paths]
    if out in sources:
        raise UsageError(f"refusing to write {output!r}: it is a source file of this document")


def _run(args: argparse.Namespace) -> Dict[str, Any]:
    entry, recording, pkg = _target(args)
    # A package is passed as itself (compile needs its manifest and entrypoint); a directory through the
    # recording wrapper, so `-o` can refuse every file that was read.
    fs: Any = pkg if pkg is not None else recording
    if args.command == "sources":
        return {"declarations": [_plain(asdict(d)) for d in mdq.validate_sources(entry, fs)]}
    kw: Dict[str, Any] = dict(inputs=_inputs(args.input) or None, snapshot=_json_file(args.snapshot, "snapshot file"),
                              observations=_json_file(args.observations, "observations file"), valuation=args.valuation)
    if args.command == "check":
        ev = mdq.evaluate(entry, fs, **kw)
        return {"calc": _plain([asdict(b) for b in ev.calc]), "values": _plain([asdict(v) for v in ev.values])}
    r = mdq.compile(entry, fs, profile=args.profile, calc_blocks=args.calc_blocks,
                    allow_plaintext=args.allow_plaintext, **kw)
    if args.output:
        _protect_sources(args.output, None if pkg is not None else (args.root or "."), args.package,
                         recording.read_paths)
        try:
            with open(args.output, "w", encoding="utf-8", newline="") as f:
                f.write(r.markdown)
        except OSError as e:
            raise UsageError(f"cannot write {args.output!r}: {e.strerror}") from None
    return {"markdown": r.markdown, "calc": _plain([asdict(b) for b in r.calc]),
            "values": _plain([asdict(v) for v in r.values]), "_to_stdout": not args.output}


def _version_text() -> str:
    v = mdq.version_info()
    return (f"mdq {v['package']} (syntax {v['syntax']}, manifest {v['manifest']}, "
            f"unit catalogue {v['unit_catalogue']}, spec {v['spec']})")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)  # bad flags: argparse prints usage and exits with 2
    if args.version:
        print(_version_text())
        return EXIT_OK
    if args.command is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    as_json = args.format == "json"
    try:
        out = _run(args)
    except mdq.MdqError as e:
        code = EXIT_FOR_CODE.get(e.code, EXIT_INVALID)
        if as_json:
            print(json.dumps({"ok": False, "diagnostics": [_diag(e)]}, ensure_ascii=False))
        else:
            where = ":".join(str(x) for x in (e.path, e.line, e.column) if x is not None)
            print(f"{where + ': ' if where else ''}{e.code}: {e}", file=sys.stderr)
        return code
    except (UsageError, TypeError, ValueError) as e:
        if as_json:
            print(json.dumps({"ok": False, "diagnostics": [], "usage_error": str(e)}, ensure_ascii=False))
        else:
            print(f"mdq: error: {e}", file=sys.stderr)
        return EXIT_USAGE
    to_stdout = out.pop("_to_stdout", False)
    if as_json:
        print(json.dumps({"ok": True, "diagnostics": [], **out}, ensure_ascii=False))
    elif args.command == "build":
        if to_stdout:
            sys.stdout.write(out["markdown"])
    elif args.command == "check":
        n = sum(len(b["results"]) for b in out["calc"])
        print(f"ok: {len(out['calc'])} calc blocks, {n} results, {len(out['values'])} declared values")
    else:
        for d in out["declarations"]:
            kind = d["source"]["kind"] if d["source"] else "host"
            print("\t".join([d["name"], d["type"], d["unit"] or "-", kind, d["url"] or "-"]))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
