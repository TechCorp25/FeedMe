"""The chef's view of one customer's ledger, and the entries they add.

04-WORKFLOWS.md: "View entries, add manual `adjustment` or `credit`
entries with a description. Entries are append-only; corrections are new
offsetting entries, never edits or deletes."

Three rules follow from that, and they are the whole module.

**Append-only means there is no edit and no delete.** Not a disabled
button, not a route that refuses — no such route exists. A ledger whose
history can be rewritten is not a record of anything.

**A `charge` cannot be entered by hand.** 04-WORKFLOWS.md names
`adjustment` and `credit` and stops there: a charge is what checkout
writes against an order, and one typed in here would be money owed
against nothing. The form does not offer it and the parser refuses it.

**The sign is the chef's to state, never inferred from the type.** A
credit reduces what the customer owes, so it is stored negative — the
same convention `append_cancellation_credit` already writes. An
adjustment can go either way, so the form makes the chef say which, in
words, rather than reading a minus sign they may not have typed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.repositories import ledger as ledger_repo
from app.db.repositories import users as users_repo
from app.models.orders import LedgerEntry, LedgerEntryType
from app.models.users import User

#: How many entries one page shows, windowed from the newest end.
LEDGER_LIMIT = 200

#: How many customers one page of the index lists.
CUSTOMER_LIMIT = 100

MAX_DESCRIPTION = 500
#: $10,000 in cents. A bound on a typo, not on the business.
MAX_AMOUNT_CENTS = 1_000_000

#: What the chef may write by hand. `charge` is deliberately absent.
MANUAL_ENTRY_TYPES: tuple[LedgerEntryType, ...] = (
    LedgerEntryType.CREDIT,
    LedgerEntryType.ADJUSTMENT,
)

ENTRY_TYPE_LABELS: dict[LedgerEntryType, str] = {
    LedgerEntryType.CHARGE: "Charge",
    LedgerEntryType.CREDIT: "Credit",
    LedgerEntryType.ADJUSTMENT: "Adjustment",
}

#: The two directions an adjustment can go, said in words. A minus sign
#: in a number field is too easy to leave off and too easy not to notice.
DIRECTION_LABELS: dict[str, str] = {
    "owes_more": "Increase what they owe",
    "owes_less": "Reduce what they owe",
}


class LedgerEntryError(ValueError):
    """A refusal the chef can fix, with the wording to show them."""


@dataclass(frozen=True)
class LedgerRow:
    entry: LedgerEntry
    balance_cents: int

    @property
    def type_label(self) -> str:
        return ENTRY_TYPE_LABELS[self.entry.entry_type]

    @property
    def direction_label(self) -> str:
        """What this entry did to the balance, in words.

        A signed number in a column is a distinction carried by a single
        character; 03-FRONTEND.md forbids a distinction carried by colour
        alone and this is the same failure in a different key.
        """
        if self.entry.amount_cents > 0:
            return "Added to what they owe"
        if self.entry.amount_cents < 0:
            return "Reduced what they owe"
        return "No change"


@dataclass(frozen=True)
class CustomerLedger:
    customer: User
    rows: list[LedgerRow]
    balance_cents: int
    opening_balance_cents: int = 0
    is_truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.rows


def parse_amount_cents(raw: str | None) -> int:
    """Dollars as typed to integer cents, without a float anywhere.

    The same rule as every other price in this application: integer minor
    units, and `float("14.50") * 100` is 1449.9999999999998.

    Always positive. The direction is a separate, worded choice.
    """
    text = (raw or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not text:
        raise LedgerEntryError("Give the entry an amount.")
    if text.startswith("-"):
        raise LedgerEntryError(
            "Enter the amount as a positive number and choose whether it "
            "adds to or reduces what the customer owes."
        )

    whole, point, fraction = text.partition(".")
    if point and len(fraction) > 2:
        raise LedgerEntryError("An amount has at most two decimal places.")
    whole = whole or "0"
    fraction = (fraction + "00")[:2] if point else "00"
    if not (whole.isdigit() and fraction.isdigit()):
        raise LedgerEntryError("An amount is a number, like 14.50.")

    cents = int(whole) * 100 + int(fraction)
    if cents == 0:
        raise LedgerEntryError("An entry of nothing records nothing.")
    if cents > MAX_AMOUNT_CENTS:
        raise LedgerEntryError("That amount looks wrong — check it and try again.")
    return cents


@dataclass(frozen=True)
class CustomerRow:
    """One customer in the index, with the figure the chef looks for."""

    customer: User
    balance_cents: int

    @property
    def name(self) -> str:
        """The display name, falling back to the address they signed up with."""
        return self.customer.display_name or self.customer.email

    @property
    def has_dietary_notes(self) -> bool:
        return bool((self.customer.dietary_notes or "").strip())


@dataclass(frozen=True)
class CustomerDirectory:
    rows: list[CustomerRow]
    page: int = 1
    has_next: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.rows

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def previous_page(self) -> int:
        return max(1, self.page - 1)

    @property
    def next_page(self) -> int:
        return self.page + 1

    @property
    def first_position(self) -> int:
        """The 1-based position of the first row, for the page's wording."""
        return (self.page - 1) * CUSTOMER_LIMIT + 1

    @property
    def last_position(self) -> int:
        return self.first_position + len(self.rows) - 1


