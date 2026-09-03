"""Order state machine.

`status` is never assigned directly on a document. Every change goes
through `apply_transition`, which validates against the allowed map,
appends to `status_history` and stamps `prepared_at` on entry to
`ready` (04-WORKFLOWS.md).
"""

from __future__ import annotations

from collections.abc import Callable

from app.models.base import utcnow
from app.models.orders import Order, OrderStatus, StatusHistoryEntry

ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PLACED: frozenset({OrderStatus.CONFIRMED, OrderStatus.CANCELLED}),
    OrderStatus.CONFIRMED: frozenset({OrderStatus.PREPPING, OrderStatus.CANCELLED}),
    OrderStatus.PREPPING: frozenset({OrderStatus.READY, OrderStatus.CANCELLED}),
    OrderStatus.READY: frozenset(
        {OrderStatus.COLLECTED, OrderStatus.DELIVERED, OrderStatus.CANCELLED}
    ),
    OrderStatus.COLLECTED: frozenset(),
    OrderStatus.DELIVERED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}

#: A customer may cancel only from these states. Beyond them, chef only.
CUSTOMER_CANCELLABLE_FROM: frozenset[OrderStatus] = frozenset(
    {OrderStatus.PLACED, OrderStatus.CONFIRMED}
)

#: Cancelling from these states is chef-only and requires a chef_note.
CANCELLATION_REQUIRES_CHEF_NOTE: frozenset[OrderStatus] = frozenset(
    {OrderStatus.PREPPING, OrderStatus.READY}
)

#: Seam for a future notification hook. Nothing is registered in v1;
#: attaching one must not require restructuring the state machine.
TransitionHook = Callable[[Order, OrderStatus, OrderStatus], None]
TRANSITION_HOOKS: list[TransitionHook] = []


class InvalidTransition(Exception):
    """Raised for any transition not in ALLOWED_TRANSITIONS."""


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def apply_transition(
    order: Order,
    target: OrderStatus,
    *,
    by: str,
    actor_is_chef: bool,
    chef_note: str | None = None,
) -> Order:
    """Return a new Order at `target`. Never mutates the argument."""
    current = order.status

    if not can_transition(current, target):
        raise InvalidTransition(
            f"{current.value} -> {target.value} is not an allowed transition"
        )

    if target is OrderStatus.CANCELLED and not actor_is_chef:
        if current not in CUSTOMER_CANCELLABLE_FROM:
            raise InvalidTransition(
                f"a customer cannot cancel an order in {current.value}"
            )

    if target is OrderStatus.CANCELLED and current in CANCELLATION_REQUIRES_CHEF_NOTE:
        if not actor_is_chef:
            raise InvalidTransition(
                f"cancelling from {current.value} is chef-only"
            )
        if not (chef_note or "").strip():
            raise InvalidTransition(
                f"cancelling from {current.value} requires a chef_note"
            )

    now = utcnow()
    update: dict = {
        "status": target,
        "status_history": [
            *order.status_history,
            StatusHistoryEntry(status=target, at=now, by=by),
        ],
        "updated_at": now,
    }
    if chef_note:
        update["chef_note"] = chef_note
    if target is OrderStatus.READY and order.prepared_at is None:
        update["prepared_at"] = now

    updated = order.model_copy(update=update)
    for hook in TRANSITION_HOOKS:
        hook(updated, current, target)
    return updated
