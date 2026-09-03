"""Cart operations. Lines carry item identity and quantity only."""

from __future__ import annotations

import pytest

from app.models.orders import ItemType
from app.services.cart import Cart, add_line, remove_line, set_quantity


def test_a_cart_line_carries_no_price_or_allergen_data():
    """Price and allergens resolve live until checkout snapshots them."""
    cart = add_line(Cart(), ItemType.COMPONENT, "c1")
    assert set(cart.lines[0].model_dump()) == {"item_type", "item_id", "quantity"}


def test_adding_the_same_item_increments_the_existing_line():
    cart = add_line(Cart(), ItemType.COMPONENT, "c1", 2)
    cart = add_line(cart, ItemType.COMPONENT, "c1", 3)
    assert len(cart.lines) == 1
    assert cart.lines[0].quantity == 5
    assert cart.item_count == 5


def test_the_same_id_in_each_catalogue_is_two_lines():
    """components and dishes are separate catalogues; ids do not collide."""
    cart = add_line(Cart(), ItemType.COMPONENT, "x")
    cart = add_line(cart, ItemType.DISH, "x")
    assert len(cart.lines) == 2


def test_set_quantity_replaces_rather_than_adds():
    cart = add_line(Cart(), ItemType.DISH, "d1", 4)
    assert set_quantity(cart, ItemType.DISH, "d1", 1).lines[0].quantity == 1


def test_set_quantity_to_zero_removes_the_line():
    cart = add_line(Cart(), ItemType.DISH, "d1", 4)
    assert set_quantity(cart, ItemType.DISH, "d1", 0).lines == []


def test_remove_line_leaves_other_lines_alone():
    cart = add_line(add_line(Cart(), ItemType.DISH, "d1"), ItemType.DISH, "d2")
    remaining = remove_line(cart, ItemType.DISH, "d1")
    assert [line.item_id for line in remaining.lines] == ["d2"]


def test_operations_do_not_mutate_the_original_cart():
    cart = add_line(Cart(), ItemType.DISH, "d1")
    add_line(cart, ItemType.DISH, "d1", 5)
    assert cart.lines[0].quantity == 1


@pytest.mark.parametrize("quantity", [0, -1])
def test_add_rejects_a_non_positive_quantity(quantity):
    with pytest.raises(ValueError):
        add_line(Cart(), ItemType.DISH, "d1", quantity)
