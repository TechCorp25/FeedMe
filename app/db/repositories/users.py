"""users collection."""

from __future__ import annotations

from app.db.client import get_db
from app.db.repositories._common import parse_one, to_object_id
from app.models.users import User

COLLECTION = "users"


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
    result = get_db()[COLLECTION].insert_one(user.to_mongo())
    return user.model_copy(update={"id": str(result.inserted_id)})
