"""Order state machine."""

from __future__ import annotations

import pytest

from app.models.orders import Order, OrderStatus
from app.services.order_state import (
    ALLOWED_TRANSITIONS,
    TRANSITION_HOOKS,
    InvalidTransition,
    apply_transition,
)


def make_order(status: OrderStatus = OrderStatus.PLACED) -> Order:
    return Order(user_id="u1", reference="MP-2609-0001", status=status)


def advance(order: Order, target: OrderStatus, **kwargs) -> Order:
    kwargs.setdefault("by", "chef@example.com")
    kwargs.setdefault("actor_is_chef", True)
    return apply_transition(order, target, **kwargs)


def test_happy_path_to_collected():
    order = make_order()
    for target in (
        OrderStatus.CONFIRMED,
        OrderStatus.PREPPING,
        OrderStatus.READY,
        OrderStatus.COLLECTED,
    ):
        order = advance(order, target)
    assert order.status is OrderStatus.COLLECTED
    assert [entry.status for entry in order.status_history] == [
        OrderStatus.CONFIRMED,
        OrderStatus.PREPPING,
        OrderStatus.READY,
        OrderStatus.COLLECTED,
    ]


def test_ready_can_go_to_delivered():
    order = make_order(OrderStatus.READY)
    assert advance(order, OrderStatus.DELIVERED).status is OrderStatus.DELIVERED


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OrderStatus.PLACED, OrderStatus.READY),
        (OrderStatus.PLACED, OrderStatus.PREPPING),
        (OrderStatus.CONFIRMED, OrderStatus.COLLECTED),
        (OrderStatus.READY, OrderStatus.PLACED),
        (OrderStatus.COLLECTED, OrderStatus.CANCELLED),
        (OrderStatus.DELIVERED, OrderStatus.READY),
        (OrderStatus.CANCELLED, OrderStatus.CONFIRMED),
    ],
)
def test_transitions_outside_the_map_raise(current, target):
    with pytest.raises(InvalidTransition):
        advance(make_order(current), target)


def test_terminal_states_have_no_exits():
    for status in (OrderStatus.COLLECTED, OrderStatus.DELIVERED, OrderStatus.CANCELLED):
        assert ALLOWED_TRANSITIONS[status] == frozenset()
        assert make_order(status).is_terminal is True


def test_prepared_at_is_stamped_on_entry_to_ready():
    order = make_order(OrderStatus.PREPPING)
    assert order.prepared_at is None
    ready = advance(order, OrderStatus.READY)
    assert ready.prepared_at is not None

    # Re-entering ready is not possible, but the stamp is never overwritten.
    assert advance(ready, OrderStatus.DELIVERED).prepared_at == ready.prepared_at


def test_history_records_who_and_when():
    order = advance(make_order(), OrderStatus.CONFIRMED, by="chef@example.com")
    entry = order.status_history[-1]
    assert entry.by == "chef@example.com"
    assert entry.status is OrderStatus.CONFIRMED
    assert entry.at is not None


@pytest.mark.parametrize("current", [OrderStatus.PLACED, OrderStatus.CONFIRMED])
def test_customer_may_cancel_early(current):
    cancelled = apply_transition(
        make_order(current), OrderStatus.CANCELLED, by="u1", actor_is_chef=False
    )
    assert cancelled.status is OrderStatus.CANCELLED


@pytest.mark.parametrize("current", [OrderStatus.PREPPING, OrderStatus.READY])
def test_customer_cannot_cancel_once_prepping(current):
    with pytest.raises(InvalidTransition, match="customer cannot cancel"):
        apply_transition(
            make_order(current), OrderStatus.CANCELLED, by="u1", actor_is_chef=False
        )


@pytest.mark.parametrize("current", [OrderStatus.PREPPING, OrderStatus.READY])
def test_chef_cancellation_after_prepping_requires_a_note(current):
    with pytest.raises(InvalidTransition, match="requires a chef_note"):
        advance(make_order(current), OrderStatus.CANCELLED)

    cancelled = advance(
        make_order(current), OrderStatus.CANCELLED, chef_note="Out of stock"
    )
    assert cancelled.chef_note == "Out of stock"


def test_transition_does_not_mutate_the_original():
    order = make_order()
    advance(order, OrderStatus.CONFIRMED)
    assert order.status is OrderStatus.PLACED
    assert order.status_history == []


def test_a_hook_can_be_attached_without_restructuring():
    """v1 sends no notifications; the seam must already exist."""
    seen = []
    TRANSITION_HOOKS.append(lambda order, before, after: seen.append((before, after)))
    try:
        advance(make_order(), OrderStatus.CONFIRMED)
    finally:
        TRANSITION_HOOKS.clear()
    assert seen == [(OrderStatus.PLACED, OrderStatus.CONFIRMED)]
