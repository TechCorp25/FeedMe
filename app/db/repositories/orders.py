"""orders collection.

Customer-facing functions take `user_id` as a required, non-defaulted
first argument and filter on it here, in the repository — never in a
view. A tenancy bypass is a separately named `chef_*` function so the
intent is visible at the call site (02-ARCHITECTURE.md).
"""

from __future__ import annotations

import re
from datetime import date

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from app.db.client import get_db
from app.db.repositories._common import parse_many, parse_one, to_object_id
from app.models.base import as_mongo_date, utcnow
from app.models.orders import (
    TERMINAL_STATUSES,
    Order,
    OrderStatus,
    PaymentStatus,
)

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

    result = get_db()[COLLECTION].update_one(
        {
            "_id": object_id,
            "user_id": user_id,
            "status": expected_status.value,
        },
        {"$set": _transition_update(order)},
    )
    return result.matched_count == 1


def _transition_update(order: Order) -> dict:
    """The fields a transition touches, and only those.

    A whole-document replace would clobber a concurrent write to a field
    the transition has nothing to do with — the chef's `payment_status`,
    say, which 04-WORKFLOWS.md moves independently of `status`.
    """
    document = order.to_mongo()
    update = {
        field: document[field]
        for field in ("status", "status_history", "updated_at", "prepared_at")
    }
    if order.chef_note is not None:
        update["chef_note"] = document["chef_note"]
    return update


# --- system and chef scope: deliberately not user-scoped --------------------


def chef_get_order(order_id: str) -> Order | None:
    object_id = to_object_id(order_id)
    if object_id is None:
        return None
    return parse_one(Order, get_db()[COLLECTION].find_one({"_id": object_id}))


def chef_list_order_queue(
    status: OrderStatus | None = None,
    *,
    requested_for: date | None = None,
    limit: int | None = None,
) -> list[Order]:
    """Non-terminal orders by requested date ascending, unless filtered.

    Naming a `status` widens the read to that status whether or not it is
    terminal — the chef filtering for `collected` is asking for finished
    orders, and answering with none would be a filter that lies.

    `requested_for` is matched through `as_mongo_date`, the same function
    that wrote it: the field is a domain `date` stored as UTC midnight,
    and a query that built its own instant would agree with the write
    only by coincidence.
    """
    query: dict = (
        {"status": status.value}
        if status is not None
        else {"status": {"$nin": [s.value for s in TERMINAL_STATUSES]}}
    )
    if requested_for is not None:
        query["requested_for"] = as_mongo_date(requested_for)

    cursor = (
        get_db()[COLLECTION]
        .find(query)
        .sort([("requested_for", ASCENDING), ("reference", ASCENDING)])
    )
    if limit is not None:
        # Bounded by the database, not by the caller slicing what it was
        # already sent. A status filter naming a terminal state can match
        # years of history, and every one of those documents would
        # otherwise be sorted, transferred and parsed into a model —
        # nested lines and allergen snapshots included — only to be
        # dropped. The caller asks for one more than it will show and
        # uses the extra to know it truncated.
        cursor = cursor.limit(limit)
    return parse_many(Order, cursor)


def chef_apply_transition(order: Order, *, expected_status: OrderStatus) -> bool:
    """Write a transition the chef decided. True when it was applied.

    The chef's mirror of `apply_transition`, and a separately named
    function rather than an optional `user_id`, because the widened scope
    has to be visible at the call site (02-ARCHITECTURE.md).

    `expected_status` is in the filter for the same reason it is there
    for a customer: the chef advancing an order and the customer
    cancelling it can both read `placed`, and only one of them may win.
    """
    if order.id is None:
        raise ValueError("cannot write a transition to an unsaved order")

    object_id = to_object_id(order.id)
    if object_id is None:
        return False

    result = get_db()[COLLECTION].update_one(
        {"_id": object_id, "status": expected_status.value},
        {"$set": _transition_update(order)},
    )
    return result.matched_count == 1


def chef_set_payment_status(order_id: str, payment_status: PaymentStatus) -> bool:
    """Record that the chef settled or waived an order. True when written.

    `payment_status` moves independently of `status` (04-WORKFLOWS.md), so
    this is its own write and carries no expected status: an order can be
    settled while it is placed, prepping or long since collected. No money
    moves — the application prices and tracks, and captures nothing.
    """
    object_id = to_object_id(order_id)
    if object_id is None:
        return False
    result = get_db()[COLLECTION].update_one(
        {"_id": object_id},
        {"$set": {"payment_status": payment_status.value, "updated_at": utcnow()}},
    )
    return result.matched_count == 1
