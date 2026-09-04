"""The dish detail page: the four tabs, and provenance that stays provenance."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from app.models.catalogue import Component, Dish

REVIEWED_AT = datetime(2026, 3, 1, tzinfo=timezone.utc)

REVIEWED = {
    "contains": [],
    "may_contain": [],
    "reviewed_at": REVIEWED_AT,
    "reviewed_by": "chef",
}


def make_dish(**overrides) -> Dish:
    data = {
        "name": "Lamb tagine",
        "slug": "lamb-tagine",
        "summary": "Slow-cooked lamb with apricots.",
        "description": "Braised overnight with our own harissa.",
        "category": "dinner",
        "price_cents": 2600,
        "unit": "portion",
        "is_available": True,
        "is_archived": False,
        "serves": 2,
        "ingredients": [
            {"name": "Lamb shoulder", "quantity": "800g"},
            {"name": "Dried apricots", "quantity": "100g", "is_optional": True},
        ],
        "allergens": {
            "contains": ["sesame"],
            "may_contain": ["tree_nuts"],
            "reviewed_at": REVIEWED_AT,
            "reviewed_by": "chef",
        },
        "storage": {
            "method": "refrigerate",
            "temperature_c": "0-4",
            "shelf_life_days": 3,
            "freezable": True,
            "freezer_life_days": 60,
        },
        "preparation": {
            "steps": ["Reheat covered."],
            "reheat_method": "oven",
            "reheat_minutes": 25,
            "serving_suggestion": "Serve with couscous.",
        },
        "preference_flags": ["chilli", "high_protein"],
        "spice_level": 2,
    }
    data.update(overrides)
    return Dish.model_validate(data)


def make_component(**overrides) -> Component:
    data = {
        "name": "Harissa",
        "slug": "harissa",
        "category": "sauce",
        "price_cents": 850,
        "unit": "250ml",
        "is_available": True,
        "is_archived": False,
        "allergens": dict(REVIEWED, contains=["peanut"]),
    }
    data.update(overrides)
    return Component.model_validate(data)


@pytest.fixture()
def harissa_id(db) -> str:
    return str(db["components"].insert_one(make_component().to_mongo()).inserted_id)


@pytest.fixture()
def seeded(db, harissa_id):
    db["dishes"].insert_one(make_dish(component_refs=[harissa_id]).to_mongo())
    return db


@pytest.fixture()
def page(client, seeded) -> str:
    return client.get("/dishes/lamb-tagine").get_data(as_text=True)


def test_all_four_panels_are_in_the_served_html(page):
    """No lazy fetch, no JS-gated content (03-FRONTEND.md)."""
    for anchor in ("ingredients", "allergens", "storage", "preparation"):
        assert f'id="{anchor}"' in page
        assert f'href="#{anchor}"' in page

    assert "Lamb shoulder" in page
    assert "Reheat covered." in page
    assert "0-4" in page


def test_the_tab_strip_is_a_set_of_in_page_links_without_javascript(page):
    """Anchors, not buttons: they work before tabs.js runs, and without it."""
    strip = re.search(r'<nav class="tab-strip".*?</nav>', page, re.S)
    assert strip is not None
    assert "<button" not in strip.group(0)
    assert strip.group(0).count("<a ") == 4


def test_the_page_has_one_h1_and_a_heading_per_panel(page):
    assert page.count("<h1") == 1
    assert page.count('class="tab-panel__heading"') == 4


def test_the_header_states_how_many_the_dish_serves(page):
    """A dish is priced whole, so the portion count is part of the price."""
    assert "$26.00" in page
    assert "serves 2" in " ".join(page.split())


def test_preference_flags_and_spice_sit_on_the_header_not_the_allergen_tab(page):
    panel = re.search(r'<section class="tab-panel" id="allergens".*?</section>',
                      page, re.S).group(0)
    assert "Chilli" not in panel
    assert "Heat:" not in panel
    assert "Heat: Medium" in page


def test_the_dish_declares_its_own_allergens(page):
    assert "Sesame" in page
    assert "May contain — cross-contact risk" in page
    # Species detail belongs to a positive declaration only, so a
    # cross-contact entry carries the allergen name alone.
    assert "Tree nuts" in page
    assert "Declaration last reviewed" in page


def test_a_referenced_component_never_alters_the_dishs_declaration(page):
    """The dish's own tabs are authoritative (01-DOMAIN.md).

    The referenced component declares peanut and the dish does not. A
    rollup at render time would silently rewrite a compliance surface the
    chef never reviewed, so the allergen tab must not mention it.
    """
    panel = re.search(r'<section class="tab-panel" id="allergens".*?</section>',
                      page, re.S).group(0)
    assert "Peanut" not in panel
    assert "Harissa" not in panel


def test_provenance_links_to_the_component_page_outside_the_tabs(page):
    made_with = page[page.index('id="made-with"'):page.index('<div class="item-tabs"')]
    assert "Harissa" in made_with
    assert 'href="/components/harissa"' in made_with
    assert "not ordered or priced separately" in " ".join(made_with.split())


def test_provenance_is_dropped_when_the_component_is_not_published(client, db):
    """A customer never sees a provenance link they cannot open."""
    draft_id = str(
        db["components"]
        .insert_one(
            make_component(
                slug="draft-puree",
                name="Draft purée",
                is_available=False,
                allergens={"contains": [], "may_contain": []},
            ).to_mongo()
        )
        .inserted_id
    )
    db["dishes"].insert_one(
        make_dish(slug="quiet-dish", component_refs=[draft_id]).to_mongo()
    )

    html = client.get("/dishes/quiet-dish").get_data(as_text=True)
    assert "Draft purée" not in html
    assert 'id="made-with"' not in html


def test_a_malformed_component_ref_is_dropped_rather_than_raising(client, db):
    db["dishes"].insert_one(
        make_dish(slug="odd-refs", component_refs=["not-an-object-id"]).to_mongo()
    )
    response = client.get("/dishes/odd-refs")
    assert response.status_code == 200
    assert 'id="made-with"' not in response.get_data(as_text=True)


def test_provenance_follows_the_chefs_authored_order(client, db, harissa_id):
    puree_id = str(
        db["components"]
        .insert_one(make_component(slug="pea-puree", name="Pea purée").to_mongo())
        .inserted_id
    )
    db["dishes"].insert_one(
        make_dish(
            slug="ordered-dish",
            # Authored order, and a repeat that must collapse.
            component_refs=[puree_id, harissa_id, puree_id],
        ).to_mongo()
    )

    html = client.get("/dishes/ordered-dish").get_data(as_text=True)
    made_with = html[html.index('id="made-with"'):html.index('<div class="item-tabs"')]
    assert made_with.index("Pea purée") < made_with.index("Harissa")
    assert made_with.count("Pea purée") == 1


def test_a_dish_with_no_references_shows_no_provenance_block(client, db):
    db["dishes"].insert_one(make_dish(slug="plain-dish").to_mongo())
    html = client.get("/dishes/plain-dish").get_data(as_text=True)
    assert 'id="made-with"' not in html
    assert "Made with" not in html


def test_a_missing_storage_block_reads_as_pending_not_as_absent(client, db):
    db["dishes"].insert_one(
        make_dish(slug="no-storage", storage=None, preparation=None).to_mongo()
    )
    html = client.get("/dishes/no-storage").get_data(as_text=True)
    assert "Storage information pending." in html
    assert "Preparation notes pending." in html


def test_an_unreviewed_dish_is_not_published_at_all(client, db):
    """The publication gate is the model's, not the template's."""
    with pytest.raises(ValueError, match="allergen block has been reviewed"):
        make_dish(slug="unreviewed", allergens={"contains": [], "may_contain": []})

    db["dishes"].insert_one(
        make_dish(
            slug="unreviewed",
            is_available=False,
            allergens={"contains": [], "may_contain": []},
        ).to_mongo()
    )
    assert client.get("/dishes/unreviewed").status_code == 404


def test_a_draft_an_archived_dish_and_a_typo_are_all_404(client, db):
    db["dishes"].insert_many(
        [
            make_dish(
                slug="draft",
                is_available=False,
                allergens={"contains": [], "may_contain": []},
            ).to_mongo(),
            make_dish(slug="archived", is_available=False, is_archived=True).to_mongo(),
        ]
    )
    for slug in ("draft", "archived", "never-existed"):
        assert client.get(f"/dishes/{slug}").status_code == 404


def test_the_breadcrumb_returns_to_the_dish_catalogue(page):
    breadcrumb = re.search(r'<nav class="breadcrumb".*?</nav>', page, re.S).group(0)
    assert 'href="/dishes"' in breadcrumb


def test_the_detail_page_loads_the_tab_module_but_does_not_need_it(page):
    assert "js/tabs.js" in page
    tab_block = page[page.index('<div class="item-tabs"'):]
    assert "hidden" not in tab_block
    assert "aria-selected" not in tab_block
