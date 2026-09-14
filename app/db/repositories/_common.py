"""Helpers shared by repository modules.

Repositories are the only layer that touches PyMongo, and the only layer
that sees raw dicts. Everything they return is a Pydantic model.
"""

from __future__ import annotations

from typing import Any, TypeVar

from bson import ObjectId
from bson.errors import InvalidId
from pydantic import BaseModel

from app.models.allergens import STORED_CONTEXT_KEY

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


#: Marks a document as one that came out of MongoDB rather than one the
#: application just built. A model may read a stored document more
#: leniently than it accepts a new one, where a rule was added after the
#: document was written — see `AllergenBlock._check_declaration`. It never
#: relaxes a write: nothing outside this module passes it.
_STORED = {STORED_CONTEXT_KEY: True}


def parse_one(model: type[M], document: dict[str, Any] | None) -> M | None:
    if document is None:
        return None
    return model.model_validate(document, context=_STORED)


def parse_many(model: type[M], documents: Any) -> list[M]:
    return [model.model_validate(document, context=_STORED) for document in documents]