def parse_page(raw: str | None) -> int:
    """A page number from a query string. Anything else is page one.

    The same rule every other filter here keeps: an unrecognised value is
    dropped rather than rejected, because a mistyped URL should show the
    chef their customers, not an error page.
    """
    text = (raw or "").strip()
    return int(text) if text.isdigit() and int(text) >= 1 else 1


def customer_directory(page: int = 1) -> CustomerDirectory:
    """One page of customers, with their balances, for `/chef/customers`.

    04-WORKFLOWS.md gives the ledger a URL and no page to reach it from;
    the order queue links to a customer who has an order outstanding,
    which is not the same set. A customer who settled last month still
    has a ledger.

    Paged rather than bounded. A bound with nothing past it would leave
    every customer after the first page with no route to their ledger at
    all, which is the one thing this page exists to provide. One extra
    row is read to learn whether there is a next page — cheaper than
    counting the collection, and exact.

    Two reads per page, not one per row: the customers, and every balance
    in one aggregation. A customer with no entries has nothing to sum and
    reads as zero, which is what a balance computed by aggregation means
    when there is nothing to aggregate.
    """
    page = max(1, page)
    found = users_repo.chef_list_customers(
        limit=CUSTOMER_LIMIT + 1, skip=(page - 1) * CUSTOMER_LIMIT
    )
    has_next = len(found) > CUSTOMER_LIMIT
    customers = found[:CUSTOMER_LIMIT]
    balances = ledger_repo.chef_balances_by_user()
    return CustomerDirectory(
        rows=[
            CustomerRow(
                customer=customer,
                balance_cents=balances.get(customer.get_id(), 0),
            )
            for customer in customers
        ],
        page=page,
        has_next=has_next,
    )


def get_customer(user_id: str) -> User | None:
    return users_repo.chef_list_customers_by_ids([user_id]).get(user_id)


def customer_ledger(customer: User) -> CustomerLedger:
    """One customer's entries, oldest first, each with the running balance.

    The closing figure is the database's sum over *every* entry, not the
    last row's running total: the rows are a bounded window, and a
    balance that is wrong because a customer has more history than one
    page shows is a balance the chef would settle against.
    """
    user_id = customer.get_id()
    entries = ledger_repo.chef_list_entries(user_id, limit=LEDGER_LIMIT)
    closing = ledger_repo.balance_cents(user_id)
    opening = closing - sum(entry.amount_cents for entry in entries)

    running = opening
    rows: list[LedgerRow] = []
    for entry in entries:
        running += entry.amount_cents
        rows.append(LedgerRow(entry=entry, balance_cents=running))

    return CustomerLedger(
        customer=customer,
        rows=rows,
        balance_cents=closing,
        opening_balance_cents=opening,
        is_truncated=len(entries) >= LEDGER_LIMIT,
    )


def append_manual_entry(
    chef: User,
    customer: User,
    *,
    entry_type: str | None,
    amount: str | None,
    direction: str | None,
    description: str | None,
) -> LedgerEntry:
    """Write one hand-entered `credit` or `adjustment`. Never a charge."""
    if chef.id is None:
        raise LedgerEntryError("Sign in again to add an entry.")

    try:
        parsed_type = LedgerEntryType(entry_type or "")
    except ValueError as exc:
        raise LedgerEntryError("That is not an entry type.") from exc
    if parsed_type not in MANUAL_ENTRY_TYPES:
        # A charge is what checkout writes against an order. Typed in
        # here it would be money owed against nothing.
        raise LedgerEntryError(
            "Only a credit or an adjustment can be entered by hand. A charge "
            "is written when an order is placed."
        )

    text = (description or "").strip()
    if not text:
        raise LedgerEntryError(
            "Say what this entry is for. It is the only record of why the "
            "balance moved."
        )
    if len(text) > MAX_DESCRIPTION:
        raise LedgerEntryError(
            f"A description is at most {MAX_DESCRIPTION} characters."
        )

    cents = parse_amount_cents(amount)
    signed = _signed_amount(parsed_type, direction, cents)

    return ledger_repo.append_entry(
        customer.get_id(),
        LedgerEntry(
            user_id=customer.get_id(),
            entry_type=parsed_type,
            amount_cents=signed,
            description=text,
            # Who made the entry, not who it is against. An append-only
            # record that does not say who wrote a line is not much of a
            # record.
            created_by=chef.id,
        ),
    )


def _signed_amount(
    entry_type: LedgerEntryType, direction: str | None, cents: int
) -> int:
    """Turn the chef's worded choice into the stored sign.

    A credit reduces what the customer owes and is always negative — the
    same convention the cancellation credit already writes, so a
    placed-then-cancelled order still nets to nothing. An adjustment goes
    whichever way the chef said.
    """
    if entry_type is LedgerEntryType.CREDIT:
        return -cents
    if direction not in DIRECTION_LABELS:
        raise LedgerEntryError(
            "Say whether this adjustment adds to or reduces what the "
            "customer owes."
        )
    return cents if direction == "owes_more" else -cents
