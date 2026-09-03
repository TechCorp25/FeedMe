"""Idempotent index bootstrap.

Every index this application relies on is declared here and applied at
startup. Indexes are never created ad hoc at a call site (00-SYSTEM.md).
"""

from __future__ import annotations

import logging

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

logger = logging.getLogger(__name__)

#: collection name -> list of (keys, options)
INDEX_SPECS: dict[str, list[tuple[list[tuple[str, int]], dict]]] = {
    "users": [
        ([("email", ASCENDING)], {"name": "email_unique", "unique": True}),
    ],
    "components": [
        ([("slug", ASCENDING)], {"name": "slug_unique", "unique": True}),
        ([("category", ASCENDING)], {"name": "category"}),
        (
            [("is_archived", ASCENDING), ("is_available", ASCENDING)],
            {"name": "archived_available"},
        ),
        ([("preference_flags", ASCENDING)], {"name": "preference_flags"}),
    ],
    "dishes": [
        ([("slug", ASCENDING)], {"name": "slug_unique", "unique": True}),
        ([("meal_type_ids", ASCENDING)], {"name": "meal_type_ids"}),
        (
            [("is_archived", ASCENDING), ("is_available", ASCENDING)],
            {"name": "archived_available"},
        ),
        ([("preference_flags", ASCENDING)], {"name": "preference_flags"}),
    ],
    "orders": [
        (
            [("user_id", ASCENDING), ("created_at", DESCENDING)],
            {"name": "user_created_desc"},
        ),
        ([("status", ASCENDING)], {"name": "status"}),
        ([("reference", ASCENDING)], {"name": "reference_unique", "unique": True}),
    ],
    "account_ledger": [
        (
            [("user_id", ASCENDING), ("created_at", ASCENDING)],
            {"name": "user_created"},
        ),
    ],
    "meal_types": [
        ([("slug", ASCENDING)], {"name": "slug_unique", "unique": True}),
    ],
}


def ensure_indexes(db: Database) -> None:
    """Apply every declared index. Safe to run on every boot."""
    for collection_name, specs in INDEX_SPECS.items():
        for keys, options in specs:
            db[collection_name].create_index(keys, **options)
    logger.info(
        "index bootstrap complete", extra={"collections": sorted(INDEX_SPECS)}
    )
