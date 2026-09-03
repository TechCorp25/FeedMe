"""User model. Two roles only: customer and chef_admin."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator

from app.models.base import TimestampedModel


class Role(str, Enum):
    CUSTOMER = "customer"
    CHEF_ADMIN = "chef_admin"


class User(TimestampedModel):
    email: str = Field(min_length=3)
    password_hash: str
    display_name: str = ""
    phone: str | None = None
    delivery_address: str | None = None
    role: Role = Role.CUSTOMER
    is_active: bool = True
    last_login_at: datetime | None = None
    dietary_notes: str | None = None
    default_preference_filters: list[str] = Field(default_factory=list)

    @field_validator("email")
    @classmethod
    def _lowercase_email(cls, value: str) -> str:
        return value.strip().lower()

    @property
    def is_chef_admin(self) -> bool:
        return self.role is Role.CHEF_ADMIN

    # --- Flask-Login protocol ---------------------------------------------
    # Implemented directly rather than via UserMixin so the model stays the
    # single schema of record and no wrapper object crosses a layer boundary.

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        if self.id is None:
            raise ValueError("cannot identify an unsaved user")
        return self.id
