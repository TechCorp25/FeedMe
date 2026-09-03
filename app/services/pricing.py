"""Price arithmetic. Integer minor units only — no float touches money."""

from __future__ import annotations

from collections.abc import Iterable

from app.models.orders import OrderLine


def line_total_cents(unit_price_cents: int, quantity: int) -> int:
    if unit_price_cents < 0:
        raise ValueError("unit_price_cents cannot be negative")
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    return unit_price_cents * quantity


def subtotal_cents(lines: Iterable[OrderLine]) -> int:
    return sum(line.line_total_cents for line in lines)


def total_cents(lines: Iterable[OrderLine]) -> int:
    """Total equals subtotal: there are no fees, discounts or tax lines yet."""
    return subtotal_cents(lines)


def format_price_aud(cents: int) -> str:
    """Render minor units for display. Formatting only — never arithmetic."""
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars}.{remainder:02d}"
