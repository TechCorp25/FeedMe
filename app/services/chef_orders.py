"""The chef's order queue: what is outstanding, and what may happen to it.

Three rules shape this module, and they are the same three the customer's
account area keeps — read from the other side.

**A transition is decided, then written under the status it was decided
against.** `order_state.py` owns the allowed map; this module never
assigns `status`, and `orders_repo.chef_apply_transition` writes filtered
on the status the decision was made from. The chef advancing an order and
the customer cancelling it can both read `placed`, and only one of them
may win.

**The consolidated allergen summary is read from the snapshots, never
from the catalogue.** What the kitchen must cook around is what the
customer was told they were eating — the block frozen onto the line at
checkout. Re-reading the catalogue would show the chef a declaration that
has been edited since the order was placed, which is the one thing
snapshotting exists to prevent.

**Nothing here is a notification.** 04-WORKFLOWS.md keeps them out of
v1 and `order_state.TransitionHook` is the seam for later; this module
does not register one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from app.db.repositories import orders as orders_repo
from app.db.repositories import users as users_repo
from app.models.allergens import (
    ALLERGEN_LABELS,
    AllergenCode,
    GlutenCereal,
    TreeNutSpecies,
    declaration_labels,
)
from app.models.orders import Order, OrderStatus, PaymentStatus
from app.models.users import User
from app.services import order_state
from app.services.account import append_cancellation_credit

logger = logging.getLogger(__name__)

#: The verb on each transition control. A button that reads "Ready" leaves
#: the chef to work out whether it describes the order now or after the
#: click; a verb says which. 03-FRONTEND.md forbids a distinction carried
#: by colour alone, and this is the same rule applied to wording.
TRANSITION_LABELS: dict[OrderStatus, str] = {
    OrderStatus.CONFIRMED: "Confirm",
    OrderStatus.PREPPING: "Start prepping",
    OrderStatus.READY: "Mark ready",
    OrderStatus.COLLECTED: "Mark collected",
    OrderStatus.DELIVERED: "Mark delivered",
    OrderStatus.CANCELLED: "Cancel",
}

#: Every value stays selectable, `unpaid` included. 04-WORKFLOWS.md has
#: the chef setting `settled` or `waived`, which is what they do in the
#: ordinary case — but `payment_status` is a mutable tracking field, not
#: an append-only record, and a settlement recorded by mistake has to be
#: undoable. Restricting the control to the two forward values would make
#: a mis-click permanent with no other way back. The form says what
#: choosing `unpaid` means instead of hiding it.
PAYMENT_STATUS_LABELS: dict[PaymentStatus, str] = {
    PaymentStatus.UNPAID: "Unpaid",
    PaymentStatus.SETTLED: "Settled",
    PaymentStatus.WAIVED: "Waived",
}

#: How many orders one queue page renders. A single kitchen's outstanding
#: work, not an export.
QUEUE_LIMIT = 200


class QueueActionError(ValueError):
    """An action that will not happen, with the wording to show the chef."""


# --- the consolidated declaration -------------------------------------------


@dataclass(frozen=True)
class OrderAllergenSummary:
    """Every allergen an order carries, rolled up across its lines.

    Advisory in the same sense `allergen_rollup.py` is: it summarises
    declarations that already exist and invents none. It is what the chef
    reads before touching a bench — one list per order rather than one
    per line — and the per-line declarations are still rendered beneath
    it, because the summary says what is in the order and not which dish
    it is in.
    """

    contains_labels: list[str]
    may_contain_labels: list[str]
    #: True when a line on this order was snapshotted before any review.
    #: Impossible through checkout, which cannot sell an unreviewed item,
    #: and therefore worth saying out loud rather than rendering as
    #: "no allergens" if it ever appears.
    has_unreviewed_line: bool = False

    @property
    def declares_nothing(self) -> bool:
        return not self.contains_labels and not self.may_contain_labels


def _ordered(codes: set[AllergenCode]) -> list[AllergenCode]:
    """Declaration order, fixed by the enum rather than by insertion.

    Two orders carrying the same allergens must read identically, so the
    order cannot depend on which line happened to be first.
    """
    return [code for code in AllergenCode if code in codes]


def summarise_allergens(order: Order) -> OrderAllergenSummary:
    """Roll an order's line snapshots up into one declaration."""
    contains: set[AllergenCode] = set()
    may_contain: set[AllergenCode] = set()
    cereals: set[GlutenCereal] = set()
    species: set[TreeNutSpecies] = set()
    unreviewed = False

    for line in order.lines:
        block = line.allergen_snapshot
        if not block.is_reviewed:
            unreviewed = True
        contains.update(block.contains)
        may_contain.update(block.may_contain)
        cereals.update(block.gluten_cereals)
        species.update(block.tree_nut_species)

    # A declared allergen is not simultaneously a cross-contact risk. One
    # line declaring peanut and another flagging it as possible makes the
    # order a peanut order; listing it twice would read as a distinction
    # the kitchen has to act on and there is none.
    may_contain -= contains

    return OrderAllergenSummary(
        contains_labels=declaration_labels(
            _ordered(contains),
            gluten_cereals=[cereal for cereal in GlutenCereal if cereal in cereals],
            tree_nut_species=[nut for nut in TreeNutSpecies if nut in species],
        ),
        may_contain_labels=[
            ALLERGEN_LABELS[code] for code in _ordered(may_contain)
        ],
        has_unreviewed_line=unreviewed,
    )


