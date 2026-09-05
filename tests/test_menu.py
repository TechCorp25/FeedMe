"""The meal-type menu: `/menu/<slug>`, the third ordering entry point.

Same dishes, same cards, same detail pages as `/dishes`. What differs is
that the meal type is the page rather than a filter (04-WORKFLOWS.md), so
it is in the path, it cannot be cleared, and a slug that names nothing is
a 404 rather than a silent widening.
"""

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
    """Ordered meal types, returned as slug -> id. 'Supper' carries nothing."""
    ids = {}
    for sort_order, (slug, name) in enumerate(
        [
            ("breakfast", "Breakfast"),
            ("dinner", "Dinner"),
            ("snack", "Snack"),
            ("supper", "Supper"),
        ]
    ):
        meal_type = MealType.model_validate(
            {"name": name, "slug": slug, "sort_order": sort_order}
        )
        ids[slug] = str(db["meal_types"].insert_one(meal_type.to_mongo()).inserted_id)
    return ids


@pytest.fixture()
def seeded(db, meal_types):
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


def test_a_menu_lists_only_that_meal_types_published_dishes(client, seeded):
    html = client.get("/menu/breakfast").get_data(as_text=True)

    assert "Shakshuka" in html
    assert "Herbed frittata" in html
    assert "Lamb ragu" not in html
    assert "Retired stew" not in html


def test_a_dish_appears_under_every_menu_it_carries(client, seeded):
    """`meal_type_ids` is a list, and the menus read it the same way."""
    breakfast = client.get("/menu/breakfast").get_data(as_text=True)
    snack = client.get("/menu/snack").get_data(as_text=True)

    assert "Herbed frittata" in breakfast
    assert "Herbed frittata" in snack


def test_a_menu_uses_the_same_cards_and_the_same_detail_pages(client, seeded):
    """The meal type is how the customer arrived, not a second catalogue."""
    html = client.get("/menu/breakfast").get_data(as_text=True)

    assert 'href="/dishes/shakshuka"' in html
    assert "$16.50" in html
    assert "Full allergen declaration on the item page." in html


def test_a_slug_that_names_no_meal_type_is_404(client, seeded):
    """A path that names nothing is not a filter value that does not apply.

    Serving the whole catalogue under a heading the customer never asked
    for would be worse than saying the page does not exist.
    """
    assert client.get("/menu/elevenses").status_code == 404


def test_a_real_meal_type_with_no_dishes_is_an_honest_empty_page(client, seeded):
    response = client.get("/menu/supper")
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert "There are no supper dishes at the moment." in html
    # And a way out that is not a filter to clear.
    assert 'href="/dishes"' in html


def test_the_menu_offers_no_control_that_clears_its_own_meal_type(client, seeded):
    """The meal type is the page. A select here would contradict the URL."""
    html = client.get("/menu/breakfast").get_data(as_text=True)

    assert 'name="meal_type"' not in html
    assert '<select' not in html
    assert "Clear filters" not in html


def test_the_menu_renders_without_javascript(client, seeded):
    response = client.get("/menu/breakfast")
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert html.count("<h1") == 1
    assert '<form class="filters" method="get" action="/menu/breakfast"' in html
    assert 'type="submit"' in html


def test_filters_on_a_menu_keep_the_customer_on_that_menu(client, seeded):
    html = client.get("/menu/breakfast?preference=vegetarian").get_data(as_text=True)

    assert "Shakshuka" in html
    assert "Herbed frittata" in html
    assert 'action="/menu/breakfast"' in html
    assert re.search(r'value="vegetarian"\s+checked', html)


def test_a_filter_that_empties_a_menu_says_so_and_offers_the_way_back(
    client, seeded
):
    html = client.get("/menu/breakfast?preference=high_protein").get_data(
        as_text=True
    )

    assert "Shakshuka" not in html
    assert "No breakfast dishes match those filters." in html
    assert 'href="/menu/breakfast"' in html


def test_the_menu_carries_a_breadcrumb_back_to_the_dish_catalogue(client, seeded):
    html = client.get("/menu/breakfast").get_data(as_text=True)
    breadcrumb = html.split('class="breadcrumb"')[1].split("</nav>")[0]
    assert 'href="/dishes"' in breadcrumb


def test_the_dish_catalogue_links_to_every_menu_that_has_dishes(client, seeded):
    """A menu link never lands on an empty page: 'Supper' carries nothing."""
    html = client.get("/dishes").get_data(as_text=True)

    assert 'href="/menu/breakfast"' in html
    assert 'href="/menu/dinner"' in html
    assert 'href="/menu/snack"' in html
    assert 'href="/menu/supper"' not in html


def test_menu_links_follow_the_chefs_order(client, seeded):
    html = client.get("/dishes").get_data(as_text=True)
    assert html.index('href="/menu/breakfast"') < html.index('href="/menu/dinner"')
    assert html.index('href="/menu/dinner"') < html.index('href="/menu/snack"')


def test_a_menu_travels_as_a_slug_not_an_id(client, seeded):
    """A readable link survives a rename of the meal type's display name."""
    html = client.get("/dishes").get_data(as_text=True)
    assert not re.search(r'href="/menu/[0-9a-f]{24}"', html)
