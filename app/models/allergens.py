"""Allergen declaration — a compliance surface, not a content field.

Australian service. Vocabulary follows FSANZ Standard 1.2.3 / PEAL as
recorded in 01-DOMAIN.md. Controlled vocabulary only: there is no
free-text allergen entry, and nothing in this module infers, defaults or
derives a declaration.

The enum must be re-verified against the current text of Standard 1.2.3
before go-live; this file is a starting point, not a legal source.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, model_validator

from app.models.base import EmbeddedModel


class AllergenCode(str, Enum):
    """Declarable allergens. Crustacea, mollusc and fish stay separate."""

    CEREALS_GLUTEN = "cereals_gluten"
    CRUSTACEA = "crustacea"
    MOLLUSC = "mollusc"
    EGG = "egg"
    FISH = "fish"
    MILK = "milk"
    PEANUT = "peanut"
    SESAME = "sesame"
    SOY = "soy"
    TREE_NUTS = "tree_nuts"
    LUPIN = "lupin"
    SULPHITES = "sulphites"


class GlutenCereal(str, Enum):
    WHEAT = "wheat"
    RYE = "rye"
    BARLEY = "barley"
    OATS = "oats"
    SPELT = "spelt"


class TreeNutSpecies(str, Enum):
    ALMOND = "almond"
    BRAZIL = "brazil"
    CASHEW = "cashew"
    HAZELNUT = "hazelnut"
    MACADAMIA = "macadamia"
    PECAN = "pecan"
    PINE_NUT = "pine_nut"
    PISTACHIO = "pistachio"
    WALNUT = "walnut"


#: Human-readable labels. Never abbreviate an allergen name in the UI
#: (03-FRONTEND.md), so the display string lives with the vocabulary.
ALLERGEN_LABELS: dict[AllergenCode, str] = {
    AllergenCode.CEREALS_GLUTEN: "Cereals containing gluten",
    AllergenCode.CRUSTACEA: "Crustacea",
    AllergenCode.MOLLUSC: "Mollusc",
    AllergenCode.EGG: "Egg",
    AllergenCode.FISH: "Fish",
    AllergenCode.MILK: "Milk",
    AllergenCode.PEANUT: "Peanut",
    AllergenCode.SESAME: "Sesame",
    AllergenCode.SOY: "Soy",
    AllergenCode.TREE_NUTS: "Tree nuts",
    AllergenCode.LUPIN: "Lupin",
    AllergenCode.SULPHITES: "Sulphites",
}

GLUTEN_CEREAL_LABELS: dict[GlutenCereal, str] = {
    GlutenCereal.WHEAT: "wheat",
    GlutenCereal.RYE: "rye",
    GlutenCereal.BARLEY: "barley",
    GlutenCereal.OATS: "oats",
    GlutenCereal.SPELT: "spelt",
}

TREE_NUT_LABELS: dict[TreeNutSpecies, str] = {
    TreeNutSpecies.ALMOND: "almond",
    TreeNutSpecies.BRAZIL: "brazil nut",
    TreeNutSpecies.CASHEW: "cashew",
    TreeNutSpecies.HAZELNUT: "hazelnut",
    TreeNutSpecies.MACADAMIA: "macadamia",
    TreeNutSpecies.PECAN: "pecan",
    TreeNutSpecies.PINE_NUT: "pine nut",
    TreeNutSpecies.PISTACHIO: "pistachio",
    TreeNutSpecies.WALNUT: "walnut",
}


class AllergenBlock(EmbeddedModel):
    """One item's allergen declaration.

    Only the chef allergen editor writes this block. No other code path
    may modify it, and no code path may populate it by inference.
    """

    contains: list[AllergenCode] = Field(default_factory=list)
    may_contain: list[AllergenCode] = Field(default_factory=list)
    gluten_cereals: list[GlutenCereal] = Field(default_factory=list)
    tree_nut_species: list[TreeNutSpecies] = Field(default_factory=list)
    sulphites_declared: bool = False
    chef_note: str | None = None
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None

    @property
    def is_reviewed(self) -> bool:
        return self.reviewed_at is not None

    @property
    def declares_nothing(self) -> bool:
        """True only for a reviewed block with an empty `contains` list.

        The 'No declared allergens' phrasing is gated on this. An
        unreviewed item must never render it (01-DOMAIN.md).
        """
        return self.is_reviewed and not self.contains

    @model_validator(mode="after")
    def _check_declaration(self) -> "AllergenBlock":
        if AllergenCode.CEREALS_GLUTEN in self.contains and not self.gluten_cereals:
            raise ValueError(
                "gluten_cereals must name at least one cereal when "
                "'cereals_gluten' is declared in contains"
            )
        if AllergenCode.TREE_NUTS in self.contains and not self.tree_nut_species:
            raise ValueError(
                "tree_nut_species must name at least one species when "
                "'tree_nuts' is declared in contains"
            )
        if self.reviewed_at is not None and not self.reviewed_by:
            raise ValueError("reviewed_by is required once reviewed_at is set")
        duplicated = set(self.contains) & set(self.may_contain)
        if duplicated:
            raise ValueError(
                "an allergen cannot be both contains and may_contain: "
                + ", ".join(sorted(code.value for code in duplicated))
            )
        return self
