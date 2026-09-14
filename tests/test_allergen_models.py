"""Allergen validators.

Allergen data is a compliance surface: these rules are enforced by the
model, so no code path can write an incomplete declaration.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.allergens import (
    AllergenBlock,
    AllergenCode,
    GlutenCereal,
    TreeNutSpecies,
)
from app.models.catalogue import Component

REVIEWED = {
    "reviewed_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef@example.com",
}


def test_gluten_requires_named_cereals():
    with pytest.raises(ValidationError, match="gluten_cereals"):
        AllergenBlock(contains=[AllergenCode.CEREALS_GLUTEN])


def test_gluten_accepted_when_cereals_named():
    block = AllergenBlock(
        contains=[AllergenCode.CEREALS_GLUTEN],
        gluten_cereals=[GlutenCereal.WHEAT, GlutenCereal.BARLEY],
    )
    assert block.gluten_cereals == [GlutenCereal.WHEAT, GlutenCereal.BARLEY]


def test_tree_nuts_require_named_species():
    with pytest.raises(ValidationError, match="tree_nut_species"):
        AllergenBlock(contains=[AllergenCode.TREE_NUTS])


def test_tree_nuts_accepted_when_species_named():
    block = AllergenBlock(
        contains=[AllergenCode.TREE_NUTS],
        tree_nut_species=[TreeNutSpecies.MACADAMIA],
    )
    assert block.tree_nut_species == [TreeNutSpecies.MACADAMIA]


@pytest.mark.parametrize(
    "code",
    [AllergenCode.CRUSTACEA, AllergenCode.MOLLUSC, AllergenCode.FISH],
)
def test_shellfish_and_fish_are_separate_declarations(code):
    """Crustacea, mollusc and fish are never collapsed into one another."""
    block = AllergenBlock(contains=[code])
    assert block.contains == [code]
    others = {AllergenCode.CRUSTACEA, AllergenCode.MOLLUSC, AllergenCode.FISH} - {code}
    assert not others & set(block.contains)


def test_free_text_is_not_an_allergen():
    with pytest.raises(ValidationError):
        AllergenBlock(contains=["shellfish"])


def test_allergen_cannot_be_both_contains_and_may_contain():
    with pytest.raises(ValidationError, match="cannot be both"):
        AllergenBlock(contains=[AllergenCode.EGG], may_contain=[AllergenCode.EGG])


def test_reviewed_at_requires_a_reviewer():
    with pytest.raises(ValidationError, match="reviewed_by"):
        AllergenBlock(reviewed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_unreviewed_block_never_declares_nothing():
    """'No declared allergens' is gated on review, not on an empty list."""
    unreviewed = AllergenBlock()
    assert unreviewed.is_reviewed is False
    assert unreviewed.declares_nothing is False

    reviewed_empty = AllergenBlock(**REVIEWED)
    assert reviewed_empty.declares_nothing is True


def test_unreviewed_item_cannot_be_published():
    with pytest.raises(ValidationError, match="allergen block has been reviewed"):
        Component(
            name="Harissa",
            slug="harissa",
            price_cents=650,
            is_available=True,
        )


def test_unreviewed_item_may_exist_as_a_draft():
    draft = Component(name="Harissa", slug="harissa", price_cents=650)
    assert draft.is_available is False
    assert draft.is_visible_to_customers is False


def test_reviewed_item_can_be_published():
    item = Component(
        name="Harissa",
        slug="harissa",
        price_cents=650,
        is_available=True,
        allergens=AllergenBlock(contains=[AllergenCode.SESAME], **REVIEWED),
    )
    assert item.is_visible_to_customers is True


def test_preference_flags_are_not_allergens():
    """Taste metadata lives outside the allergen block entirely."""
    item = Component(
        name="Chilli oil",
        slug="chilli-oil",
        price_cents=500,
        preference_flags=["chilli", "vegan"],
        spice_level=4,
    )
    assert item.allergens.contains == []
    assert "chilli" not in [code.value for code in item.allergens.contains]
    assert item.preference_flags == ["chilli", "vegan"]


# --- sulphites: the flag and the entry are one declaration ------------------
#
# 04-WORKFLOWS.md deferred this cross-validator to the allergen editor and
# asked for a direction. It is a biconditional, and it raises rather than
# filling either half in: adding the `contains` entry would author a
# declaration the chef did not make, which 00-SYSTEM.md forbids outright,
# and it is asymmetric besides — unticking the flag could not retract the
# entry without the same inference in reverse.


def test_the_flag_without_the_contains_entry_is_refused():
    """The defect that was previously invisible to everyone.

    The customer page deliberately renders nothing for a block in this
    state, so nothing on any surface reported it.
    """
    with pytest.raises(ValidationError, match="sulphites"):
        AllergenBlock(sulphites_declared=True, **REVIEWED)


def test_the_contains_entry_without_the_flag_is_refused():
    """Equally incoherent: Schedule 9 item 1 makes sulphites declarable
    only at 10 mg/kg or above, so the entry asserts the threshold."""
    with pytest.raises(ValidationError, match="sulphites"):
        AllergenBlock(
            contains=[AllergenCode.SULPHITES],
            sulphites_declared=False,
            **REVIEWED,
        )


def test_both_halves_together_are_accepted_and_carry_the_qualifier():
    block = AllergenBlock(
        contains=[AllergenCode.SULPHITES],
        sulphites_declared=True,
        **REVIEWED,
    )

    assert block.contains_labels == ["Sulphites"]
    assert block.sulphites_threshold_note == (
        "Sulphites are present at 10 mg/kg or above."
    )


def test_neither_half_is_the_ordinary_case():
    block = AllergenBlock(contains=[AllergenCode.EGG], **REVIEWED)

    assert block.sulphites_declared is False
    assert block.sulphites_threshold_note is None


def test_sulphites_as_a_cross_contact_risk_does_not_set_the_flag():
    """`may_contain` is not a declaration, so it does not assert a level."""
    block = AllergenBlock(may_contain=[AllergenCode.SULPHITES], **REVIEWED)

    assert block.sulphites_declared is False
    assert block.may_contain_labels == ["Sulphites"]
