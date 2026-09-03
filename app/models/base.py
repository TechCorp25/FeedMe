"""Shared Pydantic base for documents that live in MongoDB.

`_id` is an ObjectId in the database and a plain string everywhere above the
repository layer. Models are the schema of record (02-ARCHITECTURE.md).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    """Timezone-aware UTC now. Never use naive datetimes in this codebase."""
    return datetime.now(timezone.utc)


class EmbeddedModel(BaseModel):
    """Base for sub-documents that live inside another document.

    Embedded models carry no `_id` and no timestamps of their own; they
    are versioned by the document that owns them.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        use_enum_values=False,
        str_strip_whitespace=True,
    )


class MongoModel(EmbeddedModel):
    """Base for any document parsed out of, or written into, MongoDB."""

    id: str | None = Field(default=None, alias="_id")

    @field_validator("id", mode="before")
    @classmethod
    def _objectid_to_str(cls, value: Any) -> Any:
        if isinstance(value, ObjectId):
            return str(value)
        return value

    def to_mongo(self, *, exclude_id: bool = True) -> dict[str, Any]:
        """Serialise for insertion. Enum members become their values."""
        data = self.model_dump(mode="python", by_alias=True, exclude_none=False)
        raw_id = data.pop("_id", None)
        if not exclude_id and raw_id is not None:
            data["_id"] = ObjectId(raw_id)
        return data


class TimestampedModel(MongoModel):
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
