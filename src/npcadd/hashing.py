"""Deterministic hashing utilities.

Every hash in npcadd flows through this module so that the hashing convention is
defined in exactly one place. Two rules are enforced here:

1. File hashes are SHA256 over raw bytes, streamed, never over a textual
   representation (which would be platform-dependent via line endings).
2. Object hashes are SHA256 over a canonical JSON encoding: keys sorted,
   no insignificant whitespace, UTF-8, floats formatted with ``repr`` so that
   round-tripping is exact.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "canonical_json",
    "hash_bytes",
    "hash_file",
    "hash_many_files",
    "hash_obj",
    "short",
]

_CHUNK = 1 << 20  # 1 MiB


def hash_bytes(data: bytes) -> str:
    """Return the hex SHA256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def hash_file(path: str | Path) -> str:
    """Return the hex SHA256 digest of the file at ``path``, streamed.

    Raises:
        FileNotFoundError: if the path does not exist.
        IsADirectoryError: if the path is a directory.
    """
    p = Path(path)
    if p.is_dir():
        raise IsADirectoryError(f"Cannot hash a directory: {p}")
    digest = hashlib.sha256()
    with p.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def hash_many_files(paths: Iterable[str | Path]) -> dict[str, str]:
    """Map each existing path (as a POSIX string) to its SHA256.

    Missing paths are reported with the sentinel ``"<missing>"`` rather than
    raising, so that provenance capture never aborts a completed run. Paths are
    returned sorted for determinism.
    """
    out: dict[str, str] = {}
    for raw in paths:
        p = Path(raw)
        out[p.as_posix()] = hash_file(p) if p.is_file() else "<missing>"
    return dict(sorted(out.items()))


def canonical_json(obj: Any) -> str:
    """Encode ``obj`` as canonical JSON suitable for hashing.

    Sorted keys, compact separators, non-ASCII preserved as UTF-8. ``Path`` and
    ``set`` are supported because configs legitimately contain them.
    """

    def default(value: Any) -> Any:
        if isinstance(value, Path):
            return value.as_posix()
        if isinstance(value, (set, frozenset)):
            return sorted(value, key=repr)
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "model_dump"):  # pydantic BaseModel
            return value.model_dump(mode="json")
        raise TypeError(f"Object of type {type(value).__name__} is not JSON-serialisable")

    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=default,
        allow_nan=False,
    )


def hash_obj(obj: Any) -> str:
    """Return the hex SHA256 digest of the canonical JSON encoding of ``obj``."""
    return hash_bytes(canonical_json(obj).encode("utf-8"))


def short(digest: str, length: int = 12) -> str:
    """Truncate a digest for human-facing labels. Never used for equality checks."""
    if length <= 0:
        raise ValueError("length must be positive")
    return digest[:length]
