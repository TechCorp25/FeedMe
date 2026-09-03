"""Helpers shared by repository modules.

Repositories are the only layer that touches PyMongo, and the only layer
that sees raw dicts. Everything they return is a Pydantic model.
"""

from __future__ import annotations

from typing import Any, TypeVar

from bson import ObjectId
from bson.errors import InvalidId
from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


def to_object_id(value: str) -> ObjectId | None:
    """Parse an id from the outside world. Returns None when malformed.

    A malformed id is treated as 'not found', never as an error: a view
    must not be able to distinguish the two.
    """
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def parse_one(model: type[M], document: dict[str, Any] | None) -> M | None:
    return model.model_validate(document) if document is not None else None


def parse_many(model: type[M], documents: Any) -> list[M]:
    return [model.model_validate(document) for document in documents]
