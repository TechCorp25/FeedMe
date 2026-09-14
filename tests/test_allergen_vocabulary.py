"""The wheat/gluten split, and the vocabulary it retired.

01-DOMAIN.md's Verification record checked `AllergenCode` against the
table to S9—3 of Schedule 9 and found two discrepancies. Both are closed
here: column 4 names *wheat* and *gluten* as two declarations where this
application had one, and spelt is a *Triticum* hybrid whose required name
is `wheat` rather than a food the table names.

Neither retired value is deleted. Both are frozen into
`OrderLine.allergen_snapshot` on orders already placed, and a snapshot is
never rewritten — so they parse and render exactly as they did, and they
are never written again.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.allergens import (
    ALLERGEN_LABELS,
    LEGACY_CODES,
    LEGACY_GLUTEN_CEREALS,
    WRITABLE_CODES,
    WRITABLE_GLUTEN_CEREALS,
    AllergenBlock,
    AllergenCode,
    GlutenCereal,
    codes_matching,
    declaration_labels,
)

REVIEWED = {
    "reviewed_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef@example.com",
}


# --- the split itself -------------------------------------------------------


def test_wheat_is_declarable_without_gluten():
    """Item 3: wheat is declarable irrespective of whether it has gluten.

    The whole point of the split. `cereals_gluten` could not say this —
    declaring it asserted gluten, and there was no other way to name
    wheat at all.
    """
    block = AllergenBlock(contains=[AllergenCode.WHEAT], **REVIEWED)

    assert block.contains_labels == ["Wheat"]
    assert block.gluten_cereals == []


def test_wheat_and_gluten_are_declared_together_when_gluten_is_present():
    """Column 4: `wheat`; and `gluten` as well, if gluten is present."""
    block = AllergenBlock(
        contains=[AllergenCode.WHEAT, AllergenCode.GLUTEN],
        gluten_cereals=[GlutenCereal.WHEAT],
        **REVIEWED,
    )

    assert block.contains_labels == ["Wheat", "Gluten (wheat)"]


def test_gluten_alone_covers_barley_oats_and_rye():
    """Item 2: declarable only if they contain gluten, named `gluten`."""
    block = AllergenBlock(
        contains=[AllergenCode.GLUTEN],
        gluten_cereals=[GlutenCereal.BARLEY, GlutenCereal.OATS],
        **REVIEWED,
    )

    assert block.contains_labels == ["Gluten (barley, oats)"]


def test_gluten_requires_named_cereals():
    with pytest.raises(ValidationError, match="gluten_cereals"):
        AllergenBlock(contains=[AllergenCode.GLUTEN])


def test_spelt_is_not_offered_as_a_cereal():
    """Spelt is *Triticum*, so its required name is `wheat`."""
    assert GlutenCereal.SPELT in LEGACY_GLUTEN_CEREALS
    assert GlutenCereal.SPELT not in WRITABLE_GLUTEN_CEREALS
    assert GlutenCereal.WHEAT in WRITABLE_GLUTEN_CEREALS


# --- the labels column 4 actually uses --------------------------------------


def test_required_names_match_column_four():
    """`crustacean`, not the taxon; mollusc means a *marine* mollusc."""
    assert ALLERGEN_LABELS[AllergenCode.CRUSTACEA] == "Crustacean"
    assert ALLERGEN_LABELS[AllergenCode.MOLLUSC] == "Marine mollusc"


def test_crustacean_mollusc_and_fish_stay_three_declarations():
    labels = declaration_labels(
        [AllergenCode.CRUSTACEA, AllergenCode.MOLLUSC, AllergenCode.FISH]
    )
    assert labels == ["Crustacean", "Marine mollusc", "Fish"]


# --- what was retired -------------------------------------------------------


def test_retired_codes_are_not_writable():
    assert AllergenCode.CEREALS_GLUTEN in LEGACY_CODES
    assert AllergenCode.CEREALS_GLUTEN not in WRITABLE_CODES
    assert AllergenCode.WHEAT in WRITABLE_CODES
    assert AllergenCode.GLUTEN in WRITABLE_CODES


def test_a_snapshot_written_before_the_split_still_parses_and_reads():
    """A declaration a customer was given is what they were given."""
    block = AllergenBlock.model_validate(
        {
            "contains": ["cereals_gluten"],
            "gluten_cereals": ["spelt"],
            **REVIEWED,
        }
    )

    assert block.contains == [AllergenCode.CEREALS_GLUTEN]
    assert block.contains_labels == ["Cereals containing gluten (spelt)"]
    assert block.uses_retired_vocabulary is True


def test_a_block_in_the_live_vocabulary_is_not_flagged_as_retired():
    block = AllergenBlock(
        contains=[AllergenCode.WHEAT, AllergenCode.GLUTEN],
        gluten_cereals=[GlutenCereal.WHEAT],
        **REVIEWED,
    )
    assert block.uses_retired_vocabulary is False


def test_a_retired_cereal_alone_still_flags_the_block():
    block = AllergenBlock(
        contains=[AllergenCode.GLUTEN],
        gluten_cereals=[GlutenCereal.SPELT],
        **REVIEWED,
    )
    assert block.uses_retired_vocabulary is True


# --- looking for a retired code with a live one -----------------------------


@pytest.mark.parametrize("code", [AllergenCode.GLUTEN, AllergenCode.WHEAT])
def test_a_live_code_matches_the_retired_code_it_replaced(code):
    assert AllergenCode.CEREALS_GLUTEN in codes_matching([code])


def test_matching_is_in_enum_order_and_widens_nothing_else():
    assert codes_matching([AllergenCode.PEANUT]) == [AllergenCode.PEANUT]
    assert codes_matching([AllergenCode.GLUTEN]) == [
        AllergenCode.GLUTEN,
        AllergenCode.CEREALS_GLUTEN,
    ]