# --- the queue --------------------------------------------------------------


@dataclass(frozen=True)
class TransitionChoice:
    """One move the chef may make on one order, ready to render."""

    target: OrderStatus
    label: str
    requires_note: bool

    @property
    def is_cancellation(self) -> bool:
        return self.target is OrderStatus.CANCELLED


def transition_choices(order: Order) -> list[TransitionChoice]:
    """The moves allowed from where this order stands, in map order.

    Derived from `order_state.ALLOWED_TRANSITIONS` rather than listed
    here: a queue that offered a move the state machine refuses would
    hand the chef a button whose only outcome is an error.
    """
    allowed = order_state.ALLOWED_TRANSITIONS[order.status]
    return [
        TransitionChoice(
            target=target,
            label=TRANSITION_LABELS[target],
            requires_note=(
                target is OrderStatus.CANCELLED
                and order.status in order_state.CANCELLATION_REQUIRES_CHEF_NOTE
            ),
        )
        for target in OrderStatus
        if target in allowed
    ]


@dataclass(frozen=True)
class QueueEntry:
    """One order as the queue renders it."""

    order: Order
    customer: User | None
    allergens: OrderAllergenSummary
    transitions: list[TransitionChoice]

    @property
    def item_count(self) -> int:
        """Quantities summed, so one line of three reads as three items."""
        return sum(line.quantity for line in self.order.lines)

    @property
    def customer_name(self) -> str:
        if self.customer is None:
            return "Customer no longer on file"
        return self.customer.display_name or self.customer.email

    @property
    def dietary_notes(self) -> str | None:
        return (self.customer.dietary_notes or None) if self.customer else None

    @property
    def delivery_address(self) -> str | None:
        """The address, only where the order is actually being delivered.

        01-DOMAIN.md keeps the address on the customer rather than on the
        order, so this reads the current one — and reads it only for a
        delivery, because a collection order has no business rendering
        somebody's home address on a kitchen screen.
        """
        if self.order.fulfilment.value != "delivery" or self.customer is None:
            return None
        return self.customer.delivery_address or None


@dataclass(frozen=True)
class QueueFilters:
    """What the queue was asked for. Parsed, never a raw request."""

    status: OrderStatus | None = None
    requested_for: date | None = None

    @property
    def is_active(self) -> bool:
        return self.status is not None or self.requested_for is not None


