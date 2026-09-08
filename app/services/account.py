"""The customer's own account: profile, order history, cancellation, balance.

Three rules shape this module.

**Tenancy is the repository's.** Nothing here re-filters by `user_id`;
every read it makes is already scoped (02-ARCHITECTURE.md), and a service
that filtered again would hide the day a repository function stopped
doing so.

**A cancellation is a transition, not a status write.** `order_state.py`
decides whether the customer may cancel from where the order stands and
returns the new `Order`; this writes it under the status it was decided
against, so a confirmation clicked twice — or a chef moving the same
order on at the same moment — cannot produce two cancellations and two
offsetting ledger credits.

**The balance is summed, never stored.** 01-DOMAIN.md computes it by
aggregation over an append-only ledger; a correction is a new offsetting
entry and never an edit.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from app.db.repositories import components as components_repo
from app.db.repositories import dishes as dishes_repo
from app.db.repositories import ledger as ledger_repo
from app.db.repositories import orders as orders_repo
from app.db.repositories import users as users_repo
from app.models.catalogue import preference_flag_label
from app.models.orders import (
    LedgerEntry,
    LedgerEntryType,
    Order,
    OrderStatus,
)
from app.models.users import User
from app.services import order_state

logger = logging.getLogger(__name__)

MAX_DISPLAY_NAME_LENGTH = 120
MAX_PHONE_LENGTH = 40
MAX_ADDRESS_LENGTH = 500
MAX_DIETARY_NOTES_LENGTH = 1000

#: How many orders the history page shows. The account area is a customer
#: reading their own past, not an export; a bound keeps one page one page.
ORDER_HISTORY_LIMIT = 100
LEDGER_LIMIT = 200


class ProfileError(ValueError):
    """A profile edit the customer can fix, with the wording to show them."""


class CancellationError(ValueError):
    """A cancellation that will not happen, with the reason to show."""


# --- profile ----------------------------------------------------------------


def offered_preference_flags() -> list[str]:
    """Every preference flag the visible catalogue currently uses.

    Read from the data, exactly as the browse filter strips are: the
    vocabulary is chef-extensible (01-DOMAIN.md), and a default the
    customer sets has to be a flag the filter can actually apply. The two
    catalogues are merged because one set of defaults pre-applies to
    both.

    Deliberately unlike the allergen strip, which offers its whole
    vocabulary whether the catalogue uses it or not: an absent allergen
    would read as a claim about the food, and an absent preference flag
    claims nothing.
    """
    flags = set(components_repo.visible_component_preference_flags())
    flags.update(dishes_repo.visible_dish_preference_flags())
    return sorted(flags)


@dataclass(frozen=True)
class PreferenceChoice:
    value: str
    label: str
    selected: bool


@dataclass(frozen=True)
class ProfileUpdate:
    """A profile edit, parsed and bounded. Never a raw form."""

    display_name: str
    phone: str | None
    delivery_address: str | None
    dietary_notes: str | None
    default_preference_filters: list[str]


def _bounded(raw: str | None, limit: int, message: str) -> str:
    value = (raw or "").strip()
    if len(value) > limit:
        raise ProfileError(message)
    return value


def parse_profile_form(form, *, offered: list[str]) -> ProfileUpdate:
    """Read the whole form, or raise the first refusal it earns.

    Only the five fields the customer owns are read. `role`, `is_active`,
    `email` and `password_hash` are not among them, and the repository
    function this feeds names its fields for the same reason: a form the
    customer controls must not be able to reach them.
    """
    display_name = _bounded(
        form.get("display_name"),
        MAX_DISPLAY_NAME_LENGTH,
        f"Keep your name to {MAX_DISPLAY_NAME_LENGTH} characters or fewer.",
    )
    phone = _bounded(
        form.get("phone"),
        MAX_PHONE_LENGTH,
        f"Keep your phone number to {MAX_PHONE_LENGTH} characters or fewer.",
    )
    address = _bounded(
        form.get("delivery_address"),
        MAX_ADDRESS_LENGTH,
        f"Keep your address to {MAX_ADDRESS_LENGTH} characters or fewer.",
    )
    dietary_notes = _bounded(
        form.get("dietary_notes"),
        MAX_DIETARY_NOTES_LENGTH,
        "Keep your dietary notes to "
        f"{MAX_DIETARY_NOTES_LENGTH} characters or fewer.",
    )

    # Kept in the offered order and deduplicated, so the same selection
    # always stores the same list and always renders the same chips. An
    # unrecognised flag is dropped rather than refused: it widens the
    # browse result, which is the safe direction to fail here as well.
    requested = {value.strip() for value in form.getlist("preference") if value}
    filters = [flag for flag in offered if flag in requested]

    return ProfileUpdate(
        display_name=display_name,
        phone=phone or None,
        delivery_address=address or None,
        dietary_notes=dietary_notes or None,
        default_preference_filters=filters,
    )


def save_profile(user: User, update: ProfileUpdate) -> User:
    """Write the profile and return the user as it now stands."""
    if user.id is None:
        raise ProfileError("Sign in to change your profile.")
    users_repo.update_profile(
        user.id,
        display_name=update.display_name,
        phone=update.phone,
        delivery_address=update.delivery_address,
        dietary_notes=update.dietary_notes,
        default_preference_filters=update.default_preference_filters,
    )
    return user.model_copy(update=asdict(update))


def preference_choices(user: User, offered: list[str]) -> list[PreferenceChoice]:
    """The default-filter checkboxes, resolved for display.

    A flag the customer has stored that the catalogue no longer offers is
    kept in the list, checked, rather than quietly disappearing from the
    form: it is still stored, it still narrows their browse, and a
    control that does not show it would be a control that lies.
    """
    stored = list(user.default_preference_filters)
    values = offered + [flag for flag in stored if flag not in offered]
    return [
        PreferenceChoice(
            value=flag,
            label=preference_flag_label(flag),
            selected=flag in stored,
        )
        for flag in values
    ]


# --- orders -----------------------------------------------------------------


def list_orders(user_id: str) -> list[Order]:
    """This customer's orders, newest first."""
    return orders_repo.list_orders(user_id, limit=ORDER_HISTORY_LIMIT)


