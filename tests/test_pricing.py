"""Price arithmetic. Integer cents only — a float here is a defect."""

from __future__ import annotations

import pytest

from app.models.allergens import AllergenBlock
from app.models.orders import ItemType, OrderLine
from app.services.pricing import (
    format_price_aud,
    line_total_cents,
    subtotal_cents,
    total_cents,
)


def make_line(unit_price_cents: int, quantity: int) -> OrderLine:
    return OrderLine(
        item_type=ItemType.COMPONENT,
        item_id="c1",
        name_snapshot="Harissa",
        unit_price_cents=unit_price_cents,
        quantity=quantity,
        line_total_cents=unit_price_cents * quantity,
        allergen_snapshot=AllergenBlock(),
    )


def test_line_total_is_integer_multiplication():
    result = line_total_cents(1999, 3)
    assert result == 5997
    assert isinstance(result, int)


@pytest.mark.parametrize(("price", "quantity"), [(-1, 1), (100, 0), (100, -2)])
def test_line_total_rejects_nonsense(price, quantity):
    with pytest.raises(ValueError):
        line_total_cents(price, quantity)


def test_subtotal_and_total_are_integers():
    lines = [make_line(1999, 3), make_line(650, 2)]
    assert subtotal_cents(lines) == 7297
    assert total_cents(lines) == 7297
    assert all(isinstance(value, int) for value in (subtotal_cents(lines),))


def test_order_line_rejects_an_inconsistent_total():
    with pytest.raises(ValueError, match="line_total_cents"):
        OrderLine(
            item_type=ItemType.DISH,
            item_id="d1",
            name_snapshot="Lamb shoulder",
            unit_price_cents=2500,
            quantity=2,
            line_total_cents=2500,
            allergen_snapshot=AllergenBlock(),
        )


@pytest.mark.parametrize(
    ("cents", "expected"),
    [(0, "$0.00"), (5, "$0.05"), (650, "$6.50"), (100000, "$1000.00"), (-250, "-$2.50")],
)
def test_display_formatting(cents, expected):
    assert format_price_aud(cents) == expected
