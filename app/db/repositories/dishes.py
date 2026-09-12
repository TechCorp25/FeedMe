"""dishes collection.

The catalogue is chef-owned, not customer-owned, so these functions carry
no `user_id`. Customer-facing reads are constrained by visibility
(`is_available` and not `is_archived`) rather than by tenancy.
"""

from __future__ import annotations

from collections.abc import Sequence

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from app.db.client import get_db
from app.models.base import utcnow
from app.db.repositories._common import parse_many, parse_one, to_object_id
from app.models.allergens import AllergenCode
from app.models.catalogue import Dish

COLLECTION = "dishes"

#: The customer-facing predicate. 01-DOMAIN.md derives publication from
#: these two flags plus the allergen review; the review is enforced by the
#: model, so an unreviewed item cannot be `is_available` in the first place.
_VISIBLE = {"is_available": True, "is_archived": False}


def _visible_query(
    meal_type_id: str | None,
    preference_flags: Sequence[str],
    exclude_allergens: Sequence[AllergenCode] = (),
) -> dict:
    """Build the browse query. Filters narrow visibility, never widen it."""
    query: dict = dict(_VISIBLE)
    if exclude_allergens:
        # Only `contains` is matched. An item whose `may_contain` names an
        # excluded allergen stays in the result and is marked in the
        # listing instead: hiding a cross-contact risk would let the
        # filter read as a safety guarantee (04-WORKFLOWS.md).
        query["allergens.contains"] = {
            "$nin": [code.value for code in exclude_allergens]
        }
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
    exclude_allergens: Sequence[AllergenCode] = (),
) -> list[Dish]:
    cursor = (
        get_db()[COLLECTION]
        .find(
            _visible_query(meal_type_id, preference_flags, exclude_allergens)
        )
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


def list_visible_dishes_by_ids(ids: Sequence[str]) -> list[Dish]:
    """Published dishes for the given ids, in the order they were given.

    The customer-facing read by id. Ids that are malformed, unknown or
    not published are dropped, so the publication rule stays in the query
    rather than being re-checked by a caller.
    """
    wanted: list[str] = []
    for value in ids:
        if isinstance(value, str) and value not in wanted:
            wanted.append(value)
    object_ids = [
        object_id
        for object_id in (to_object_id(value) for value in wanted)
        if object_id is not None
    ]
    if not object_ids:
        return []
    cursor = get_db()[COLLECTION].find({"_id": {"$in": object_ids}, **_VISIBLE})
    found = {str(document["_id"]): document for document in cursor}
    return parse_many(Dish, [found[value] for value in wanted if value in found])


def get_visible_dish_by_slug(slug: str) -> Dish | None:
    return parse_one(
        Dish, get_db()[COLLECTION].find_one({"slug": slug, **_VISIBLE})
    )


# --- chef scope: sees drafts and archived items -----------------------------


def chef_list_dishes(include_archived: bool = False) -> list[Dish]:
    query: dict = {} if include_archived else {"is_archived": False}
    # Name breaks the tie: `sort_order` defaults to 0 across a catalogue
    # nobody has ordered yet, and an editor list that reshuffles between
    # two loads is one the chef cannot use to reorder anything.
    cursor = (
        get_db()[COLLECTION]
        .find(query)
        .sort([("sort_order", ASCENDING), ("name", ASCENDING)])
    )
    return parse_many(Dish, cursor)


def chef_get_dish(dish_id: str) -> Dish | None:
    object_id = to_object_id(dish_id)
    if object_id is None:
        return None
    return parse_one(Dish, get_db()[COLLECTION].find_one({"_id": object_id}))


def chef_create_dish(dish: Dish) -> Dish:
    try:
        result = get_db()[COLLECTION].insert_one(dish.to_mongo())
    except DuplicateKeyError as exc:
        raise SlugTaken(dish.slug) from exc
    return dish.model_copy(update={"id": str(result.inserted_id)})



def chef_list_dishes_by_ids(ids: Sequence[str]) -> dict[str, Dish]:
    """Dishs by id for the chef, keyed by id, whatever their visibility.

    The prep sheet's read. Named `chef_*` because it deliberately sees
    what a customer read would refuse — an archived or withdrawn dish
    can still be on an order placed before it was withdrawn, and the
    kitchen has to make it.

    Ids that are malformed or no longer resolve are simply absent. The
    sheet names the line from its own snapshot in that case rather than
    dropping it, because a line the chef cannot see is a portion that
    does not get cooked.
    """
    object_ids = [
        object_id
        for object_id in (to_object_id(value) for value in set(ids))
        if object_id is not None
    ]
    if not object_ids:
        return {}
    cursor = get_db()[COLLECTION].find({"_id": {"$in": object_ids}})
    return {
        str(document["_id"]): Dish.model_validate(document)
        for document in cursor
    }



class SlugTaken(ValueError):
    """Raised when the unique index on `slug` refuses a write.

    The index is what decides, not a prior read: two saves racing for one
    slug would both pass a check made beforehand.
    """


def chef_update_dish(dish_id: str, fields: dict) -> bool:
    """Write the editor's fields onto one dish. True when it landed.

    Only the fields the editor owns are `$set`. Deliberately never a
    whole-document replace: `allergens` is written by the allergen editor
    alone (01-DOMAIN.md), and a replace here would carry a stale copy of
    it back over a review made in another tab.
    """
    object_id = to_object_id(dish_id)
    if object_id is None:
        return False
    try:
        result = get_db()[COLLECTION].update_one(
            {"_id": object_id}, {"$set": fields}
        )
    except DuplicateKeyError as exc:
        raise SlugTaken(str(fields.get("slug", ""))) from exc
    return result.matched_count == 1


def chef_next_sort_order() -> int:
    """One past the highest `sort_order` in the collection.

    So a newly created item lands at the end of the chef's list rather
    than sharing position 0 with everything else that was never ordered.
    """
    document = get_db()[COLLECTION].find_one(
        {}, sort=[("sort_order", DESCENDING)], projection={"sort_order": 1}
    )
    return int(document.get("sort_order", 0)) + 1 if document else 0


def chef_set_sort_order(dish_id: str, sort_order: int) -> bool:
    """Move one dish in the chef's ordering."""
    object_id = to_object_id(dish_id)
    if object_id is None:
        return False
    result = get_db()[COLLECTION].update_one(
        {"_id": object_id},
        {"$set": {"sort_order": sort_order, "updated_at": utcnow()}},
    )
    return result.matched_count == 1


# --- cart scope: sees a withdrawn item, by id, so a line can still render ---


def list_dishes_for_cart(ids: Sequence[str]) -> list[Dish]:
    """Dishes for cart lines, whatever their visibility, in id order.

    A cart never silently drops a line (04-WORKFLOWS.md). A line whose
    item has since been withdrawn still has to render — by name, struck
    through, blocking checkout until the customer removes it — and that
    needs a document the customer-facing read would refuse to return.

    So this is a separately named function rather than a flag on the
    visible read: the widened scope is visible at the call site, as
    02-ARCHITECTURE.md requires of any scope that is not the default one.
    Only the name of a withdrawn item reaches the customer; the cart
    prices and orders nothing that is not visible.
    """
    wanted: list[str] = []
    for value in ids:
        if isinstance(value, str) and value not in wanted:
            wanted.append(value)
    object_ids = [
        object_id
        for object_id in (to_object_id(value) for value in wanted)
        if object_id is not None
    ]
    if not object_ids:
        return []
    cursor = get_db()[COLLECTION].find({"_id": {"$in": object_ids}})
    found = {str(document["_id"]): document for document in cursor}
    return parse_many(Dish, [found[value] for value in wanted if value in found])
