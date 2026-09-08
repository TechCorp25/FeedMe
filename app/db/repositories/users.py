"""users collection."""

from __future__ import annotations

from datetime import datetime

from pymongo.errors import DuplicateKeyError

from app.db.client import get_db
from app.db.repositories._common import parse_one, to_object_id
from app.models.base import utcnow
from app.models.users import User

COLLECTION = "users"


class EmailAlreadyRegistered(ValueError):
    """Raised when the unique index on `email` refuses an insert.

    The check and the insert cannot be one operation, so a duplicate is
    caught here rather than guessed at beforehand: the index is what
    actually decides, and two simultaneous registrations for one address
    would otherwise both pass a prior check.
    """


def get_user(user_id: str) -> User | None:
    object_id = to_object_id(user_id)
    if object_id is None:
        return None
    return parse_one(User, get_db()[COLLECTION].find_one({"_id": object_id}))


def get_user_by_email(email: str) -> User | None:
    return parse_one(
        User, get_db()[COLLECTION].find_one({"email": email.strip().lower()})
    )


def create_user(user: User) -> User:
    try:
        result = get_db()[COLLECTION].insert_one(user.to_mongo())
    except DuplicateKeyError as exc:
        raise EmailAlreadyRegistered(user.email) from exc
    return user.model_copy(update={"id": str(result.inserted_id)})


def record_login(user_id: str, at: datetime | None = None) -> None:
    """Stamp `last_login_at`. Scoped by `user_id`, like every owned write."""
    object_id = to_object_id(user_id)
    if object_id is None:
        return
    moment = at or utcnow()
    get_db()[COLLECTION].update_one(
        {"_id": object_id},
        {"$set": {"last_login_at": moment, "updated_at": moment}},
    )


def update_delivery_address(user_id: str, address: str | None) -> None:
    """Store the address a delivery goes to.

    Written at checkout as well as from the account area: the address
    belongs to the customer, not to one order, and 01-DOMAIN.md keeps it
    on the user document. An order therefore carries no address of its
    own, and the chef reads the current one.
    """
    object_id = to_object_id(user_id)
    if object_id is None:
        return
    cleaned = (address or "").strip() or None
    get_db()[COLLECTION].update_one(
        {"_id": object_id},
        {"$set": {"delivery_address": cleaned, "updated_at": utcnow()}},
    )


def update_password_hash(user_id: str, password_hash: str) -> None:
    """Store a re-derived hash after Argon2 asks for a rehash."""
    object_id = to_object_id(user_id)
    if object_id is None:
        return
    get_db()[COLLECTION].update_one(
        {"_id": object_id},
        {"$set": {"password_hash": password_hash, "updated_at": utcnow()}},
    )
