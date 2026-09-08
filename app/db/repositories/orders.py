"""orders collection.

Customer-facing functions take `user_id` as a required, non-defaulted
first argument and filter on it here, in the repository — never in a
view. A tenancy bypass is a separately named `chef_*` function so the
intent is visible at the call site (02-ARCHITECTURE.md).
"""

from __future__ import annotations

import re

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from app.db.client import get_db
from app.db.repositories._common import parse_many, parse_one, to_object_id
from app.models.orders import TERMINAL_STATUSES, Order, OrderStatus

COLLECTION = "orders"


class ReferenceTaken(ValueError):
    """Raised when the unique index on `reference` refuses an insert.

    Two orders placed in the same second would otherwise be handed the
    same number. The index is what decides; the caller draws the next
    reference and tries again.
    """


def get_order(user_id: str, order_id: str) -> Order | None:
    object_id = to_object_id(order_id)
    if object_id is None:
        return None
    return parse_one(
        Order, get_db()[COLLECTION].find_one({"_id": object_id, "user_id": user_id})
    )


def get_order_by_reference(user_id: str, reference: str) -> Order | None:
    return parse_one(
        Order,
        get_db()[COLLECTION].find_one({"reference": reference, "user_id": user_id}),
    )


def list_orders(user_id: str, limit: int = 20) -> list[Order]:
    cursor = (
        get_db()[COLLECTION]
        .find({"user_id": user_id})
        .sort("created_at", DESCENDING)
        .limit(limit)
    )
    return parse_many(Order, cursor)


def create_order(user_id: str, order: Order) -> Order:
    if order.user_id != user_id:
        raise ValueError("order.user_id does not match the scoping user_id")
    try:
        result = get_db()[COLLECTION].insert_one(order.to_mongo())
    except DuplicateKeyError as exc:
        raise ReferenceTaken(order.reference) from exc
    return order.model_copy(update={"id": str(result.inserted_id)})


def system_highest_reference(prefix: str) -> str | None:
    """The largest reference already issued this month, or None.

    `system_*`, like `chef_*`, is a deliberately named scope outside the
    customer contract — the reference counter is drawn across every
    customer's orders, so it cannot take a `user_id` and must not look as
    though it forgot one. It returns a single reference string and never
    a document, so no customer data leaves the collection through it, and
    the unique index rather than this read is what settles a collision.
    """
    document = get_db()[COLLECTION].find_one(
        {"reference": {"$regex": f"^{re.escape(prefix)}"}},
        sort=[("reference", DESCENDING)],
        projection={"reference": 1},
    )
    return document["reference"] if document else None


def apply_transition(
    user_id: str,
    order: Order,
    *,
    expected_status: OrderStatus,
) -> bool:
    """Write a transition back to one order. True when it was applied.

    The update the customer account area owes: `services/order_state.py`
    decides what a transition may be and returns the new `Order`; this
    writes it, and nothing else. Only the fields a transition touches are
    set, so a concurrent write to an untouched field is not clobbered by
    a whole-document replace.

    `expected_status` is part of the filter, not an assertion made
    beforehand. A cancel confirmed twice, or a customer cancelling while
    the chef moves the same order on, would otherwise both read `placed`,
    both decide the transition is allowed, and both write — appending two
    status-history entries and, at the caller, two offsetting ledger
    credits. The second update matches nothing and returns False, so the
    caller knows it did not happen.
    """
    if order.id is None:
        raise ValueError("cannot write a transition to an unsaved order")
    if order.user_id != user_id:
        raise ValueError("order.user_id does not match the scoping user_id")

    object_id = to_object_id(order.id)
    if object_id is None:
        return False

    document = order.to_mongo()
    update = {
        field: document[field]
        for field in ("status", "status_history", "updated_at", "prepared_at")
    }
    if order.chef_note is not None:
        update["chef_note"] = document["chef_note"]

    result = get_db()[COLLECTION].update_one(
        {
            "_id": object_id,
            "user_id": user_id,
            "status": expected_status.value,
        },
        {"$set": update},
    )
    return result.matched_count == 1


# --- system and chef scope: deliberately not user-scoped --------------------


def chef_get_order(order_id: str) -> Order | None:
    object_id = to_object_id(order_id)
    if object_id is None:
        return None
    return parse_one(Order, get_db()[COLLECTION].find_one({"_id": object_id}))


def chef_list_order_queue(status: OrderStatus | None = None) -> list[Order]:
    """Non-terminal orders by requested date ascending, unless filtered."""
    query = (
        {"status": status.value}
        if status is not None
        else {"status": {"$nin": [s.value for s in TERMINAL_STATUSES]}}
    )
    return parse_many(
        Order, get_db()[COLLECTION].find(query).sort("requested_for", ASCENDING)
    )
