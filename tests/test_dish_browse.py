"""Dish browse: visibility, meal-type filtering, and a page that works JS-off."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from app.models.catalogue import Dish, MealType

REVIEWED = {
    "contains": [],
    "may_contain": [],
    "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef",
}


def make_dish(**overrides) -> Dish:
    data = {
        "name": "Shakshuka",
        "slug": "shakshuka",
        "summary": "Eggs poached in spiced tomato.",
        "category": "brunch",
        "price_cents": 1650,
        "unit": "portion",
        "is_available": True,
        "is_archived": False,
        "allergens": dict(REVIEWED, contains=["egg"]),
        "preference_flags": ["chilli", "vegetarian"],
        "spice_level": 2,
        "serves": 2,
    }
    data.update(overrides)
    return Dish.model_validate(data)


@pytest.fixture()
def meal_types(db) -> dict[str, str]:
    """Ordered meal types, returned as slug -> id."""
    ids = {}
    for sort_order, (slug, name) in enumerate(
        [("breakfast", "Breakfast"), ("dinner", "Dinner"), ("snack", "Snack")]
    ):
        meal_type = MealType.model_validate(
            {"name": name, "slug": slug, "sort_order": sort_order}
        )
        ids[slug] = str(db["meal_types"].insert_one(meal_type.to_mongo()).inserted_id)
    return ids


@pytest.fixture()
def seeded(db, meal_types):
    """A catalogue with one draft, one archived and three visible dishes."""
    dishes = [
        make_dish(meal_type_ids=[meal_types["breakfast"]]),
        make_dish(
            name="Lamb ragu",
            slug="lamb-ragu",
            price_cents=2400,
            allergens=dict(
                REVIEWED, contains=["cereals_gluten"], gluten_cereals=["wheat"]
            ),
            preference_flags=["high_protein"],
            spice_level=0,
            meal_type_ids=[meal_types["dinner"]],
        ),
        make_dish(
            # A dish may span several meal types (01-DOMAIN.md).
            name="Herbed frittata",
            slug="herbed-frittata",
            price_cents=1400,
            allergens=dict(REVIEWED, contains=["egg", "milk"]),
            preference_flags=["vegetarian"],
            spice_level=0,
            meal_type_ids=[meal_types["breakfast"], meal_types["snack"]],
        ),
        make_dish(
            name="Unpublished draft",
            slug="unpublished-draft",
            is_available=False,
            allergens={"contains": [], "may_contain": []},
            meal_type_ids=[meal_types["dinner"]],
        ),
        make_dish(
            name="Retired stew",
            slug="retired-stew",
            is_available=False,
            is_archived=True,
            allergens={"contains": [], "may_contain": []},
            meal_type_ids=[meal_types["dinner"]],
        ),
    ]
    db["dishes"].insert_many([dish.to_mongo() for dish in dishes])
    return db


def test_browse_lists_only_published_dishes(client, seeded):
    html = client.get("/dishes").get_data(as_text=True)

    assert "Shakshuka" in html
    assert "Lamb ragu" in html
    assert "Herbed frittata" in html
    # A draft and an archived item are not published (01-DOMAIN.md).
    assert "Unpublished draft" not in html
    assert "Retired stew" not in html


def test_browse_renders_without_javascript(client, seeded):
    """Complete, useful HTML on first request — no JS-gated content."""
    response = client.get("/dishes")
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert html.count("<h1") == 1
    # The filter form submits by itself: a plain GET, not a fetch handler.
    assert '<form class="filters" method="get"' in html
    assert 'type="submit"' in html
    assert 'href="/dishes/shakshuka"' in html


def test_browse_shows_prices_as_integer_minor_units(client, seeded):
    html = client.get("/dishes").get_data(as_text=True)
    assert "$16.50" in html
    assert "$24.00" in html


def test_the_meal_type_filter_narrows_the_list(client, seeded):
    html = client.get("/dishes?meal_type=dinner").get_data(as_text=True)
    assert "Lamb ragu" in html
    assert "Shakshuka" not in html


def test_a_dish_appears_under_every_meal_type_it_carries(client, seeded):
    """`meal_type_ids` is a list; matching one of them is enough."""
    breakfast = client.get("/dishes?meal_type=breakfast").get_data(as_text=True)
    snack = client.get("/dishes?meal_type=snack").get_data(as_text=True)

    assert "Herbed frittata" in breakfast
    assert "Herbed frittata" in snack
    assert "Lamb ragu" not in snack


def test_the_meal_type_filter_travels_as_a_slug_not_an_id(client, seeded):
    """A readable link survives a rename of the meal type's display name."""
    html = client.get("/dishes").get_data(as_text=True)
    assert 'value="breakfast"' in html
    assert 'name="meal_type"' in html
    # No raw ObjectId hex reaches the filter strip.
    assert not re.search(r'<option\s+value="[0-9a-f]{24}"', html)


