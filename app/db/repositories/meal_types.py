"""meal_types collection. Chef-owned, ordered, renameable."""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from app.db.client import get_db
from app.db.repositories._common import parse_many, parse_one, to_object_id
from app.models.base import utcnow
from app.models.catalogue import MealType

COLLECTION = "meal_types"


def list_meal_types() -> list[MealType]:
    # Name breaks the tie. `sort_order` defaults to 0 across a collection
    # nobody has ordered yet, and reordering is a swap of two positions —
    # which needs the list it reads to be in the same order twice running,
    # or the chef moves one label and a different one moves.
    cursor = (
        get_db()[COLLECTION]
        .find({})
        .sort([("sort_order", ASCENDING), ("name", ASCENDING)])
    )
    return parse_many(MealType, cursor)


def get_meal_type_by_slug(slug: str) -> MealType | None:
    return parse_one(MealType, get_db()[COLLECTION].find_one({"slug": slug}))


# --- chef scope -------------------------------------------------------------
#
# A meal type is a navigation label, not a sellable item: there is no
# `is_archived` on `MealType` and there is no visibility predicate over
# this collection, so the chef reads exactly what a customer's `/menu`
# resolves against. These are named `chef_*` all the same, because they
# write, and 02-ARCHITECTURE.md wants the scope visible at the call site.


class SlugTaken(ValueError):
    """Raised when the unique index on `slug` refuses a write.

    The index is what decides, not a prior read: two saves racing for one
    slug would both pass a check made beforehand.
    """


def chef_get_meal_type(meal_type_id: str) -> MealType | None:
    object_id = to_object_id(meal_type_id)
    if object_id is None:
        return None
    return parse_one(MealType, get_db()[COLLECTION].find_one({"_id": object_id}))


def chef_create_meal_type(meal_type: MealType) -> MealType:
    try:
        result = get_db()[COLLECTION].insert_one(meal_type.to_mongo())
    except DuplicateKeyError as exc:
        raise SlugTaken(meal_type.slug) from exc
    return meal_type.model_copy(update={"id": str(result.inserted_id)})


def chef_update_meal_type(meal_type_id: str, fields: dict) -> bool:
    """Write the editor's fields onto one meal type. True when it landed.

    An explicit `$set` of the named fields rather than a replace, for the
    same reason the catalogue editors use one: a replace carries back
    whatever the caller happened to be holding, including a `sort_order`
    another tab has since moved.
    """
    object_id = to_object_id(meal_type_id)
    if object_id is None:
        return False
    try:
        result = get_db()[COLLECTION].update_one({"_id": object_id}, {"$set": fields})
    except DuplicateKeyError as exc:
        raise SlugTaken(str(fields.get("slug", ""))) from exc
    return result.matched_count == 1


def chef_delete_meal_type(meal_type_id: str) -> bool:
    """Remove one meal type. True when a document was actually removed.

    The only hard delete in the chef area, and it is deliberate: a meal
    type carries no content of its own, so there is nothing to preserve
    and nothing an archive flag would protect. What protects `/menu` from
    a dead link is the service's refusal to call this while a dish still
    references the type — see `meal_type_admin.delete`.
    """
    object_id = to_object_id(meal_type_id)
    if object_id is None:
        return False
    return get_db()[COLLECTION].delete_one({"_id": object_id}).deleted_count == 1


def chef_next_sort_order() -> int:
    """One past the highest `sort_order` in the collection.

    So a new meal type lands at the end of the chef's order rather than
    sharing position 0 with everything created before anybody ordered
    anything.
    """
    document = get_db()[COLLECTION].find_one(
        {}, sort=[("sort_order", DESCENDING)], projection={"sort_order": 1}
    )
    return int(document.get("sort_order", 0)) + 1 if document else 0


def chef_set_sort_order(meal_type_id: str, sort_order: int) -> bool:
    """Move one meal type in the chef's ordering."""
    object_id = to_object_id(meal_type_id)
    if object_id is None:
        return False
    result = get_db()[COLLECTION].update_one(
        {"_id": object_id},
        {"$set": {"sort_order": sort_order, "updated_at": utcnow()}},
    )
    return result.matched_count == 1
