"""Allergen declaration — a compliance surface, not a content field.

Australian service. Vocabulary follows FSANZ Standard 1.2.3 / PEAL as
recorded in 01-DOMAIN.md. Controlled vocabulary only: there is no
free-text allergen entry, and nothing in this module infers, defaults or
derives a declaration.

The enum must be re-verified against the current text of Standard 1.2.3
before go-live; this file is a starting point, not a legal source.
"""

from __future__ import annotations

from collections.abc import Iterable
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


def declaration_labels(
    codes: Iterable[AllergenCode],
    *,
    gluten_cereals: Iterable[GlutenCereal] = (),
    tree_nut_species: Iterable[TreeNutSpecies] = (),
) -> list[str]:
    """Display strings for a set of declared allergens, species inline.

    03-FRONTEND.md requires the gluten cereals and the tree nut species to
    read inside their own chip and forbids abbreviating an allergen name.
    The wording is built here rather than in Jinja so it stays under test,
    and it is a module function rather than only a property because the
    chef's order queue renders the same declaration rolled up across an
    order's lines — one compliance string, written once.
    """
    detail = {
        AllergenCode.CEREALS_GLUTEN: [
            GLUTEN_CEREAL_LABELS[cereal] for cereal in gluten_cereals
        ],
        AllergenCode.TREE_NUTS: [
            TREE_NUT_LABELS[species] for species in tree_nut_species
        ],
    }
    labels = []
    for code in codes:
        named = detail.get(code)
        label = ALLERGEN_LABELS[code]
        labels.append(f"{label} ({', '.join(named)})" if named else label)
    return labels


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
    def contains_labels(self) -> list[str]:
        """Display strings for `contains`, species named inline."""
        return declaration_labels(
            self.contains,
            gluten_cereals=self.gluten_cereals,
            tree_nut_species=self.tree_nut_species,
        )

    @property
    def sulphites_threshold_note(self) -> str | None:
        """Threshold wording for a declared sulphites entry, or None.

        `sulphites_declared` records that the level reaches the 10 mg/kg
        labelling threshold; `contains` is what actually declares the
        allergen. The note therefore qualifies an existing declaration and
        never stands in for one — a block whose flag is set without the
        matching `contains` entry is a data defect for the chef allergen
        editor to prevent, not something a customer page invents a
        declaration to cover. The cross-validator that would prevent it is
        deferred to that editor and recorded in 04-WORKFLOWS.md.
        """
        if not (self.sulphites_declared and AllergenCode.SULPHITES in self.contains):
            return None
        return "Sulphites are present at 10 mg/kg or above."

    @property
    def may_contain_labels(self) -> list[str]:
        """Display strings for `may_contain`.

        Species detail belongs to a positive declaration only, so a
        cross-contact entry carries the allergen name alone.
        """
        return [ALLERGEN_LABELS[code] for code in self.may_contain]

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
