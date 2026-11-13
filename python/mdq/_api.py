"""Public API: `compile`, `evaluate`, file systems and result types (E8.1a, G50).

Thin layer over the Rust pipeline (`mdq._internal.compile_document`): argument checks, file-system
adapters and conversion of the result dicts into frozen dataclasses. The sources are never modified:
everything is read through the file system and the result is new text and data.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePath
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple, Union

from mdq import _internal as _internal_module
from mdq._native import MdqError

# The unstable test hooks are deliberately untyped; the public API below is fully typed.
_i: Any = _internal_module

JsonDict = Dict[str, Any]


class Readable(Protocol):
    """Anything with `read(path)`: the bytes or text of a root-relative path, or None if it does not exist."""

    def read(self, path: str) -> Optional[Union[str, bytes]]: ...

__all__ = ["MemoryFS", "DirectoryFS", "PackageFS", "Readable", "FileSystem", "Entry", "Transport", "JsonDict", "CalcValue", "CalcBlock", "DeclaredValue", "SourceDeclaration",
           "Result", "Evaluation", "compile", "evaluate", "validate_sources", "test_source"]


class MemoryFS:
    """Files held in memory: `{root-relative path: str | bytes}`. Paths use `/` and are case-sensitive."""

    def __init__(self, files: Mapping[str, Union[str, bytes]]) -> None:
        if not isinstance(files, Mapping):
            raise TypeError("MemoryFS takes a mapping of path -> str | bytes")
        for k, v in files.items():
            if not isinstance(k, str) or not isinstance(v, (str, bytes)):
                raise TypeError("MemoryFS takes a mapping of path -> str | bytes")
        self._files = dict(files)

    def read(self, path: str) -> Optional[Union[str, bytes]]:
        return self._files.get(path)


class DirectoryFS:
    """Files under a directory on disk, confined to it (E4.1 rules: `..`, absolute paths and symlinks
    leading outside are MDQ_SECURITY_DENIED). Only reads; never writes."""

    def __init__(self, root: Union[str, "os.PathLike[str]"]) -> None:
        path = os.fspath(root)
        if not isinstance(path, str):
            raise TypeError("DirectoryFS root must be a str or path")
        if not os.path.isdir(path):
            raise ValueError("DirectoryFS root must be an existing directory")
        self._root: str = path

    def read(self, path: str) -> Optional[bytes]:
        try:
            real = _i.path_confine(self._root, path)
        except MdqError as e:
            if e.code == "MDQ_MISSING_IMPORT":
                return None
            raise
        with open(real, "rb") as f:
            data: bytes = f.read()
        return data


class PackageFS:
    """The files of a `.mdq` package, or of a whole-package encrypted `.mdq.age` (decrypted with the
    host's `identities` / `passphrase`). Opening checks everything (hostile zip, manifest, entrypoint) and
    never fetches. Read-only; `mdq.compile(pkg)` compiles the entrypoint."""

    def __init__(self, data: bytes, *, identities: Optional[Sequence[str]] = None, passphrase: Optional[str] = None,
                 limits: Optional[Mapping[str, int]] = None, max_work_factor: int = 20) -> None:
        if not isinstance(data, bytes):
            raise TypeError("PackageFS takes the package as bytes")
        self.encrypted: bool = bool(_i.age_is(data))
        ids = list(identities) if identities is not None else None
        p: JsonDict
        if self.encrypted:
            p = _i.package_open_age(data, ids, passphrase, limits, max_work_factor)
        else:
            p = _i.package_open(data, limits)
        self.manifest: JsonDict = {k: p[k] for k in ("format", "version", "entrypoint", "features")}
        self.entrypoint: str = p["entrypoint_member"]
        self.metadata: JsonDict = p["metadata"]
        self.requests: JsonDict = p["requests"]
        self.files: Tuple[str, ...] = tuple(p["files"])
        self.directories: Tuple[str, ...] = tuple(p["directories"])
        # Member names are unique in NFC (E7.1); look paths up the same way.
        self._by_id: Dict[str, bytes] = {_i.path_identity(n): d for n, d in p["files"].items()}

    def read(self, path: str) -> Optional[bytes]:
        return self._by_id.get(_i.path_identity(path))

    def _info(self) -> JsonDict:
        return {"metadata": self.metadata, "requests": self.requests, "encrypted": self.encrypted}


@dataclass(frozen=True)
class CalcValue:
    name: str
    line: int
    column: int
    type: str  # "decimal" | "quantity" | "money"
    value: Decimal
    unit: Optional[str]
    currency: Optional[str]
    display: str


@dataclass(frozen=True)
class CalcBlock:
    path: str
    line: int  # 1-based line of the opening fence
    source: str  # block content as written (without the fence lines)
    results: Tuple[CalcValue, ...]


@dataclass(frozen=True)
class DeclaredValue:
    """A declared variable (`type`/`source` under `variables:`) with its value: from the snapshot
    (then `retrieved_at` and `source` are set) or from `inputs`."""
    name: str  # dotted name under `variables:`
    type: str  # "number" | "string" | "boolean" | "date"
    value: Any  # Decimal | str | bool | "YYYY-MM-DD"
    unit: Optional[str]
    retrieved_at: Optional[str]
    source: Optional[JsonDict]


@dataclass(frozen=True)
class Evaluation:
    metadata: JsonDict
    provenance: Tuple[Tuple[str, str], ...]
    calc: Tuple[CalcBlock, ...]
    values: Tuple[DeclaredValue, ...]


@dataclass(frozen=True)
class Result(Evaluation):
    markdown: str = ""


@dataclass(frozen=True)
class SourceDeclaration:
    """A declared variable (E6.1), checked offline."""
    name: str
    type: str
    unit: Optional[str]
    source: Optional[JsonDict]  # None = host-supplied
    url: Optional[str]  # http sources: the URL with its arguments filled in


_LIMITS = ("max_iterations", "max_depth", "max_expansions", "max_bytes")

FileSystem = Union[MemoryFS, DirectoryFS, PackageFS, Mapping[str, Union[str, bytes]], Readable]
Entry = Union[str, PurePath, PackageFS, None]
Transport = Callable[[JsonDict], JsonDict]


def _reader(fs: Any) -> Callable[[str], Any]:
    if isinstance(fs, Mapping):
        fs = MemoryFS(fs)
    read: Any = getattr(fs, "read", None)
    if not callable(read):
        raise TypeError("fs must be a MemoryFS, a DirectoryFS, a dict, or an object with read(path)")
    reader: Callable[[str], Any] = read
    return reader


def _entry(entry: Any) -> str:
    if isinstance(entry, PurePath):
        return entry.as_posix()
    if not isinstance(entry, str):
        raise TypeError("entry must be a str or a path (root-relative)")
    return entry


def _limits(limits: Optional[Mapping[str, int]]) -> Dict[str, int]:
    if limits is None:
        return {}
    if not isinstance(limits, Mapping):
        raise TypeError("limits must be a dict")
    out: Dict[str, int] = {}
    for k, v in limits.items():
        if k not in _LIMITS:
            raise ValueError(f"unknown limit {k!r} (known: {', '.join(_LIMITS)})")
        if isinstance(v, bool) or not isinstance(v, int):
            raise TypeError(f"limit {k} must be an int")
        if v < 1:
            raise ValueError(f"limit {k} must be at least 1")
        out[k] = v
    return out


def _target(entry: Any, fs: Any) -> Tuple[str, Any, Optional[JsonDict]]:
    """(entry, fs, package info): `compile(pkg)` and `compile(None, pkg)` use the package's entrypoint."""
    if fs is None and isinstance(entry, PackageFS):
        entry, fs = None, entry
    if isinstance(fs, PackageFS):
        return (fs.entrypoint if entry is None else _entry(entry)), fs, fs._info()
    if fs is None:
        raise TypeError("fs is required (a MemoryFS, DirectoryFS, PackageFS, dict, or object with read(path))")
    return _entry(entry), fs, None


def _run(entry: Any, fs: Any, inputs: Any, limits: Optional[Mapping[str, int]], data: JsonDict, **kw: Any) -> JsonDict:
    if inputs is not None and not isinstance(inputs, Mapping):
        raise TypeError("inputs must be a dict")
    path, target, package = _target(entry, fs)
    r: JsonDict = _i.compile_document(path, _reader(target), dict(inputs) if inputs is not None else None,
                                      **kw, **_limits(limits), **data, package=package)
    return r


def _data(snapshot: Any, observations: Any, valuation: Any, market: Any, pins: Any) -> JsonDict:
    return {"snapshot": snapshot, "observations": observations, "valuation": valuation, "market": market, "pins": pins}


def _values(raw: List[JsonDict]) -> Tuple[DeclaredValue, ...]:
    return tuple(DeclaredValue(**v) for v in raw)


def _blocks(raw: List[JsonDict]) -> Tuple[CalcBlock, ...]:
    return tuple(CalcBlock(b["path"], b["line"], b["source"], tuple(CalcValue(**v) for v in b["results"])) for b in raw)


def compile(entry: Entry, fs: Optional[FileSystem] = None, *, inputs: Optional[Mapping[str, Any]] = None, profile: str = "commonmark",
            calc_blocks: str = "remove", limits: Optional[Mapping[str, int]] = None, allow_plaintext: bool = False,
            snapshot: Optional[Sequence[JsonDict]] = None, observations: Optional[Sequence[Mapping[str, str]]] = None,
            valuation: Optional[str] = None, market: Optional[str] = None,
            pins: Optional[Mapping[str, Mapping[str, str]]] = None) -> Result:
    """Compile the document `entry` (root-relative) read from `fs` into portable Markdown.

    `inputs` are host values visible as `variables.<name>` (the top metadata layer; numbers as int or
    Decimal, never float); they also fill declared host-supplied variables. `snapshot` holds recorded
    values for declared source variables (never fetched here). `observations`/`valuation`/`market`/`pins`
    are FX data for currency conversions in calc. `calc_blocks="remove"` leaves ```calc blocks out of the
    output, `"keep"` keeps their source; their results are always in `Result.calc`.
    `compile(pkg)` compiles a PackageFS's entrypoint; an encrypted package needs `allow_plaintext=True`.
    """
    if calc_blocks not in ("remove", "keep"):
        raise ValueError("calc_blocks must be 'remove' or 'keep'")
    if not isinstance(allow_plaintext, bool):
        raise TypeError("allow_plaintext must be a bool")
    r = _run(entry, fs, inputs, limits, _data(snapshot, observations, valuation, market, pins),
             profile=profile, keep_calc=calc_blocks == "keep", markdown=True, allow_plaintext=allow_plaintext)
    return Result(r["metadata"], tuple(map(tuple, r["provenance"])), _blocks(r["calc"]), _values(r["values"]),
                  r["markdown"])


def evaluate(entry: Entry, fs: Optional[FileSystem] = None, *, inputs: Optional[Mapping[str, Any]] = None,
             limits: Optional[Mapping[str, int]] = None, snapshot: Optional[Sequence[JsonDict]] = None,
             observations: Optional[Sequence[Mapping[str, str]]] = None, valuation: Optional[str] = None,
             market: Optional[str] = None, pins: Optional[Mapping[str, Mapping[str, str]]] = None) -> Evaluation:
    """Like `compile`, but returns only the data (metadata, calc results, declared values); no Markdown."""
    r = _run(entry, fs, inputs, limits, _data(snapshot, observations, valuation, market, pins), markdown=False)
    return Evaluation(r["metadata"], tuple(map(tuple, r["provenance"])), _blocks(r["calc"]), _values(r["values"]))


def validate_sources(entry: Entry, fs: Optional[FileSystem] = None) -> Tuple[SourceDeclaration, ...]:
    """Every declared variable of the document's effective metadata (manifest and all metadata files),
    checked offline: schema, URL form, type and selection syntax. No I/O beyond reading the files."""
    path, target, package = _target(entry, fs)
    return tuple(SourceDeclaration(**d) for d in _i.validate_document_sources(path, _reader(target), package))


def test_source(entry: Entry, fs: Optional[FileSystem] = None, *, name: str, transport: Transport, retrieved_at: str,
                host_policy: Optional[JsonDict] = None, library_policy: Optional[JsonDict] = None,
                allow_network: bool = True, headers: Optional[Mapping[str, str]] = None) -> JsonDict:
    """One authorised retrieval of the declared variable `name` through the host's `transport` (E6.5).

    The network is allowed only if BOTH `host_policy` and `library_policy` say so; the document's requests
    (manifest, metadata files) can only narrow that. Returns a snapshot entry for `compile(snapshot=[...])`.
    `retrieved_at` is supplied by the host (`YYYY-MM-DDTHH:MM:SSZ`); the library never reads the clock.
    """
    if not isinstance(name, str):
        raise TypeError("name must be a str")
    path, target, package = _target(entry, fs)
    r: JsonDict = _i.test_document_source(path, _reader(target), name, host_policy, library_policy, transport,
                                          retrieved_at, allow_network, headers, package)
    return r


setattr(test_source, "__test__", False)  # not a pytest test, despite the name
