"""Cart shape, the pure operations over it, and where it is kept.

A cart line stores `item_type`, `item_id` and `quantity` only. Price and
allergen data resolve live from the catalogue until checkout, where they
are snapshotted onto the order (04-WORKFLOWS.md). Nothing here writes an
allergen block or copies one onto a line.

Three concerns, in order below and kept apart:

1. the shape and the pure operations over it — no Flask, no database;
2. the store, which is the Flask session: 01-DOMAIN.md names six
   collections and none of them is a cart, so a guest cart lives in the
   signed session cookie rather than in a seventh. What the cookie holds
   is ids and quantities; every price the customer is shown is read from
   the catalogue on the server, so a tampered cookie cannot alter one.
   04-WORKFLOWS.md also has the cart key to `user_id` once authenticated
   and a guest cart merge on login — there is no login yet, so
   `merge_into_user_cart` is where that goes and is not written here;
3. resolution, which pairs each line with its live catalogue item.
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import session
from pydantic import Field, ValidationError

from app.db.repositories import components as components_repo
from app.db.repositories import dishes as dishes_repo
from app.models.base import EmbeddedModel
from app.models.catalogue import ItemBase
from app.models.orders import ItemType
from app.services import pricing

#: Where the guest cart sits in the session.
SESSION_KEY = "cart"

#: Bounds, so a scripted client cannot grow the session cookie without
#: limit. Both are far above any real order and neither is a business
#: rule; a request that exceeds one is clamped rather than rejected, and
#: the cart page then shows the customer exactly what it holds.
MAX_LINES = 50
MAX_QUANTITY_PER_LINE = 99


class CartLine(EmbeddedModel):
    item_type: ItemType
    item_id: str
    quantity: int = Field(ge=1)


class Cart(EmbeddedModel):
    lines: list[CartLine] = Field(default_factory=list)

    @property
    def item_count(self) -> int:
        return sum(line.quantity for line in self.lines)


def _index_of(cart: Cart, item_type: ItemType, item_id: str) -> int | None:
    for index, line in enumerate(cart.lines):
        if line.item_type is item_type and line.item_id == item_id:
            return index
    return None


class CartFullError(ValueError):
    """Raised when a new line would exceed `MAX_LINES`."""


def _capped(quantity: int) -> int:
    return min(quantity, MAX_QUANTITY_PER_LINE)


def add_line(cart: Cart, item_type: ItemType, item_id: str, quantity: int = 1) -> Cart:
    """Add to an existing line for the same item, or append a new one."""
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    lines = list(cart.lines)
    index = _index_of(cart, item_type, item_id)
    if index is None:
        if len(lines) >= MAX_LINES:
            raise CartFullError(
                f"a cart holds at most {MAX_LINES} different items"
            )
        lines.append(
            CartLine(item_type=item_type, item_id=item_id, quantity=_capped(quantity))
        )
    else:
        existing = lines[index]
        lines[index] = existing.model_copy(
            update={"quantity": _capped(existing.quantity + quantity)}
        )
    return cart.model_copy(update={"lines": lines})


def set_quantity(
    cart: Cart, item_type: ItemType, item_id: str, quantity: int
) -> Cart:
    """Set a line's quantity. A quantity of 0 removes the line."""
    if quantity < 0:
        raise ValueError("quantity cannot be negative")
    if quantity == 0:
        return remove_line(cart, item_type, item_id)
    index = _index_of(cart, item_type, item_id)
    if index is None:
        return add_line(cart, item_type, item_id, quantity)
    lines = list(cart.lines)
    lines[index] = lines[index].model_copy(update={"quantity": _capped(quantity)})
    return cart.model_copy(update={"lines": lines})


def remove_line(cart: Cart, item_type: ItemType, item_id: str) -> Cart:
    lines = [
        line
        for line in cart.lines
        if not (line.item_type is item_type and line.item_id == item_id)
    ]
    return cart.model_copy(update={"lines": lines})


# --- the store --------------------------------------------------------------


def load_cart() -> Cart:
    """The cart for this session, or an empty one.

    Anything unreadable in the session is treated as an empty cart rather
    than an error. The session is signed, so a malformed value is not an
    attack that got through — it is an old cookie meeting newer code, and
    a customer should meet an empty cart, not a 500.
    """
    stored = session.get(SESSION_KEY)
    if not stored:
        return Cart()
    try:
        return Cart.model_validate({"lines": stored})
    except (ValidationError, TypeError):
        return Cart()


def save_cart(cart: Cart) -> None:
    """Write the cart back, or drop the key entirely when it is empty."""
    if not cart.lines:
        session.pop(SESSION_KEY, None)
        return
    session[SESSION_KEY] = [line.model_dump(mode="json") for line in cart.lines]


def cart_item_count() -> int:
    """The badge's number, read from the session alone.

    Rendered into the header of every page, so it must not touch the
    database: a count is not worth a query on a page that has nothing to
    do with the cart.
    """
    return load_cart().item_count


# --- resolution -------------------------------------------------------------


