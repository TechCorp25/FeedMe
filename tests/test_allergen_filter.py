"""The allergen exclusion filter, offered on all three entry points.

04-WORKFLOWS.md fixes the behaviour that matters: selecting "exclude
peanut" hides any item whose `allergens.contains` includes it, items with
`may_contain` are shown with a visible caution rather than hidden, and the
UI states plainly that it is a browsing aid rather than a safety
guarantee. Hiding a cross-contact risk would turn the filter into exactly
the medical safeguard it says it is not.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from app.models.allergens import ALLERGEN_LABELS, AllergenCode
from app.models.catalogue import Component, Dish, MealType

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
        "allergens": dict(REVIEWED),
        "preference_flags": ["vegan"],
    }
    data.update(overrides)
    return Component.model_validate(data)


def make_dish(**overrides) -> Dish:
    data = {
        "name": "Satay bowl",
        "slug": "satay-bowl",
        "summary": "Grilled chicken, peanut sauce.",
        "category": "bowl",
        "price_cents": 1900,
        "unit": "portion",
        "is_available": True,
        "is_archived": False,
        "allergens": dict(REVIEWED),
        "preference_flags": ["high_protein"],
        "serves": 1,
    }
    data.update(overrides)
    return Dish.model_validate(data)


@pytest.fixture()
def dinner_id(db) -> str:
    meal_type = MealType.model_validate(
        {"name": "Dinner", "slug": "dinner", "sort_order": 0}
    )
    return str(db["meal_types"].insert_one(meal_type.to_mongo()).inserted_id)


@pytest.fixture()
def seeded(db, dinner_id):
    """One item declaring peanut, one at risk of it, one clear of it."""
    db["components"].insert_many(
        [
            item.to_mongo()
            for item in [
                make_component(
                    name="Peanut dressing",
                    slug="peanut-dressing",
                    allergens=dict(REVIEWED, contains=["peanut"]),
                ),
                make_component(
                    name="Sesame dressing",
                    slug="sesame-dressing",
                    allergens=dict(
                        REVIEWED, contains=["sesame"], may_contain=["peanut"]
                    ),
                ),
                make_component(name="Harissa", slug="harissa"),
            ]
        ]
    )
    db["dishes"].insert_many(
        [
            item.to_mongo()
            for item in [
                make_dish(
                    name="Satay bowl",
                    slug="satay-bowl",
                    allergens=dict(REVIEWED, contains=["peanut"]),
                    meal_type_ids=[dinner_id],
                ),
                make_dish(
                    name="Sesame noodles",
                    slug="sesame-noodles",
                    allergens=dict(
                        REVIEWED, contains=["sesame"], may_contain=["peanut"]
                    ),
                    meal_type_ids=[dinner_id],
                ),
                make_dish(
                    name="Roast pumpkin",
                    slug="roast-pumpkin",
                    preference_flags=["vegan"],
                    meal_type_ids=[dinner_id],
                ),
            ]
        ]
    )
    return db


#: The three entry points, and the item each one should hide for peanut.
SURFACES = [
    ("/components", "Peanut dressing", "Sesame dressing", "Harissa"),
    ("/dishes", "Satay bowl", "Sesame noodles", "Roast pumpkin"),
    ("/menu/dinner", "Satay bowl", "Sesame noodles", "Roast pumpkin"),
]


@pytest.mark.parametrize(("url", "declares", "may_contain", "clear"), SURFACES)
def test_excluding_an_allergen_hides_only_what_declares_it(
    client, seeded, url, declares, may_contain, clear
):
    html = client.get(url + "?exclude=peanut").get_data(as_text=True)

    assert declares not in html
    # Shown, not hidden: a cross-contact risk is not a declared ingredient.
    assert may_contain in html
    assert clear in html


@pytest.mark.parametrize(("url", "declares", "may_contain", "clear"), SURFACES)
def test_a_cross_contact_item_carries_a_visible_caution(
    client, seeded, url, declares, may_contain, clear
):
    html = client.get(url + "?exclude=peanut").get_data(as_text=True)

    assert "card__caution" in html
    assert "may contain Peanut" in html
    # The caution names only what the customer excluded, not the item's own
    # declaration — that stays on the item page.
    assert "Sesame" not in html.split("card__caution")[1].split("</p>")[0]


@pytest.mark.parametrize(("url", "declares", "may_contain", "clear"), SURFACES)
def test_no_caution_is_shown_when_nothing_is_excluded(
    client, seeded, url, declares, may_contain, clear
):
    assert "card__caution" not in client.get(url).get_data(as_text=True)


@pytest.mark.parametrize(("url", "declares", "may_contain", "clear"), SURFACES)
def test_the_control_states_that_it_is_not_a_safety_guarantee(
    client, seeded, url, declares, may_contain, clear
):
    """The disclaimer sits with the control, not in a footnote."""
    html = client.get(url).get_data(as_text=True)
    strip = html.split('<form class="filters"')[1].split("</form>")[0]

    assert "browsing aid, not a medical safeguard" in strip
    assert "Always read the full declaration on the item page." in strip


@pytest.mark.parametrize(("url", "declares", "may_contain", "clear"), SURFACES)
def test_the_whole_controlled_vocabulary_is_always_offered(
    client, seeded, url, declares, may_contain, clear
):
    """A shorter list would itself be a claim about the catalogue.

    An allergen missing from the strip would read as 'nothing here
    contains that'. Lupin is declared by no seeded item and is still
    offered, in the vocabulary's own order.
    """
    html = client.get(url).get_data(as_text=True)

    for code in AllergenCode:
        assert 'name="exclude"' in html
        assert 'value="{}"'.format(code.value) in html
        assert ALLERGEN_LABELS[code] in html

    assert html.index('value="cereals_gluten"') < html.index('value="lupin"')


@pytest.mark.parametrize(("url", "declares", "may_contain", "clear"), SURFACES)
def test_exclusions_compose_and_are_reflected_back_into_the_form(
    client, seeded, url, declares, may_contain, clear
):
    html = client.get(url + "?exclude=peanut&exclude=sesame").get_data(as_text=True)

    assert declares not in html
    assert may_contain not in html
    assert clear in html
    assert re.search(r'value="peanut"\s+checked', html)
    assert re.search(r'value="sesame"\s+checked', html)
    assert not re.search(r'value="milk"\s+checked', html)


@pytest.mark.parametrize(("url", "declares", "may_contain", "clear"), SURFACES)
def test_an_unknown_exclusion_widens_rather_than_errors(
    client, seeded, url, declares, may_contain, clear
):
    """A stale or hand-edited link degrades to a wider result.

    Widening is the safe direction to fail here: the customer is shown
    more than they asked to see, never less, and every item page still
    carries its own declaration.
    """
    response = client.get(url + "?exclude=not-an-allergen")
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert declares in html
    assert may_contain in html
    assert clear in html


def test_exclusion_composes_with_the_other_filters_as_and(client, seeded):
    html = client.get("/dishes?exclude=peanut&preference=vegan").get_data(
        as_text=True
    )

    assert "Roast pumpkin" in html
    assert "Satay bowl" not in html
    # 'Sesame noodles' survives the exclusion but is not vegan.
    assert "Sesame noodles" not in html


def test_an_exclusion_is_a_filter_that_can_be_cleared(client, seeded):
    html = client.get("/components?exclude=peanut").get_data(as_text=True)
    assert "Clear filters" in html


def test_excluding_everything_says_so_without_pretending_to_be_safe(client, seeded):
    """An empty result is still not a statement about safety."""
    query = "&".join("exclude=" + code.value for code in AllergenCode)
    html = client.get("/components?" + query).get_data(as_text=True)

    assert "Peanut dressing" not in html
    assert "Sesame dressing" not in html
    # An item declaring nothing is not hidden by an exclusion.
    assert "Harissa" in html
