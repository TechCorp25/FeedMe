"""account_ledger collection.

Append-only. The running balance is computed by aggregation and is never
stored as a mutable field (01-DOMAIN.md).
"""

from __future__ import annotations

from pymongo import DESCENDING

from app.db.client import get_db
from app.db.repositories._common import parse_many
from app.models.orders import LedgerEntry, LedgerEntryType

COLLECTION = "account_ledger"

#: Both windowed reads sort on `created_at` and then on `_id`. Two entries
#: written in the same instant — a cancellation's credit landing beside
#: the charge it offsets, or two corrections typed in one go — carry the
#: same timestamp, and without a tiebreak the order they come back in is
#: whatever the storage engine happens to give. That decides which of
#: them a bounded window keeps, so the page could show a different pair
#: on two loads. `_id` is monotonic, so it orders them by when they were
#: actually written.


def list_recent_entries(user_id: str, limit: int = 100) -> list[LedgerEntry]:
    """The newest `limit` entries, returned oldest first.

    A ledger is read chronologically, so this reads back ascending — but
    sorting ascending *before* the limit, as this did, means an account
    past the limit is pinned to its oldest page and never shows the
    charge it took this morning. The window is therefore selected from
    the newest end and reversed for display; the balance that page opens
    on is the caller's to derive, because the aggregate over every entry
    is a separate read.
    """
    cursor = (
        get_db()[COLLECTION]
        .find({"user_id": user_id})
        .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
        .limit(limit)
    )
    return list(reversed(parse_many(LedgerEntry, cursor)))


def has_entry(user_id: str, order_id: str, entry_type: LedgerEntryType) -> bool:
    """Whether an entry of this kind already stands against this order.

    Checkout tolerates a charge that fails to reach the ledger — the
    order stands and the entry is repaired by hand. Cancellation must
    therefore ask before it credits: crediting an order that was never
    charged does not restore a zero balance, it invents one the other
    way, and the customer's page would say the kitchen owes them the
    whole order.
    """
    return (
        get_db()[COLLECTION].find_one(
            {
                "user_id": user_id,
                "order_id": order_id,
                "entry_type": entry_type.value,
            },
            projection={"_id": 1},
        )
        is not None
    )


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
    """Chef view of one customer's ledger, oldest first.

    Still filtered by `user_id` — the chef reads one customer at a time —
    but named `chef_*` because the caller is not the owning customer.

    Windowed from the newest end and reversed, for the same reason
    `list_recent_entries` is: sorting ascending *before* the limit pins a
    long-running account to its oldest page, so the chef looking up a
    customer to correct this morning's charge would be shown entries from
    the year they signed up. The opening balance the page counts from is
    the caller's to derive, because the aggregate is a separate read.
    """
    cursor = (
        get_db()[COLLECTION]
        .find({"user_id": user_id})
        .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
        .limit(limit)
    )
    return list(reversed(parse_many(LedgerEntry, cursor)))
