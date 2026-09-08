"""Placing an order: the one moment the catalogue stops being live.

Until here a cart is ids and quantities, and every price and allergen
declaration is read from the catalogue as it stands (`services/cart.py`).
At confirm, 04-WORKFLOWS.md fixes what happens: name, unit price and the
full allergen block are snapshotted onto each line, the totals are
computed in integer minor units, a `MP-YYMM-NNNN` reference is generated,
the order is written at `placed` / `unpaid`, a `charge` goes to the
ledger and the cart is cleared. No payment is taken and no provider is
contacted.

**On "atomically".** The order is one document, and its insert is one
atomic write: an order never exists half-snapshotted or half-priced. The
ledger entry is a second document in a second collection, and a genuine
two-collection transaction needs a replica set — which a workstation, a
single-node deployment and the test suite's mongomock do not have, so a
`with_transaction` here would fail everywhere except a cluster. The order
is therefore written first and the ledger entry second, carrying
`order_id`: the failure that remains is a charge missing from a ledger,
which is visible (every order's charge can be found by its `order_id`),
recoverable by appending the entry, and never a customer charged for an
order that does not exist. It is logged at ERROR when it happens.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from app.db.repositories import ledger as ledger_repo
from app.db.repositories import orders as orders_repo
from app.db.repositories import users as users_repo
from app.models.base import utcnow
from app.models.orders import (
    Fulfilment,
    LedgerEntry,
    LedgerEntryType,
    Order,
    OrderLine,
    OrderStatus,
    StatusHistoryEntry,
)
from app.models.users import User
from app.services import pricing
from app.services.cart import CartView

logger = logging.getLogger(__name__)

#: How far ahead a kitchen will take a date. Not a business rule handed
#: down by the domain docs — a bound, so a typo'd year is refused at the
#: form rather than sitting in the queue for three centuries.
MAX_LEAD_DAYS = 90

MAX_NOTE_LENGTH = 500
MAX_ADDRESS_LENGTH = 500

#: Reference numbers are drawn from the collection and settled by its
#: unique index, so a collision is retried rather than prevented.
REFERENCE_PREFIX = "MP"
REFERENCE_ATTEMPTS = 5


class CheckoutError(ValueError):
    """A refusal the customer can act on, with the wording to show them."""


@dataclass(frozen=True)
class CheckoutRequest:
    """What the customer chose, already parsed and bounded."""

    requested_for: date
    fulfilment: Fulfilment
    customer_note: str | None
    delivery_address: str | None


def parse_requested_for(raw: str | None, *, today: date) -> date:
    """The date on the form, or a refusal that says what was wrong."""
    if not (raw or "").strip():
        raise CheckoutError("Choose the date you would like this for.")
    try:
        requested = date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise CheckoutError("Choose the date you would like this for.") from exc
    if requested < today:
        raise CheckoutError("Choose a date that has not already passed.")
    if requested > today + timedelta(days=MAX_LEAD_DAYS):
        raise CheckoutError(
            f"Choose a date within the next {MAX_LEAD_DAYS} days."
        )
    return requested


def parse_fulfilment(raw: str | None) -> Fulfilment:
    """Collection or delivery. An unrecognised value is refused, not defaulted.

    Defaulting would decide for the customer whether they are coming to
    collect their food.
    """
    try:
        return Fulfilment(str(raw or "").strip())
    except ValueError as exc:
        raise CheckoutError("Choose collection or delivery.") from exc


def parse_checkout_form(form, *, user: User, today: date) -> CheckoutRequest:
    """Read the whole form, or raise the first refusal it earns."""
    fulfilment = parse_fulfilment(form.get("fulfilment"))
    requested_for = parse_requested_for(form.get("requested_for"), today=today)

    note = (form.get("customer_note") or "").strip()
    if len(note) > MAX_NOTE_LENGTH:
        raise CheckoutError(
            f"Keep your note to {MAX_NOTE_LENGTH} characters or fewer."
        )

    address = (form.get("delivery_address") or "").strip()
    if fulfilment is Fulfilment.DELIVERY:
        address = address or (user.delivery_address or "").strip()
        if not address:
            raise CheckoutError("Add the address this should be delivered to.")
        if len(address) > MAX_ADDRESS_LENGTH:
            raise CheckoutError(
                f"Keep the address to {MAX_ADDRESS_LENGTH} characters or fewer."
            )

    return CheckoutRequest(
        requested_for=requested_for,
        fulfilment=fulfilment,
        customer_note=note or None,
        delivery_address=address or None,
    )


def snapshot_lines(view: CartView) -> list[OrderLine]:
    """Freeze the cart against the catalogue as it stands right now.

    The name, the unit price and the entire allergen block are copied
    onto the line. A later catalogue edit must never change what a
    customer was told they were eating (01-DOMAIN.md), so nothing on an
    order line is a reference back into the catalogue except `item_id`,
    which identifies the item and is never read for display.
    """
    lines: list[OrderLine] = []
    for entry in view.entries:
        if entry.item is None or not entry.is_available:
            # `place_order` refuses a blocked cart before reaching here.
            raise CheckoutError(
                "Remove the items that are no longer available before "
                "placing your order."
            )
        lines.append(
            OrderLine(
                item_type=entry.item_type,
                item_id=entry.item_id,
                name_snapshot=entry.item.name,
                unit_price_cents=entry.item.price_cents,
                quantity=entry.quantity,
                line_total_cents=pricing.line_total_cents(
                    entry.item.price_cents, entry.quantity
                ),
                allergen_snapshot=entry.item.allergens.model_copy(deep=True),
            )
        )
    return lines


def reference_prefix_for(moment: date) -> str:
    """`MP-YYMM-` — the part of a reference the month decides."""
    return f"{REFERENCE_PREFIX}-{moment:%y%m}-"


def next_reference(moment: date) -> str:
    """The next `MP-YYMM-NNNN` for this month.

    The counter restarts each month and is read from the highest
    reference already issued, so it stays right without a counter
    document — 01-DOMAIN.md names six collections and a sequence store
    is not one of them. A reference that loses a race is refused by the
    unique index and drawn again.
    """
    prefix = reference_prefix_for(moment)
    highest = orders_repo.system_highest_reference(prefix)
    sequence = 0
    if highest is not None:
        tail = highest[len(prefix):]
        if tail.isdigit():
            sequence = int(tail)
    return f"{prefix}{sequence + 1:04d}"


def place_order(user: User, view: CartView, request: CheckoutRequest) -> Order:
    """Write the order, then its ledger charge. Never mutates the cart.

    Clearing the cart is the caller's, because the cart lives in the
    session and this service does not touch one.
    """
    if user.id is None:
        raise CheckoutError("Sign in to place an order.")
    if view.is_empty:
        raise CheckoutError("Your cart is empty.")
    if view.is_blocked:
        raise CheckoutError(
            "Remove the items that are no longer available before placing "
            "your order."
        )

    lines = snapshot_lines(view)
    now = utcnow()
    order = Order(
        user_id=user.id,
        reference="",  # replaced per attempt below
        status=OrderStatus.PLACED,
        lines=lines,
        subtotal_cents=pricing.subtotal_cents(lines),
        total_cents=pricing.total_cents(lines),
        requested_for=request.requested_for,
        fulfilment=request.fulfilment,
        customer_note=request.customer_note,
        status_history=[
            StatusHistoryEntry(status=OrderStatus.PLACED, at=now, by=user.id)
        ],
        created_at=now,
        updated_at=now,
    )

    placed = _create_with_reference(user.id, order, now.date())

    if request.fulfilment is Fulfilment.DELIVERY and request.delivery_address:
        # The address is the customer's, not the order's (01-DOMAIN.md
        # keeps it on the user document), so it is stored where the chef
        # and the account area both read it.
        if request.delivery_address != (user.delivery_address or ""):
            users_repo.update_delivery_address(user.id, request.delivery_address)

    _append_charge(placed)
    return placed


def _create_with_reference(user_id: str, order: Order, moment: date) -> Order:
    """Insert the order, redrawing its reference if the index refuses it."""
    last_error: Exception | None = None
    for _attempt in range(REFERENCE_ATTEMPTS):
        candidate = order.model_copy(update={"reference": next_reference(moment)})
        try:
            return orders_repo.create_order(user_id, candidate)
        except orders_repo.ReferenceTaken as exc:
            last_error = exc
    raise CheckoutError(
        "Your order could not be given a reference. Please try again."
    ) from last_error


def _append_charge(order: Order) -> None:
    """The order's charge on the customer's ledger.

    Positive cents: a charge increases what is owed, and the credit that
    offsets it on cancellation is the same number negated. The balance is
    a sum over these entries and is never a stored field (01-DOMAIN.md).
    """
    try:
        ledger_repo.append_entry(
            order.user_id,
            LedgerEntry(
                user_id=order.user_id,
                order_id=order.id,
                entry_type=LedgerEntryType.CHARGE,
                amount_cents=order.total_cents,
                description=f"Order {order.reference}",
                created_by=order.user_id,
            ),
        )
    except Exception:  # noqa: BLE001 — the order stands; the entry is repairable
        logger.error(
            "order placed without its ledger charge",
            extra={"order_id": order.id, "reference": order.reference},
            exc_info=True,
        )
