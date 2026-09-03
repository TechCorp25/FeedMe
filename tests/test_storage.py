"""Local storage backend. Items store a path, never a URL."""

from __future__ import annotations

import io

import pytest

from app.storage.local import LocalStorage


@pytest.fixture()
def storage(tmp_path) -> LocalStorage:
    return LocalStorage(tmp_path)


def test_round_trip(storage):
    path = storage.save("items/harissa.jpg", io.BytesIO(b"jpeg-bytes"))
    assert path == "items/harissa.jpg"
    assert storage.exists(path) is True
    with storage.open(path) as handle:
        assert handle.read() == b"jpeg-bytes"


def test_delete_is_forgiving(storage):
    storage.delete("items/never-existed.jpg")
    assert storage.exists("items/never-existed.jpg") is False


@pytest.mark.parametrize("path", ["../escape.jpg", "items/../../escape.jpg", "/etc/passwd"])
def test_paths_cannot_escape_the_root(storage, path):
    with pytest.raises(ValueError, match="unsafe storage path"):
        storage.save(path, io.BytesIO(b""))
