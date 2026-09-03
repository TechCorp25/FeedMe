"""Filesystem storage backend."""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from app.storage.base import StorageBackend


class LocalStorage(StorageBackend):
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _resolve(self, path: str) -> Path:
        candidate = PurePosixPath(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"unsafe storage path: {path!r}")
        return self.root / candidate

    def save(self, path: str, stream: BinaryIO) -> str:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            shutil.copyfileobj(stream, handle)
        return path

    def open(self, path: str) -> BinaryIO:
        return self._resolve(path).open("rb")

    def delete(self, path: str) -> None:
        self._resolve(path).unlink(missing_ok=True)

    def exists(self, path: str) -> bool:
        return self._resolve(path).is_file()