@dataclass(frozen=True)
class QueueView:
    entries: list[QueueEntry]
    filters: QueueFilters
    is_truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.entries


def parse_filters(status: str | None, requested_for: str | None) -> QueueFilters:
    """Read the filter strip's values. An unrecognised one is ignored.

    Deliberately forgiving: a stale bookmark or a hand-typed date should
    show the default queue, not a 400. The alternative is a working list
    that refuses to render because of a query string.
    """
    parsed_status: OrderStatus | None = None
    if status:
        try:
            parsed_status = OrderStatus(status)
        except ValueError:
            parsed_status = None

    parsed_date: date | None = None
    if requested_for:
        try:
            parsed_date = date.fromisoformat(requested_for.strip())
        except ValueError:
            parsed_date = None

    return QueueFilters(status=parsed_status, requested_for=parsed_date)


def queue_view(filters: QueueFilters) -> QueueView:
    """The queue, with every order's customer and declaration attached."""
    # One more than the page shows: the extra document is how the page
    # knows it truncated, and asking for it is cheaper than reading the
    # whole match to count.
    orders = orders_repo.chef_list_order_queue(
        filters.status,
        requested_for=filters.requested_for,
        limit=QUEUE_LIMIT + 1,
    )
    truncated = len(orders) > QUEUE_LIMIT
    orders = orders[:QUEUE_LIMIT]

    customers = users_repo.chef_list_customers_by_ids(
        [order.user_id for order in orders]
    )
    entries = [
        QueueEntry(
            order=order,
            customer=customers.get(order.user_id),
            allergens=summarise_allergens(order),
            transitions=transition_choices(order),
        )
        for order in orders
    ]
    return QueueView(entries=entries, filters=filters, is_truncated=truncated)


# --- the two writes ---------------------------------------------------------


def apply_transition(
    chef: User, order: Order, target: str | None, chef_note: str | None
) -> Order:
    """Move one order along the allowed map, or raise `QueueActionError`.

    The order is read inside the request and the write is filtered on the
    status that read returned, so a double-submitted form moves the order
    once — and credits a cancellation once.
    """
    if chef.id is None:
        raise QueueActionError("Sign in again to act on an order.")

    try:
        parsed = OrderStatus(target or "")
    except ValueError as exc:
        raise QueueActionError("That is not a status an order can move to.") from exc

    current = order.status
    try:
        moved = order_state.apply_transition(
            order,
            parsed,
            by=chef.id,
            actor_is_chef=True,
            chef_note=(chef_note or "").strip() or None,
        )
    except order_state.InvalidTransition as exc:
        # The state machine's wording names the rule it enforced, and the
        # chef is the person who can act on it — unlike a customer, who
        # gets a sentence about contacting the kitchen.
        raise QueueActionError(str(exc)) from exc

    if not orders_repo.chef_apply_transition(moved, expected_status=current):
        raise QueueActionError(
            f"Order {order.reference} moved on while the queue was open. "
            "It has been left alone; reload and try again."
        )

    if parsed is OrderStatus.CANCELLED:
        # The same offsetting entry the customer's own cancellation
        # writes, and written after the transition for the same reason:
        # a credit that stood alone would tell a customer the kitchen
        # owed them for an order still being prepared.
        append_cancellation_credit(moved, created_by=chef.id)

    return moved


def set_payment_status(order: Order, payment_status: str | None) -> PaymentStatus:
    """Record settlement by hand. No provider is contacted, ever.

    04-WORKFLOWS.md has the chef setting `settled` or `waived` and moving
    `payment_status` independently of `status`. The application prices
    and tracks; it captures nothing (00-SYSTEM.md).
    """
    try:
        parsed = PaymentStatus(payment_status or "")
    except ValueError as exc:
        raise QueueActionError("That is not a payment status.") from exc

    if order.id is None or not orders_repo.chef_set_payment_status(order.id, parsed):
        raise QueueActionError(
            f"Order {order.reference} could not be updated. Reload and try again."
        )
    return parsed
