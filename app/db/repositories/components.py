"""components collection.

The catalogue is chef-owned, not customer-owned, so these functions carry
no `user_id`. Customer-facing reads are constrained by visibility
(`is_available` and not `is_archived`) rather than by tenancy.
"""

from __future__ import annotations

from collections.abc import Sequence

from pymongo import ASCENDING

from app.db.client import get_db
from app.db.repositories._common import parse_many, parse_one, to_object_id
from app.models.allergens import AllergenCode
from app.models.catalogue import Component, ComponentCategory

COLLECTION = "components"

#: The customer-facing predicate. 01-DOMAIN.md derives publication from
#: these two flags plus the allergen review; the review is enforced by the
#: model, so an unreviewed item cannot be `is_available` in the first place.
_VISIBLE = {"is_available": True, "is_archived": False}


def _visible_query(
    category: ComponentCategory | None,
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
    if category is not None:
        query["category"] = category.value
    if preference_flags:
        # Every selected flag must be present: filters compose as AND, so
        # 'vegan + high protein' means both, not either.
        query["preference_flags"] = {"$all": list(preference_flags)}
    return query


def list_visible_components(
    *,
    category: ComponentCategory | None = None,
    preference_flags: Sequence[str] = (),
    exclude_allergens: Sequence[AllergenCode] = (),
) -> list[Component]:
    cursor = (
        get_db()[COLLECTION]
        .find(_visible_query(category, preference_flags, exclude_allergens))
        .sort([("sort_order", ASCENDING), ("name", ASCENDING)])
    )
    return parse_many(Component, cursor)


def visible_component_categories() -> list[ComponentCategory]:
    """Categories that currently have at least one visible component.

    Read from the data rather than from the enum so the filter strip never
    offers a choice that returns an empty page.
    """
    values = get_db()[COLLECTION].distinct("category", dict(_VISIBLE))
    known = [category for category in ComponentCategory if category.value in values]
    return known


def visible_component_preference_flags() -> list[str]:
    """Preference flags in use across the visible catalogue.

    Flags are chef-extensible (01-DOMAIN.md), so the filter strip is built
    from the data; a flag the chef invents appears without a code change.
    """
    values = get_db()[COLLECTION].distinct("preference_flags", dict(_VISIBLE))
    return sorted(value for value in values if isinstance(value, str))


def list_visible_components_by_ids(ids: Sequence[str]) -> list[Component]:
    """Visible components for the given ids, in the order they were given.

    Used for a dish's provenance links. Ids that are malformed, unknown or
    not published are dropped rather than reported: a customer following a
    dead provenance link is worse than not seeing the link at all. Order
    is the caller's authored order, and repeats collapse, so the same dish
    always renders the same list.
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
    return parse_many(Component, [found[value] for value in wanted if value in found])


def get_visible_component_by_slug(slug: str) -> Component | None:
    return parse_one(
        Component, get_db()[COLLECTION].find_one({"slug": slug, **_VISIBLE})
    )


# --- chef scope: sees drafts and archived items -----------------------------


def chef_list_components(include_archived: bool = False) -> list[Component]:
    query: dict = {} if include_archived else {"is_archived": False}
    cursor = get_db()[COLLECTION].find(query).sort("sort_order", ASCENDING)
    return parse_many(Component, cursor)


def chef_get_component(component_id: str) -> Component | None:
    object_id = to_object_id(component_id)
    if object_id is None:
        return None
    return parse_one(Component, get_db()[COLLECTION].find_one({"_id": object_id}))


def chef_create_component(component: Component) -> Component:
    result = get_db()[COLLECTION].insert_one(component.to_mongo())
    return component.model_copy(update={"id": str(result.inserted_id)})
