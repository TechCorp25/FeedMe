"""Storage interface.

Items store an `image_path`, never a URL, so the backend can change
without touching catalogue documents (01-DOMAIN.md).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import BinaryIO


class StorageBackend(ABC):
    @abstractmethod
    def save(self, path: str, stream: BinaryIO) -> str:
        """Persist `stream` at `path` and return the stored path."""

    @abstractmethod
    def open(self, path: str) -> BinaryIO:
        """Open a stored object for reading."""

    @abstractmethod
    def delete(self, path: str) -> None:
        """Remove a stored object. Missing objects are not an error."""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """True when an object is stored at `path`."""
