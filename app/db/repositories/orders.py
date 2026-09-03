"""orders collection.

Customer-facing functions take `user_id` as a required, non-defaulted
first argument and filter on it here, in the repository — never in a
view. A tenancy bypass is a separately named `chef_*` function so the
intent is visible at the call site (02-ARCHITECTURE.md).
"""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from app.db.client import get_db
from app.db.repositories._common import parse_many, parse_one, to_object_id
from app.models.orders import TERMINAL_STATUSES, Order, OrderStatus

COLLECTION = "orders"


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
    result = get_db()[COLLECTION].insert_one(order.to_mongo())
    return order.model_copy(update={"id": str(result.inserted_id)})


# --- chef scope: deliberately not user-scoped -------------------------------


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
