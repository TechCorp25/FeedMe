"""dishes collection.

The catalogue is chef-owned, not customer-owned, so these functions carry
no `user_id`. Customer-facing reads are constrained by visibility
(`is_available` and not `is_archived`) rather than by tenancy.
"""

from __future__ import annotations

from collections.abc import Sequence

from pymongo import ASCENDING

from app.db.client import get_db
from app.db.repositories._common import parse_many, parse_one, to_object_id
from app.models.catalogue import Dish

COLLECTION = "dishes"

#: The customer-facing predicate. 01-DOMAIN.md derives publication from
#: these two flags plus the allergen review; the review is enforced by the
#: model, so an unreviewed item cannot be `is_available` in the first place.
_VISIBLE = {"is_available": True, "is_archived": False}


def _visible_query(
    meal_type_id: str | None,
    preference_flags: Sequence[str],
) -> dict:
    """Build the browse query. Filters narrow visibility, never widen it."""
    query: dict = dict(_VISIBLE)
    if meal_type_id is not None:
        # `meal_type_ids` is a list: a dish may sit under several meal
        # types, and matching one of them is enough.
        query["meal_type_ids"] = meal_type_id
    if preference_flags:
        # Every selected flag must be present: filters compose as AND, so
        # 'vegan + high protein' means both, not either.
        query["preference_flags"] = {"$all": list(preference_flags)}
    return query


def list_visible_dishes(
    *,
    meal_type_id: str | None = None,
    preference_flags: Sequence[str] = (),
) -> list[Dish]:
    cursor = (
        get_db()[COLLECTION]
        .find(_visible_query(meal_type_id, preference_flags))
        .sort([("sort_order", ASCENDING), ("name", ASCENDING)])
    )
    return parse_many(Dish, cursor)


def visible_dish_meal_type_ids() -> list[str]:
    """Meal type ids carried by at least one visible dish.

    Read from the data rather than from the `meal_types` collection so the
    filter strip never offers a choice that returns an empty page.
    """
    values = get_db()[COLLECTION].distinct("meal_type_ids", dict(_VISIBLE))
    return sorted(value for value in values if isinstance(value, str))


def visible_dish_preference_flags() -> list[str]:
    """Preference flags in use across the visible catalogue.

    Flags are chef-extensible (01-DOMAIN.md), so the filter strip is built
    from the data; a flag the chef invents appears without a code change.
    """
    values = get_db()[COLLECTION].distinct("preference_flags", dict(_VISIBLE))
    return sorted(value for value in values if isinstance(value, str))


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
