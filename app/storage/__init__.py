"""Storage backends, and the one way to reach the configured one.

`STORAGE_BACKEND` names the implementation and `app.storage.get_storage()`
builds it. Nothing above this package constructs a backend, and nothing
anywhere builds a filesystem path or a bucket URL of its own: an item
stores an `image_path`, which is "a storage-interface path, not a URL"
(01-DOMAIN.md), and a template that turns one into a path on disk is the
defect this interface exists to prevent.

The backend is cached on `app.extensions` rather than built per call, so
one process holds one object and a test can replace it wholesale.
"""

from __future__ import annotations

from flask import Flask, current_app

from app.storage.base import StorageBackend
from app.storage.local import LocalStorage

__all__ = ["StorageBackend", "LocalStorage", "get_storage", "set_storage"]

_EXTENSION_KEY = "feedme_storage"


class UnknownBackend(RuntimeError):
    """Raised when `STORAGE_BACKEND` names an implementation we do not have.

    Loudly, at first use. A configuration typo that silently fell back to
    the filesystem would write uploads onto a container's ephemeral disk
    and lose them at the next deploy, which is worse than not starting.
    """


def _build(app: Flask) -> StorageBackend:
    name = (app.config.get("STORAGE_BACKEND") or "local").strip().lower()
    if name == "local":
        return LocalStorage(app.config["STORAGE_LOCAL_PATH"])
    raise UnknownBackend(
        f"STORAGE_BACKEND is {name!r}; this build has 'local' only"
    )


def get_storage(app: Flask | None = None) -> StorageBackend:
    """The configured backend for this application."""
    target = app or current_app
    backend = target.extensions.get(_EXTENSION_KEY)
    if backend is None:
        backend = _build(target)
        target.extensions[_EXTENSION_KEY] = backend
    return backend


def set_storage(app: Flask, backend: StorageBackend) -> None:
    """Install a backend explicitly. For tests and for a future migration."""
    app.extensions[_EXTENSION_KEY] = backend
