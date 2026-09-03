"""Catalogue models: the shared item shape, plus Component and Dish.

`components` and `dishes` are separate catalogues with separate browse
pages, editors and ordering flows. A dish may reference components for
provenance only — a dish's own four tabs are authoritative and are never
merged with, or overridden by, a referenced component (01-DOMAIN.md).
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from app.models.allergens import AllergenBlock
from app.models.base import EmbeddedModel, TimestampedModel


class ComponentCategory(str, Enum):
    DRESSING = "dressing"
    SAUCE = "sauce"
    PUREE = "puree"
    SIDE = "side"
    PROTEIN = "protein"
    BASE = "base"
    OTHER = "other"


class Unit(str, Enum):
    EACH = "each"
    PER_100G = "100g"
    PORTION = "portion"
    PER_250ML = "250ml"


class StorageMethod(str, Enum):
    REFRIGERATE = "refrigerate"
    FREEZE = "freeze"
    AMBIENT = "ambient"
    REHEAT_FROM_FROZEN = "reheat_from_frozen"


class ReheatMethod(str, Enum):
    OVEN = "oven"
    MICROWAVE = "microwave"
    PAN = "pan"
    NONE = "none"


#: Controlled but chef-extensible. Preference data is taste metadata and is
#: kept out of the allergen block so compliance labelling is never diluted.
KNOWN_PREFERENCE_FLAGS: tuple[str, ...] = (
    "chilli",
    "garlic",
    "coriander",
    "onion",
    "dairy_free",
    "vegetarian",
    "vegan",
    "high_protein",
    "low_carb",
)


class Ingredient(EmbeddedModel):
    """One ingredient line. Displayed in authored order, never sorted."""

    name: str = Field(min_length=1)
    quantity: str | None = None
    note: str | None = None
    is_optional: bool = False


class StorageBlock(EmbeddedModel):
    method: StorageMethod
    temperature_c: str
    shelf_life_days: int = Field(ge=0)
    shelf_life_note: str | None = None
    freezable: bool = False
    freezer_life_days: int | None = Field(default=None, ge=0)


class PreparationBlock(EmbeddedModel):
    steps: list[str] = Field(default_factory=list)
    reheat_method: ReheatMethod | None = None
    reheat_minutes: int | None = Field(default=None, ge=0)
    reheat_note: str | None = None
    serving_suggestion: str | None = None


class ItemBase(TimestampedModel):
    """Fields shared by every sellable item in either catalogue."""

    name: str = Field(min_length=1)
    slug: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    summary: str = ""
    description: str = ""
    category: str = ""
    image_path: str | None = None
    price_cents: int = Field(ge=0)
    unit: Unit = Unit.EACH
    is_available: bool = False
    is_archived: bool = False
    sort_order: int = 0

    # --- the four tabs ---
    ingredients: list[Ingredient] = Field(default_factory=list)
    allergens: AllergenBlock = Field(default_factory=AllergenBlock)
    storage: StorageBlock | None = None
    preparation: PreparationBlock | None = None

    # --- non-compliance taste metadata ---
    preference_flags: list[str] = Field(default_factory=list)
    spice_level: int = Field(default=0, ge=0, le=5)

    @property
    def is_visible_to_customers(self) -> bool:
        return self.is_available and not self.is_archived

    @model_validator(mode="after")
    def _unreviewed_items_cannot_be_published(self) -> "ItemBase":
        """An item with no allergen review cannot be published.

        Publication is `is_available and not is_archived`; there is no
        separate published flag in the domain model. An item may be
        drafted without a review, but not made visible to customers.
        """
        if self.is_visible_to_customers and not self.allergens.is_reviewed:
            raise ValueError(
                "item cannot be made available to customers until its "
                "allergen block has been reviewed"
            )
        return self


class Component(ItemBase):
    category: ComponentCategory = ComponentCategory.OTHER


class Dish(ItemBase):
    meal_type_ids: list[str] = Field(default_factory=list)
    component_refs: list[str] = Field(default_factory=list)
    serves: int = Field(default=1, ge=1)


class MealType(TimestampedModel):
    name: str = Field(min_length=1)
    slug: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    sort_order: int = 0
