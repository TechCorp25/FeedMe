"""Rollup is advisory. It warns; it never writes."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.allergens import AllergenBlock, AllergenCode, TreeNutSpecies
from app.models.catalogue import Component, Dish
from app.services.allergen_rollup import rollup_warnings

REVIEWED = {
    "reviewed_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef@example.com",
}


def make_component(name: str, *contains: AllergenCode, **extra) -> Component:
    return Component(
        name=name,
        slug=name.lower().replace(" ", "-"),
        price_cents=500,
        allergens=AllergenBlock(contains=list(contains), **REVIEWED, **extra),
    )


def make_dish(*contains: AllergenCode, **extra) -> Dish:
    return Dish(
        name="Lamb shoulder",
        slug="lamb-shoulder",
        price_cents=2500,
        allergens=AllergenBlock(contains=list(contains), **REVIEWED, **extra),
    )


def test_warns_when_a_component_declares_what_the_dish_does_not():
    dish = make_dish()
    component = make_component("Tahini dressing", AllergenCode.SESAME)

    warnings = rollup_warnings(dish, [component])

    assert [w.code for w in warnings] == [AllergenCode.SESAME]
    assert "Tahini dressing" in warnings[0].message
    assert "Sesame" in warnings[0].message


def test_no_warning_when_the_dish_already_declares_it():
    dish = make_dish(AllergenCode.SESAME)
    component = make_component("Tahini dressing", AllergenCode.SESAME)
    assert rollup_warnings(dish, [component]) == []


def test_rollup_never_mutates_the_dish():
    dish = make_dish()
    before = dish.model_dump()
    rollup_warnings(dish, [make_component("Tahini dressing", AllergenCode.SESAME)])
    assert dish.model_dump() == before
    assert dish.allergens.contains == []


def test_warnings_are_reported_per_component():
    dish = make_dish()
    components = [
        make_component("Tahini dressing", AllergenCode.SESAME),
        make_component("Dukkah", AllergenCode.SESAME),
        make_component(
            "Nut crumb",
            AllergenCode.TREE_NUTS,
            tree_nut_species=[TreeNutSpecies.PISTACHIO],
        ),
    ]

    warnings = rollup_warnings(dish, components)

    assert [(w.code, w.component_name) for w in warnings] == [
        (AllergenCode.SESAME, "Dukkah"),
        (AllergenCode.SESAME, "Tahini dressing"),
        (AllergenCode.TREE_NUTS, "Nut crumb"),
    ]


def test_no_components_means_no_warnings():
    assert rollup_warnings(make_dish(), []) == []