@dataclass(frozen=True)
class CartEntry:
    """One cart line paired with the catalogue item it names.

    `item` is the live document, which may be one the catalogue no longer
    publishes. `is_available` is what decides how it renders and whether
    checkout is blocked; a withdrawn item contributes its name and
    nothing else — no price, and nothing towards the subtotal.
    """

    line: CartLine
    item: ItemBase | None

    @property
    def quantity(self) -> int:
        return self.line.quantity

    @property
    def item_type(self) -> ItemType:
        return self.line.item_type

    @property
    def item_id(self) -> str:
        return self.line.item_id

    @property
    def is_available(self) -> bool:
        return self.item is not None and self.item.is_visible_to_customers

    @property
    def name(self) -> str:
        """The item's name, or a neutral stand-in when it is gone entirely.

        An item deleted outright leaves a line with no name to show. The
        line still renders, because a cart never silently drops one.
        """
        return self.item.name if self.item is not None else "Item no longer listed"

    @property
    def unit_price_cents(self) -> int:
        return self.item.price_cents if self.is_available else 0

    @property
    def line_total_cents(self) -> int:
        if not self.is_available:
            return 0
        return pricing.line_total_cents(self.unit_price_cents, self.quantity)


@dataclass(frozen=True)
class CartView:
    """A cart resolved against the catalogue, ready to render."""

    entries: list[CartEntry]

    @property
    def is_empty(self) -> bool:
        return not self.entries

    @property
    def item_count(self) -> int:
        return sum(entry.quantity for entry in self.entries)

    @property
    def unavailable(self) -> list[CartEntry]:
        return [entry for entry in self.entries if not entry.is_available]

    @property
    def is_blocked(self) -> bool:
        """Checkout waits until every unavailable line has been removed.

        Dropping those lines automatically would be quicker and would be
        the wrong thing: the customer chose them, and finding out at
        collection is worse than being told now (04-WORKFLOWS.md).
        """
        return bool(self.unavailable)

    @property
    def subtotal_cents(self) -> int:
        """Integer minor units, and only over lines that can be ordered."""
        return sum(entry.line_total_cents for entry in self.entries)


def _items_by_id(cart: Cart) -> dict[tuple[ItemType, str], ItemBase]:
    """Look each catalogue up once, not once per line."""
    resolved: dict[tuple[ItemType, str], ItemBase] = {}
    for item_type, repo_read in (
        (ItemType.COMPONENT, components_repo.list_components_for_cart),
        (ItemType.DISH, dishes_repo.list_dishes_for_cart),
    ):
        ids = [
            line.item_id for line in cart.lines if line.item_type is item_type
        ]
        for item in repo_read(ids) if ids else []:
            if item.id is not None:
                resolved[(item_type, item.id)] = item
    return resolved


def resolve_cart(cart: Cart | None = None) -> CartView:
    """Pair every line with its live catalogue item, in the cart's order.

    Prices are read here rather than remembered, so a cart shows the
    price the catalogue holds now. Nothing is frozen until checkout
    snapshots it onto the order (01-DOMAIN.md).
    """
    resolved_cart = load_cart() if cart is None else cart
    items = _items_by_id(resolved_cart)
    return CartView(
        entries=[
            CartEntry(line=line, item=items.get((line.item_type, line.item_id)))
            for line in resolved_cart.lines
        ]
    )


def find_orderable_item(item_type: ItemType, item_id: str) -> ItemBase | None:
    """The item a line may be added for, or None.

    Only a published item can be added: an unpublished one is not on
    sale, and the customer never saw a page for it. The customer-facing
    read decides that, so the publication rule is applied in the one
    place that owns it rather than re-checked here. A withdrawn item
    already in the cart is a different case, handled by `resolve_cart`.
    """
    if item_type is ItemType.COMPONENT:
        found = components_repo.list_visible_components_by_ids([item_id])
    else:
        found = dishes_repo.list_visible_dishes_by_ids([item_id])
    return found[0] if found else None


def parse_item_type(raw: str | None) -> ItemType | None:
    """Resolve an `item_type` from the outside world, or None.

    Unlike a browse filter, an unrecognised value here is not widened
    away: a mutation that quietly does nothing would leave the customer
    believing they had added something.
    """
    if not raw:
        return None
    try:
        return ItemType(raw)
    except ValueError:
        return None


def parse_quantity(raw: str | None, *, default: int | None = None) -> int | None:
    """Resolve a quantity, clamped to the per-line bound, or None.

    Returns None for anything that is not a whole number, so the caller
    can say so rather than guessing at an intent.
    """
    if raw is None or str(raw).strip() == "":
        return default
    try:
        quantity = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if quantity < 0:
        return None
    return _capped(quantity)


def merge_into_user_cart(user_id: str, guest_cart: Cart) -> None:
    """Seam for the guest-cart merge on login (04-WORKFLOWS.md).

    Deliberately unimplemented: there is no login yet, and a user-keyed
    cart has nowhere to live until the auth slice decides where. Writing
    it now would be a guess at that decision, not an implementation of it.
    """
    raise NotImplementedError(
        "the guest-cart merge lands with the auth slice, which decides "
        "where a user-keyed cart is stored"
    )
