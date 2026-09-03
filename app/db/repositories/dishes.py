"""dishes collection.

The catalogue is chef-owned, not customer-owned, so these functions carry
no `user_id`. Customer-facing reads are constrained by visibility
(`is_available` and not `is_archived`) rather than by tenancy.
"""

from __future__ import annotations

from pymongo import ASCENDING

from app.db.client import get_db
from app.db.repositories._common import parse_many, parse_one, to_object_id
from app.models.catalogue import Dish

COLLECTION = "dishes"

_VISIBLE = {"is_available": True, "is_archived": False}


def list_visible_dishes() -> list[Dish]:
    cursor = get_db()[COLLECTION].find(dict(_VISIBLE)).sort("sort_order", ASCENDING)
    return parse_many(Dish, cursor)


def get_visible_dish_by_slug(slug: str) -> Dish | None:
    return parse_one(
        Dish, get_db()[COLLECTION].find_one({"slug": slug, **_VISIBLE})
    )


# --- chef scope: sees drafts and archived items -----------------------------


def chef_list_dishes(include_archived: bool = False) -> list[Dish]:
    query: dict = {} if include_archived else {"is_archived": False}
    cursor = get_db()[COLLECTION].find(query).sort("sort_order", ASCENDING)
    return parse_many(Dish, cursor)


def chef_get_dish(dish_id: str) -> Dish | None:
    object_id = to_object_id(dish_id)
    if object_id is None:
        return None
    return parse_one(Dish, get_db()[COLLECTION].find_one({"_id": object_id}))


def chef_create_dish(dish: Dish) -> Dish:
    result = get_db()[COLLECTION].insert_one(dish.to_mongo())
    return dish.model_copy(update={"id": str(result.inserted_id)})
