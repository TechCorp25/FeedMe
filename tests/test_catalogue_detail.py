"""The tabbed item view.

All four panels are in the served HTML, the allergen tab obeys the
compliance rules in 01-DOMAIN.md and 03-FRONTEND.md, and none of it
depends on JavaScript.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from app.models.catalogue import Component

REVIEWED_AT = datetime(2026, 3, 1, tzinfo=timezone.utc)


def make_component(**overrides) -> Component:
    data = {
        "name": "Harissa",
        "slug": "harissa",
        "summary": "Smoky roasted chilli paste.",
        "description": "Roasted red peppers, dried chillies and caraway.",
        "category": "sauce",
        "price_cents": 850,
        "unit": "250ml",
        "is_available": True,
        "is_archived": False,
        "ingredients": [
            {"name": "Red peppers", "quantity": "6"},
            {"name": "Caraway seeds", "quantity": "1 tsp", "is_optional": True},
        ],
        "allergens": {
            "contains": ["cereals_gluten", "tree_nuts", "sesame"],
            "may_contain": ["peanut"],
            "gluten_cereals": ["wheat", "rye"],
            "tree_nut_species": ["almond", "cashew"],
            "sulphites_declared": True,
            "chef_note": "Made in a kitchen that also handles shellfish.",
            "reviewed_at": REVIEWED_AT,
            "reviewed_by": "chef",
        },
        "storage": {
            "method": "refrigerate",
            "temperature_c": "0-4",
            "shelf_life_days": 10,
            "shelf_life_note": "3 days once opened",
            "freezable": True,
            "freezer_life_days": 90,
        },
        "preparation": {
            "steps": ["Stir before use.", "Bring to room temperature."],
            "reheat_method": "none",
            "serving_suggestion": "Spoon over roast carrots.",
        },
        "preference_flags": ["chilli", "vegan"],
        "spice_level": 3,
    }
    data.update(overrides)
    return Component.model_validate(data)


@pytest.fixture()
def seeded(db):
    db["components"].insert_one(make_component().to_mongo())
    return db


@pytest.fixture()
def page(client, seeded) -> str:
    return client.get("/components/harissa").get_data(as_text=True)


def test_all_four_panels_are_in_the_served_html(page):
    """No lazy fetch, no JS-gated content (03-FRONTEND.md)."""
    for anchor in ("ingredients", "allergens", "storage", "preparation"):
        assert f'id="{anchor}"' in page
        assert f'href="#{anchor}"' in page

    assert "Red peppers" in page
    assert "Stir before use." in page
    assert "0-4" in page


def test_the_tab_strip_is_a_set_of_in_page_links_without_javascript(page):
    """Anchors, not buttons: they work before tabs.js runs, and without it."""
    strip = re.search(r'<nav class="tab-strip".*?</nav>', page, re.S)
    assert strip is not None
    assert "<button" not in strip.group(0)
    assert "<select" not in strip.group(0)
    assert strip.group(0).count("<a ") == 4


def test_panels_carry_their_own_headings_for_a_javascript_less_reader(page):
    assert page.count('class="tab-panel__heading"') == 4
    assert page.count("<h1") == 1


def test_the_allergen_tab_names_gluten_cereals_and_nut_species_inline(page):
    assert "Cereals containing gluten (wheat, rye)" in page
    assert "Tree nuts (almond, cashew)" in page


def test_contains_and_may_contain_are_visually_distinct_blocks(page):
    contains_at = page.index("Contains")
    may_at = page.index("May contain — cross-contact risk")
    # `contains` always renders first (03-FRONTEND.md).
    assert contains_at < may_at
    assert "chip chip--contains" in page
    assert "chip chip--may" in page


def test_allergen_names_are_never_abbreviated(page):
    panel = re.search(r'<section class="tab-panel" id="allergens".*?</section>',
                      page, re.S).group(0)
    assert "Sesame" in panel
    assert "Peanut" in panel


def test_preference_flags_and_spice_never_appear_in_the_allergen_tab(page):
    """Preference data must never dilute a compliance surface."""
    panel = re.search(r'<section class="tab-panel" id="allergens".*?</section>',
                      page, re.S).group(0)
    assert "Chilli" not in panel
    assert "Vegan" not in panel
    assert "Heat:" not in panel
    # They do belong on the header and the ingredients tab.
    assert "Heat: Hot" in page


def test_the_sulphites_note_qualifies_a_declaration_never_replaces_one(client, db):
    """The threshold flag is a qualifier on the chip, not a second route
    to a declaration.

    The fixture item sets `sulphites_declared` without listing sulphites
    in `contains`. That is a data defect, and the page must not paper over
    it by asserting a declaration the block does not make.
    """
    assert "10 mg/kg" not in client.get("/components/harissa").get_data(as_text=True)

    db["components"].insert_one(
        make_component(
            slug="pickled-onions",
            name="Pickled onions",
            allergens={
                "contains": ["sulphites"],
                "may_contain": [],
                "sulphites_declared": True,
                "reviewed_at": REVIEWED_AT,
                "reviewed_by": "chef",
            },
        ).to_mongo()
    )
    html = client.get("/components/pickled-onions").get_data(as_text=True)
    assert "Sulphites" in html
    assert "Sulphites are present at 10 mg/kg or above." in html


def test_a_reviewed_declaration_reports_when_it_was_reviewed(page):
    assert "Declaration last reviewed" in page
    assert "1 March 2026" in page


def test_a_reviewed_empty_declaration_says_no_declared_allergens(client, db):
    db["components"].insert_one(
        make_component(
            slug="plain-rice",
            name="Plain rice",
            allergens={
                "contains": [],
                "may_contain": [],
                "reviewed_at": REVIEWED_AT,
                "reviewed_by": "chef",
            },
        ).to_mongo()
    )
    html = client.get("/components/plain-rice").get_data(as_text=True)
    assert "No declared allergens." in html


def test_an_unreviewed_item_is_not_published_at_all(client, db):
    """The publication gate is the model's, not the template's.

    An unreviewed item cannot be `is_available`, so it never reaches a
    page that could render the wrong phrase in the first place.
    """
    with pytest.raises(ValueError, match="allergen block has been reviewed"):
        make_component(
            slug="unreviewed",
            allergens={"contains": [], "may_contain": []},
        )

    db["components"].insert_one(
        make_component(
            slug="unreviewed",
            is_available=False,
            allergens={"contains": [], "may_contain": []},
        ).to_mongo()
    )
    assert client.get("/components/unreviewed").status_code == 404


def test_storage_and_preparation_render_their_declared_values(page):
    assert "Refrigerate" in page
    assert "10 days from preparation" in " ".join(page.split())
    assert "3 days once opened" in page
    assert "up to 90 days frozen" in " ".join(page.split())
    assert "No reheating needed" in page
    assert "Spoon over roast carrots." in page


def test_a_missing_storage_block_reads_as_pending_not_as_absent(client, db):
    db["components"].insert_one(
        make_component(slug="no-storage", storage=None, preparation=None).to_mongo()
    )
    html = client.get("/components/no-storage").get_data(as_text=True)
    assert "Storage information pending." in html
    assert "Preparation notes pending." in html


def test_a_draft_an_archived_item_and_a_typo_are_all_404(client, db):
    db["components"].insert_many(
        [
            make_component(
                slug="draft",
                is_available=False,
                allergens={"contains": [], "may_contain": []},
            ).to_mongo(),
            make_component(
                slug="archived", is_available=False, is_archived=True
            ).to_mongo(),
        ]
    )
    for slug in ("draft", "archived", "never-existed"):
        assert client.get(f"/components/{slug}").status_code == 404


def test_the_detail_page_loads_the_tab_module_but_does_not_need_it(page):
    assert "js/tabs.js" in page
    # Nothing in the tab block is hidden in the served HTML: hiding a
    # panel is tabs.js's job, and only once it has actually run.
    tab_block = page[page.index('<div class="item-tabs"'):]
    assert "hidden" not in tab_block
    assert "aria-selected" not in tab_block
