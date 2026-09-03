"""account_ledger collection.

Append-only. The running balance is computed by aggregation and is never
stored as a mutable field (01-DOMAIN.md).
"""

from __future__ import annotations

from pymongo import ASCENDING

from app.db.client import get_db
from app.db.repositories._common import parse_many
from app.models.orders import LedgerEntry

COLLECTION = "account_ledger"


def list_entries(user_id: str, limit: int = 100) -> list[LedgerEntry]:
    cursor = (
        get_db()[COLLECTION]
        .find({"user_id": user_id})
        .sort("created_at", ASCENDING)
        .limit(limit)
    )
    return parse_many(LedgerEntry, cursor)


def balance_cents(user_id: str) -> int:
    """Signed running balance, summed by the database."""
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": None, "total": {"$sum": "$amount_cents"}}},
    ]
    result = list(get_db()[COLLECTION].aggregate(pipeline))
    return int(result[0]["total"]) if result else 0


def append_entry(user_id: str, entry: LedgerEntry) -> LedgerEntry:
    if entry.user_id != user_id:
        raise ValueError("entry.user_id does not match the scoping user_id")
    result = get_db()[COLLECTION].insert_one(entry.to_mongo())
    return entry.model_copy(update={"id": str(result.inserted_id)})


# --- chef scope: deliberately not user-scoped -------------------------------


def chef_list_entries(user_id: str, limit: int = 500) -> list[LedgerEntry]:
    """Chef view of one customer's ledger.

    Still filtered by `user_id` — the chef reads one customer at a time —
    but named `chef_*` because the caller is not the owning customer.
    """
    cursor = (
        get_db()[COLLECTION]
        .find({"user_id": user_id})
        .sort("created_at", ASCENDING)
        .limit(limit)
    )
    return parse_many(LedgerEntry, cursor)
