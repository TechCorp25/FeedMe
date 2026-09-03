"""Components browse: visibility, filtering, and a page that works JS-off."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from app.models.catalogue import Component

REVIEWED = {
    "contains": [],
    "may_contain": [],
    "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef",
}


def make_component(**overrides) -> Component:
    data = {
        "name": "Harissa",
        "slug": "harissa",
        "summary": "Smoky roasted chilli paste.",
        "category": "sauce",
        "price_cents": 850,
        "unit": "250ml",
        "is_available": True,
        "is_archived": False,
        "allergens": dict(REVIEWED, contains=["sesame"], may_contain=["peanut"]),
        "preference_flags": ["chilli", "vegan"],
        "spice_level": 3,
    }
    data.update(overrides)
    return Component.model_validate(data)


@pytest.fixture()
def seeded(db):
    """A catalogue with one draft, one archived and three visible items."""
    components = [
        make_component(),
        make_component(
            name="Green goddess",
            slug="green-goddess",
            category="dressing",
            price_cents=1200,
            allergens=dict(REVIEWED, contains=["milk"]),
            preference_flags=["vegetarian"],
            spice_level=0,
        ),
        make_component(
            name="Slow-roast lamb",
            slug="slow-roast-lamb",
            category="protein",
            price_cents=2450,
            unit="100g",
            allergens=dict(REVIEWED),
            preference_flags=["high_protein"],
            spice_level=0,
        ),
        make_component(
            name="Unpublished draft",
            slug="unpublished-draft",
            is_available=False,
            allergens={"contains": [], "may_contain": []},
        ),
        make_component(
            name="Retired sauce",
            slug="retired-sauce",
            is_available=False,
            is_archived=True,
            allergens={"contains": [], "may_contain": []},
        ),
    ]
    db["components"].insert_many([item.to_mongo() for item in components])
    return db


def test_browse_lists_only_published_components(client, seeded):
    html = client.get("/components").get_data(as_text=True)

    assert "Harissa" in html
    assert "Green goddess" in html
    assert "Slow-roast lamb" in html
    # A draft and an archived item are not published (01-DOMAIN.md).
    assert "Unpublished draft" not in html
    assert "Retired sauce" not in html


def test_browse_renders_without_javascript(client, seeded):
    """Complete, useful HTML on first request — no JS-gated content."""
    response = client.get("/components")
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert html.count("<h1") == 1
    # The filter form submits by itself: a plain GET, not a fetch handler.
    assert '<form class="filters" method="get"' in html
    assert 'type="submit"' in html
    assert 'href="/components/harissa"' in html


def test_browse_shows_prices_as_integer_minor_units(client, seeded):
    html = client.get("/components").get_data(as_text=True)
    assert "$8.50" in html
    assert "$24.50" in html


def test_category_filter_narrows_the_list(client, seeded):
    html = client.get("/components?category=sauce").get_data(as_text=True)
    assert "Harissa" in html
    assert "Green goddess" not in html


def test_preference_filters_compose_as_and(client, seeded):
    both = client.get("/components?preference=chilli&preference=vegan")
    assert "Harissa" in both.get_data(as_text=True)

    # 'chilli' and 'vegetarian' together match nothing in the fixture.
    neither = client.get("/components?preference=chilli&preference=vegetarian")
    html = neither.get_data(as_text=True)
    assert "Harissa" not in html
    assert "No components match those filters" in html


def test_an_unknown_filter_value_widens_rather_than_errors(client, seeded):
    """A stale link degrades to a wider result, never to an error page."""
    response = client.get("/components?category=not-a-category&preference=nonsense")
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert "Harissa" in html
    assert "Green goddess" in html


def test_the_filter_strip_is_built_from_the_visible_catalogue(client, seeded):
    html = client.get("/components").get_data(as_text=True)

    # Categories in use are offered; ones with no visible item are not.
    assert 'value="sauce"' in html
    assert 'value="dressing"' in html
    assert 'value="puree"' not in html
    # Chef-extensible preference flags come from the data, not a constant.
    assert 'value="high_protein"' in html


def test_narrowing_the_list_keeps_the_controls_that_widen_it(client, seeded):
    """Facets come from the whole visible catalogue, not the filtered page."""
    html = client.get("/components?category=sauce").get_data(as_text=True)
    assert 'value="dressing"' in html
    assert "Clear filters" in html


def test_the_selected_filters_are_reflected_back_into_the_form(client, seeded):
    """A filtered URL renders its own state, so it survives a bookmark."""
    html = client.get("/components?category=sauce&preference=vegan").get_data(
        as_text=True
    )
    category_option = re.search(
        r'<option\s+value="sauce"\s+(selected)?\s*>', html
    )
    assert category_option is not None and category_option.group(1) == "selected"
    assert re.search(r'value="vegan"\s+checked', html)
    assert not re.search(r'value="vegetarian"\s+checked', html)


def test_browse_never_summarises_allergens_onto_a_card(client, seeded):
    """Allergen data belongs on the item page, in the allergen tab.

    A card may say a reviewed item declares nothing, but it never renders
    a declaration: a chip on a card would be a second, unreviewed copy of
    a compliance surface.
    """
    html = client.get("/components").get_data(as_text=True)
    assert "chip--contains" not in html
    assert "Sesame" not in html
    assert "Full allergen declaration on the item page." in html
    assert "No declared allergens — full declaration on the item page." in html


def test_an_empty_catalogue_says_so_without_blaming_filters(client, db):
    html = client.get("/components").get_data(as_text=True)
    assert "There are no components available at the moment." in html
