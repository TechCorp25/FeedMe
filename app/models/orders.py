"""Order models.

Name, unit price and the full allergen block are snapshotted onto every
line at checkout, and so is the storage block. A later catalogue edit
must never retroactively change what a customer was told they were
eating, nor how long they were told to keep it (01-DOMAIN.md).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import Field, model_validator

from app.models.allergens import AllergenBlock
from app.models.base import EmbeddedModel, MongoModel, TimestampedModel, utcnow
from app.models.catalogue import StorageBlock


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

    #: The storage guidance as it stood when the order was placed. The
    #: customer's use-by is `prepared_at + shelf_life_days`, and a shelf
    #: life read live from the catalogue is one the chef may have edited
    #: since — a use-by *lengthened* underneath a customer is the one
    #: direction this must never fail in.
    #:
    #: The whole block, not `shelf_life_days` alone. A date computed from
    #: a snapshotted shelf life, sitting beside a method and temperature
    #: the chef has since changed from "refrigerate" to "freeze", is
    #: worse than either alone. The allergen block set the precedent: the
    #: whole compliance block travels with the line.
    #:
    #: Nullable, and never backfilled. Lines written before this field
    #: existed have no snapshot, and inventing one from today's catalogue
    #: would be exactly the retroactive edit the snapshot exists to
    #: prevent. An item with no storage block of its own also lands here.
    #: Either way the order page says where the current guidance is
    #: rather than computing a date it cannot stand behind.
    storage_snapshot: StorageBlock | None = None

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
