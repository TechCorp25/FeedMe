"""Cart shape and the pure operations over it.

A cart line stores `item_type`, `item_id` and `quantity` only. Price and
allergen data resolve live from the catalogue until checkout, where they
are snapshotted onto the order (04-WORKFLOWS.md).

Session wiring, availability checks and the checkout path belong with the
ordering flow and are not implemented here.
"""

from __future__ import annotations

from pydantic import Field

from app.models.base import EmbeddedModel
from app.models.orders import ItemType


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


def add_line(cart: Cart, item_type: ItemType, item_id: str, quantity: int = 1) -> Cart:
    """Add to an existing line for the same item, or append a new one."""
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    lines = list(cart.lines)
    index = _index_of(cart, item_type, item_id)
    if index is None:
        lines.append(CartLine(item_type=item_type, item_id=item_id, quantity=quantity))
    else:
        existing = lines[index]
        lines[index] = existing.model_copy(
            update={"quantity": existing.quantity + quantity}
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
    lines[index] = lines[index].model_copy(update={"quantity": quantity})
    return cart.model_copy(update={"lines": lines})


def remove_line(cart: Cart, item_type: ItemType, item_id: str) -> Cart:
    lines = [
        line
        for line in cart.lines
        if not (line.item_type is item_type and line.item_id == item_id)
    ]
    return cart.model_copy(update={"lines": lines})
