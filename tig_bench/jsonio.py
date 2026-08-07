"""Canonical JSON and atomic file helpers."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterator


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes, *, require_same: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if require_same and path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"Refusing to replace different existing file: {path}")
        return

    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary_file:
        temporary_file.write(data)
        temporary_file.flush()
        os.fsync(temporary_file.fileno())
        temporary_path = Path(temporary_file.name)
    os.replace(temporary_path, path)


@contextmanager
def atomic_directory(path: Path) -> Iterator[Path]:
    """Populate a sibling staging directory and install it as one rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    )
    installed = False
    try:
        yield staging
        staging.rename(path)
        installed = True
    finally:
        if not installed and staging.exists():
            shutil.rmtree(staging)
