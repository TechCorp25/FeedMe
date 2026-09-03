"""Order models.

Name, unit price and the full allergen block are snapshotted onto every
line at checkout. A later catalogue edit must never retroactively change
what a customer was told they were eating (01-DOMAIN.md).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import Field, model_validator

from app.models.allergens import AllergenBlock
from app.models.base import EmbeddedModel, MongoModel, TimestampedModel, utcnow


class OrderStatus(str, Enum):
    PLACED = "placed"
    CONFIRMED = "confirmed"
    PREPPING = "prepping"
    READY = "ready"
    COLLECTED = "collected"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class PaymentStatus(str, Enum):
    """Tracking only. The application never captures money."""

    UNPAID = "unpaid"
    SETTLED = "settled"
    WAIVED = "waived"


class Fulfilment(str, Enum):
    COLLECTION = "collection"
    DELIVERY = "delivery"


class ItemType(str, Enum):
    COMPONENT = "component"
    DISH = "dish"


class OrderLine(EmbeddedModel):
    item_type: ItemType
    item_id: str
    name_snapshot: str
    unit_price_cents: int = Field(ge=0)
    quantity: int = Field(ge=1)
    line_total_cents: int = Field(ge=0)
    allergen_snapshot: AllergenBlock

    @model_validator(mode="after")
    def _line_total_is_consistent(self) -> "OrderLine":
        expected = self.unit_price_cents * self.quantity
        if self.line_total_cents != expected:
            raise ValueError(
                f"line_total_cents {self.line_total_cents} does not equal "
                f"unit_price_cents * quantity ({expected})"
            )
        return self


class StatusHistoryEntry(EmbeddedModel):
    status: OrderStatus
    at: datetime = Field(default_factory=utcnow)
    by: str


class Order(TimestampedModel):
    user_id: str
    reference: str
    status: OrderStatus = OrderStatus.PLACED
    lines: list[OrderLine] = Field(default_factory=list)
    subtotal_cents: int = Field(default=0, ge=0)
    total_cents: int = Field(default=0, ge=0)
    payment_status: PaymentStatus = PaymentStatus.UNPAID
    requested_for: date | None = None
    fulfilment: Fulfilment = Fulfilment.COLLECTION
    customer_note: str | None = None
    chef_note: str | None = None
    prepared_at: datetime | None = None
    status_history: list[StatusHistoryEntry] = Field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.COLLECTED, OrderStatus.DELIVERED, OrderStatus.CANCELLED}
)


class LedgerEntryType(str, Enum):
    CHARGE = "charge"
    CREDIT = "credit"
    ADJUSTMENT = "adjustment"


class LedgerEntry(MongoModel):
    """Append-only. Corrections are new offsetting entries, never edits.

    Deliberately not a TimestampedModel: an entry is never updated, so it
    has no `updated_at`.
    """

    created_at: datetime = Field(default_factory=utcnow)
    user_id: str
    order_id: str | None = None
    entry_type: LedgerEntryType
    amount_cents: int
    description: str
    created_by: str