def test_meal_type_and_preference_filters_compose_as_and(client, seeded):
    both = client.get("/dishes?meal_type=breakfast&preference=vegetarian")
    html = both.get_data(as_text=True)
    assert "Shakshuka" in html
    assert "Herbed frittata" in html
    assert "Lamb ragu" not in html

    # 'breakfast' and 'high_protein' together match nothing in the fixture.
    neither = client.get("/dishes?meal_type=breakfast&preference=high_protein")
    assert "No dishes match those filters" in neither.get_data(as_text=True)


def test_an_unknown_filter_value_widens_rather_than_errors(client, seeded):
    """A stale link degrades to a wider result, never to an error page."""
    response = client.get("/dishes?meal_type=not-a-meal-type&preference=nonsense")
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert "Shakshuka" in html
    assert "Lamb ragu" in html


def test_a_real_meal_type_with_no_dishes_narrows_to_an_honest_empty_page(
    client, seeded, db
):
    """A recognised vocabulary is applied even when it matches nothing.

    Widening here would be a lie: the customer asked for supper and would
    be shown every dish as though all of them were supper.
    """
    db["meal_types"].insert_one(
        MealType.model_validate(
            {"name": "Supper", "slug": "supper", "sort_order": 9}
        ).to_mongo()
    )
    html = client.get("/dishes?meal_type=supper").get_data(as_text=True)

    assert "Shakshuka" not in html
    assert "No dishes match those filters" in html
    # The selection is still reflected back, so it can be seen and cleared.
    assert re.search(r'<option\s+value="supper"\s+selected', html)


def test_the_filter_strip_is_built_from_the_visible_catalogue(client, seeded, db):
    """A meal type no visible dish carries is not offered."""
    db["meal_types"].insert_one(
        MealType.model_validate(
            {"name": "Supper", "slug": "supper", "sort_order": 9}
        ).to_mongo()
    )
    html = client.get("/dishes").get_data(as_text=True)

    assert 'value="breakfast"' in html
    assert 'value="dinner"' in html
    assert 'value="supper"' not in html
    # Chef-extensible preference flags come from the data, not a constant.
    assert 'value="high_protein"' in html


def test_meal_types_are_offered_in_their_chef_defined_order(client, seeded):
    html = client.get("/dishes").get_data(as_text=True)
    assert html.index('value="breakfast"') < html.index('value="dinner"')
    assert html.index('value="dinner"') < html.index('value="snack"')


def test_narrowing_the_list_keeps_the_controls_that_widen_it(client, seeded):
    """Facets come from the whole visible catalogue, not the filtered page."""
    html = client.get("/dishes?meal_type=dinner").get_data(as_text=True)
    assert 'value="breakfast"' in html
    assert "Clear filters" in html


def test_the_selected_filters_are_reflected_back_into_the_form(client, seeded):
    """A filtered URL renders its own state, so it survives a bookmark."""
    html = client.get("/dishes?meal_type=dinner&preference=high_protein").get_data(
        as_text=True
    )
    meal_type_option = re.search(
        r'<option\s+value="dinner"\s+(selected)?\s*>', html
    )
    assert meal_type_option is not None and meal_type_option.group(1) == "selected"
    assert re.search(r'value="high_protein"\s+checked', html)
    assert not re.search(r'value="vegetarian"\s+checked', html)


def test_clearing_filters_returns_to_the_dish_catalogue_not_the_component_one(
    client, seeded
):
    """One filter macro serves both catalogues; each keeps its own action."""
    html = client.get("/dishes?meal_type=dinner").get_data(as_text=True)
    assert '<form class="filters" method="get" action="/dishes"' in html
    assert 'href="/components"' not in html.split('class="filters"')[1].split(
        "</form>"
    )[0]


def test_browse_never_summarises_allergens_onto_a_card(client, seeded):
    """Allergen data belongs on the item page, in the allergen tab.

    A chip on a card would be a second, unreviewed copy of a compliance
    surface, so a card points at the declaration rather than repeating it.
    """
    html = client.get("/dishes").get_data(as_text=True)
    assert "chip--contains" not in html
    assert "chip--may" not in html
    # No declared allergen is named: 'Milk' and 'Egg' are on two of the
    # seeded dishes and appear nowhere in the rendered cards.
    assert "Milk" not in html
    assert "Egg " not in html
    assert "Full allergen declaration on the item page." in html


def test_an_empty_catalogue_says_so_without_blaming_filters(client, db):
    html = client.get("/dishes").get_data(as_text=True)
    assert "There are no dishes available at the moment." in html
