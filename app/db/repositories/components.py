"""components collection.

The catalogue is chef-owned, not customer-owned, so these functions carry
no `user_id`. Customer-facing reads are constrained by visibility
(`is_available` and not `is_archived`) rather than by tenancy.
"""

from __future__ import annotations

from pymongo import ASCENDING

from app.db.client import get_db
from app.db.repositories._common import parse_many, parse_one, to_object_id
from app.models.catalogue import Component

COLLECTION = "components"

_VISIBLE = {"is_available": True, "is_archived": False}


def list_visible_components() -> list[Component]:
    cursor = get_db()[COLLECTION].find(dict(_VISIBLE)).sort("sort_order", ASCENDING)
    return parse_many(Component, cursor)


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
