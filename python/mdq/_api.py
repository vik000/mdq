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
from typing import Any, Callable, Mapping, Optional, Tuple, Union

from mdq import _internal as _i
from mdq._native import MdqError

__all__ = ["MemoryFS", "DirectoryFS", "CalcValue", "CalcBlock", "Result", "Evaluation", "compile", "evaluate"]


class MemoryFS:
    """Files held in memory: `{root-relative path: str | bytes}`. Paths use `/` and are case-sensitive."""

    def __init__(self, files: Mapping[str, Union[str, bytes]]):
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

    def __init__(self, root: Union[str, "os.PathLike[str]"]):
        root = os.fspath(root)
        if not isinstance(root, str):
            raise TypeError("DirectoryFS root must be a str or path")
        if not os.path.isdir(root):
            raise ValueError("DirectoryFS root must be an existing directory")
        self._root = root

    def read(self, path: str) -> Optional[bytes]:
        try:
            real = _i.path_confine(self._root, path)
        except MdqError as e:
            if e.code == "MDQ_MISSING_IMPORT":
                return None
            raise
        with open(real, "rb") as f:
            return f.read()


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
class Evaluation:
    metadata: dict
    provenance: Tuple[Tuple[str, str], ...]
    calc: Tuple[CalcBlock, ...]


@dataclass(frozen=True)
class Result(Evaluation):
    markdown: str = ""


_LIMITS = ("max_iterations", "max_depth", "max_expansions", "max_bytes")


def _reader(fs: Any) -> Callable[[str], Any]:
    if isinstance(fs, Mapping):
        fs = MemoryFS(fs)
    read = getattr(fs, "read", None)
    if not callable(read):
        raise TypeError("fs must be a MemoryFS, a DirectoryFS, a dict, or an object with read(path)")
    return read


def _entry(entry: Any) -> str:
    if isinstance(entry, PurePath):
        return entry.as_posix()
    if not isinstance(entry, str):
        raise TypeError("entry must be a str or a path (root-relative)")
    return entry


def _limits(limits: Optional[Mapping[str, int]]) -> dict:
    if limits is None:
        return {}
    if not isinstance(limits, Mapping):
        raise TypeError("limits must be a dict")
    out = {}
    for k, v in limits.items():
        if k not in _LIMITS:
            raise ValueError(f"unknown limit {k!r} (known: {', '.join(_LIMITS)})")
        if isinstance(v, bool) or not isinstance(v, int):
            raise TypeError(f"limit {k} must be an int")
        if v < 1:
            raise ValueError(f"limit {k} must be at least 1")
        out[k] = v
    return out


def _run(entry, fs, inputs, limits, **kw) -> dict:
    if inputs is not None and not isinstance(inputs, Mapping):
        raise TypeError("inputs must be a dict")
    return _i.compile_document(_entry(entry), _reader(fs), dict(inputs) if inputs is not None else None,
                               **kw, **_limits(limits))


def _blocks(raw) -> Tuple[CalcBlock, ...]:
    return tuple(CalcBlock(b["path"], b["line"], b["source"], tuple(CalcValue(**v) for v in b["results"])) for b in raw)


def compile(entry, fs, *, inputs: Optional[Mapping[str, Any]] = None, profile: str = "commonmark",
            calc_blocks: str = "remove", limits: Optional[Mapping[str, int]] = None) -> Result:
    """Compile the document `entry` (root-relative) read from `fs` into portable Markdown.

    `inputs` are host values visible as `variables.<name>` (the top metadata layer; numbers as int or
    Decimal, never float). `calc_blocks="remove"` leaves ```calc blocks out of the output, `"keep"`
    keeps their source; their results are always in `Result.calc`.
    """
    if calc_blocks not in ("remove", "keep"):
        raise ValueError("calc_blocks must be 'remove' or 'keep'")
    r = _run(entry, fs, inputs, limits, profile=profile, keep_calc=calc_blocks == "keep", markdown=True)
    return Result(r["metadata"], tuple(map(tuple, r["provenance"])), _blocks(r["calc"]), r["markdown"])


def evaluate(entry, fs, *, inputs: Optional[Mapping[str, Any]] = None,
             limits: Optional[Mapping[str, int]] = None) -> Evaluation:
    """Like `compile`, but returns only the data (metadata and calc results); no Markdown is produced."""
    r = _run(entry, fs, inputs, limits, markdown=False)
    return Evaluation(r["metadata"], tuple(map(tuple, r["provenance"])), _blocks(r["calc"]))