def get_order(user_id: str, reference: str) -> Order | None:
    """One of this customer's orders by reference, or None.

    None, never another customer's order and never a 403: the caller
    turns it into a 404 so the answer does not confirm that somebody
    else's order exists (02-ARCHITECTURE.md).
    """
    return orders_repo.get_order_by_reference(user_id, reference)


def can_customer_cancel(order: Order) -> bool:
    """Whether the cancel control is offered at all.

    Asked by the template as well as by the POST. The POST does not trust
    it — `order_state` decides again, against the status read inside the
    request, and the write is conditional on that status — but a button
    that is going to be refused should not be drawn.
    """
    return order.status in order_state.CUSTOMER_CANCELLABLE_FROM


def cancel_order(user: User, order: Order) -> Order:
    """Cancel one of the customer's own orders and credit the charge back.

    Ordering is deliberate: the transition is written first, under the
    status it was decided against, and the credit is appended only if
    that write matched. A credit written first would stand alone if the
    transition then lost its race, leaving a customer credited for an
    order still being prepared.

    The remaining failure is the mirror of the one checkout carries and
    names: a cancellation whose credit did not reach the ledger. It is
    detectable (the order is cancelled and no credit carries its
    `order_id`), repairable by appending the entry, and never money taken
    for something nobody is cooking. It is logged at ERROR.
    """
    if user.id is None:
        raise CancellationError("Sign in to cancel an order.")

    current = order.status
    try:
        cancelled = order_state.apply_transition(
            order,
            OrderStatus.CANCELLED,
            by=user.id,
            actor_is_chef=False,
        )
    except order_state.InvalidTransition as exc:
        raise CancellationError(
            "This order can no longer be cancelled here. "
            "Contact the kitchen and they will sort it out."
        ) from exc

    if not orders_repo.apply_transition(
        user.id, cancelled, expected_status=current
    ):
        # Somebody else moved this order between the read and the write —
        # the chef confirming it, or this same form submitted twice. The
        # order is not cancelled twice and no second credit is written.
        raise CancellationError(
            "This order moved on while you were looking at it. "
            "Check its status below."
        )

    _append_credit(cancelled)
    return cancelled


def _append_credit(order: Order) -> None:
    """The entry that offsets the order's charge.

    The charge was `+total_cents` (`services/checkout.py`); the credit is
    the same number negated, so a placed-then-cancelled order nets to
    nothing on a balance that is a sum over entries.

    A failure here does not un-cancel the order. The customer asked for
    it, the kitchen must not cook it, and refusing the cancellation over
    a bookkeeping write would be the worse outcome; the missing entry is
    logged loudly and appended by hand.
    """
    try:
        ledger_repo.append_entry(
            order.user_id,
            LedgerEntry(
                user_id=order.user_id,
                order_id=order.id,
                entry_type=LedgerEntryType.CREDIT,
                amount_cents=-order.total_cents,
                description=f"Cancelled order {order.reference}",
                created_by=order.user_id,
            ),
        )
    except Exception:  # noqa: BLE001 — the cancellation stands; the entry is repairable
        logger.error(
            "order cancelled without its ledger credit",
            extra={"order_id": order.id, "reference": order.reference},
            exc_info=True,
        )


# --- balance ----------------------------------------------------------------


@dataclass(frozen=True)
class LedgerRow:
    """One ledger entry with the balance as it stood after it."""

    entry: LedgerEntry
    balance_cents: int


@dataclass(frozen=True)
class BalanceView:
    rows: list[LedgerRow]
    balance_cents: int

    @property
    def is_empty(self) -> bool:
        return not self.rows


def balance_view(user_id: str) -> BalanceView:
    """The ledger oldest-first, each row carrying the running balance.

    The closing balance is the database's own sum over every entry, not
    the last row's running total: the rows are a bounded page and the
    balance is not allowed to be wrong because a customer has more
    history than one page shows.
    """
    entries = ledger_repo.list_entries(user_id, limit=LEDGER_LIMIT)
    running = 0
    rows: list[LedgerRow] = []
    for entry in entries:
        running += entry.amount_cents
        rows.append(LedgerRow(entry=entry, balance_cents=running))
    return BalanceView(rows=rows, balance_cents=ledger_repo.balance_cents(user_id))
