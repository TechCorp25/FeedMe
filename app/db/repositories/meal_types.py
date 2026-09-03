"""meal_types collection. Chef-owned, ordered, renameable."""

from __future__ import annotations

from pymongo import ASCENDING

from app.db.client import get_db
from app.db.repositories._common import parse_many, parse_one
from app.models.catalogue import MealType

COLLECTION = "meal_types"


def list_meal_types() -> list[MealType]:
    cursor = get_db()[COLLECTION].find({}).sort("sort_order", ASCENDING)
    return parse_many(MealType, cursor)


def get_meal_type_by_slug(slug: str) -> MealType | None:
    return parse_one(MealType, get_db()[COLLECTION].find_one({"slug": slug}))
