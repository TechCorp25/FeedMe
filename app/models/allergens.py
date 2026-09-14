"""Allergen declaration — a compliance surface, not a content field.

Australian service. Vocabulary follows FSANZ Standard 1.2.3 / PEAL as
recorded in 01-DOMAIN.md. Controlled vocabulary only: there is no
free-text allergen entry, and nothing in this module infers, defaults or
derives a declaration.

The enum was re-verified against the table to S9—3 of Schedule 9 on
12 September 2026 and the two discrepancies it found are closed here:
`WHEAT` and `GLUTEN` are the two declarations column 4 actually names,
and `spelt` is not a food in the table — it is a *Triticum* hybrid, so
its required name is `wheat`. The record is in 01-DOMAIN.md under
"Verification record".

`CEREALS_GLUTEN` and `GlutenCereal.SPELT` remain in the enums and are
still parsed, because both are frozen into `OrderLine.allergen_snapshot`
on orders already placed and a snapshot is never rewritten. They are
never written again: `WRITABLE_CODES` and `WRITABLE_GLUTEN_CEREALS` are
what the allergen editor offers and accepts. A declaration a customer was
given is what they were given, whatever the vocabulary has since become.

This file is still not a legal source.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import Enum

from pydantic import Field, model_validator

from app.models.base import EmbeddedModel


class AllergenCode(str, Enum):
    """Declarable allergens. Crustacea, mollusc and fish stay separate.

    `wheat` and `gluten` are two declarations, not one. Schedule 9 item 3
    makes wheat declarable *irrespective of whether it contains gluten*,
    with the required name `wheat` and `gluten` as well where gluten is
    present; item 2 makes barley, oats and rye declarable only if they
    contain gluten, with the required name `gluten`. A single
    `cereals_gluten` code cannot express wheat without gluten and renders
    a name the table does not use.
    """

    WHEAT = "wheat"
    GLUTEN = "gluten"
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

    # --- retired vocabulary: read, never written ---------------------------
    #
    # Superseded by WHEAT and GLUTEN. Kept so every historical
    # `allergen_snapshot` still parses. Last in the enum so it sorts after
    # the live vocabulary wherever declaration order is taken from it.
    CEREALS_GLUTEN = "cereals_gluten"


class GlutenCereal(str, Enum):
    WHEAT = "wheat"
    RYE = "rye"
    BARLEY = "barley"
    OATS = "oats"

    # --- retired vocabulary: read, never written ---------------------------
    #
    # Spelt is of the genus *Triticum*, so Schedule 9 item 3 covers it and
    # its required name is `wheat`. Kept only so stored blocks parse.
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


#: The codes a declaration may still be written with. The allergen editor
#: offers these and accepts nothing else; everything outside the set is
#: readable history.
LEGACY_CODES: frozenset[AllergenCode] = frozenset({AllergenCode.CEREALS_GLUTEN})
WRITABLE_CODES: tuple[AllergenCode, ...] = tuple(
    code for code in AllergenCode if code not in LEGACY_CODES
)

LEGACY_GLUTEN_CEREALS: frozenset[GlutenCereal] = frozenset({GlutenCereal.SPELT})
WRITABLE_GLUTEN_CEREALS: tuple[GlutenCereal, ...] = tuple(
    cereal for cereal in GlutenCereal if cereal not in LEGACY_GLUTEN_CEREALS
)

#: What a retired code is read as when a *live* code is used to look for
#: it. A stored `cereals_gluten` is a declaration of gluten, and the
#: cereal it names may be wheat, so both live codes have to find it.
#:
#: Used only by the browse filter, which is a browsing aid and not a
#: safety guarantee (04-WORKFLOWS.md): it makes an exclusion cover more
#: than it strictly names, which is the safe direction for a filter to
#: fail. It never rewrites a declaration and never renders one.
_RETIRED_EQUIVALENTS: dict[AllergenCode, tuple[AllergenCode, ...]] = {
    AllergenCode.GLUTEN: (AllergenCode.CEREALS_GLUTEN,),
    AllergenCode.WHEAT: (AllergenCode.CEREALS_GLUTEN,),
}


def codes_matching(codes: Iterable[AllergenCode]) -> list[AllergenCode]:
    """Every stored code these live codes should match, in enum order.

    A catalogue item reviewed before the vocabulary split still carries
    `cereals_gluten`, and a customer excluding gluten — or wheat — means
    to exclude it. Returned in enum order so the query is stable.
    """
    wanted = set(codes)
    for code in list(wanted):
        wanted.update(_RETIRED_EQUIVALENTS.get(code, ()))
    return [code for code in AllergenCode if code in wanted]


#: Human-readable labels. Never abbreviate an allergen name in the UI
#: (03-FRONTEND.md), so the display string lives with the vocabulary.
#: Each is the required name from column 4 of the table to S9—3, which is
#: the column that governs a declaration made outside a statement of
#: ingredients — which is what this application renders.
ALLERGEN_LABELS: dict[AllergenCode, str] = {
    AllergenCode.WHEAT: "Wheat",
    AllergenCode.GLUTEN: "Gluten",
    # The required name is "crustacean", not the taxon.
    AllergenCode.CRUSTACEA: "Crustacean",
    # S9—3(2)(c) defines mollusc as a marine mollusc, so the label says so
    # rather than leaving a customer to assume a snail in the garden
    # counts.
    AllergenCode.MOLLUSC: "Marine mollusc",
    AllergenCode.EGG: "Egg",
    AllergenCode.FISH: "Fish",
    AllergenCode.MILK: "Milk",
    AllergenCode.PEANUT: "Peanut",
    AllergenCode.SESAME: "Sesame",
    AllergenCode.SOY: "Soy",
    AllergenCode.TREE_NUTS: "Tree nuts",
    AllergenCode.LUPIN: "Lupin",
    AllergenCode.SULPHITES: "Sulphites",
    # Retired. Still rendered, because an order placed before the split
    # carries it and its page must keep saying what the customer was told.
    AllergenCode.CEREALS_GLUTEN: "Cereals containing gluten",
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
    cereals = [GLUTEN_CEREAL_LABELS[cereal] for cereal in gluten_cereals]
    detail = {
        AllergenCode.GLUTEN: cereals,
        # The retired code carried the same detail, and a snapshot that
        # still uses it must keep reading the way it did when it was made.
        AllergenCode.CEREALS_GLUTEN: cereals,
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

    @property
    def uses_retired_vocabulary(self) -> bool:
        """True when this block still carries a code the editor retired.

        A block written before the wheat/gluten split, or a snapshot
        frozen onto an order then. It renders exactly as it was written —
        the declaration is what the customer was given — but the chef
        editor says so and a re-review replaces it with the live
        vocabulary.
        """
        return bool(
            LEGACY_CODES & set(self.contains)
            or LEGACY_CODES & set(self.may_contain)
            or LEGACY_GLUTEN_CEREALS & set(self.gluten_cereals)
        )

    @model_validator(mode="after")
    def _check_declaration(self) -> "AllergenBlock":
        declares_gluten = {AllergenCode.GLUTEN, AllergenCode.CEREALS_GLUTEN} & set(
            self.contains
        )
        if declares_gluten and not self.gluten_cereals:
            raise ValueError(
                "gluten_cereals must name at least one cereal when "
                "'gluten' is declared in contains"
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
