"""Catalogue browsing rules.

Sits between the public blueprint and the catalogue repositories: it
turns untrusted query-string input into a validated filter set, and it
pairs the filtered result with the facets the filter strip needs.

Both catalogues are wired up here. `components` and `dishes` are separate
catalogues with separate queries (01-DOMAIN.md); they share the browse
*shape* — a validated filter set, the facets the filter strip needs, and
labels resolved for display — but never a query.

Three browse surfaces are served: the two catalogues, and the meal-type
menu, which is the dish catalogue entered through a meal type in the path
rather than through a query parameter (04-WORKFLOWS.md). All three offer
the same preference and allergen-exclusion controls.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.db.repositories import components as components_repo
from app.db.repositories import dishes as dishes_repo
from app.db.repositories import meal_types as meal_types_repo
from app.models.allergens import ALLERGEN_LABELS, AllergenCode
from app.models.catalogue import (
    COMPONENT_CATEGORY_LABELS,
    Component,
    ComponentCategory,
    Dish,
    ItemBase,
    MealType,
    preference_flag_label,
)

#: Every declarable allergen, always offered, in the vocabulary's own order.
#:
#: Deliberately not read from the data, unlike every other facet here. A
#: strip built from the catalogue would be shorter — and its shortness
#: would itself be a claim: an allergen missing from the list would read
#: as 'nothing here contains that'. The exclusion control is a browsing
#: aid and is not allowed to make a statement about the catalogue's
#: contents, so it offers the whole controlled vocabulary every time.
EXCLUDABLE_ALLERGENS: tuple[AllergenCode, ...] = tuple(AllergenCode)


@dataclass(frozen=True)
class FilterChoice:
    """One option in the filter strip, already resolved for display."""

    value: str
    label: str
    selected: bool


@dataclass(frozen=True)
class FilterFacet:
    """A single-select facet, resolved for display.

    Both catalogues filter on one controlled vocabulary plus the shared
    preference flags. Naming that vocabulary here — its query-string key
    and its wording — lets one macro render either filter strip without
    knowing which catalogue it is looking at.
    """

    name: str
    label: str
    all_label: str
    choices: list[FilterChoice]


def _parse_preference_flags(
    raw: Sequence[str], offered: Sequence[str]
) -> tuple[str, ...]:
    """Keep the requested flags that the catalogue actually offers.

    Order follows `offered`, and duplicates collapse, so the same
    selection always produces the same query and the same rendered chips
    regardless of how the URL was assembled.
    """
    requested = {value.strip() for value in raw if value and value.strip()}
    return tuple(flag for flag in offered if flag in requested)


def _parse_excluded_allergens(raw: Sequence[str]) -> tuple[AllergenCode, ...]:
    """Keep the values that name a real allergen, in vocabulary order.

    Same rule as every other filter: an unrecognised value is dropped
    rather than rejected. Dropping widens the result here, which is the
    safe direction to fail — the customer is shown more than they asked
    to see, never less, and the item pages still carry the declaration.
    """
    requested = {value.strip() for value in raw if value and value.strip()}
    return tuple(code for code in EXCLUDABLE_ALLERGENS if code.value in requested)


class _SharedFilters:
    """The two controls every browse surface offers.

    Mixed into each surface's own frozen filter dataclass, which supplies
    the `preference_flags` and `exclude_allergens` fields. It holds no
    fields of its own, so it does not disturb any dataclass signature.
    """

    preference_flags: tuple[str, ...]
    exclude_allergens: tuple[AllergenCode, ...]

    @property
    def is_active(self) -> bool:
        return bool(self.preference_flags or self.exclude_allergens)

    def has_flag(self, flag: str) -> bool:
        return flag in self.preference_flags

    def excludes(self, code: AllergenCode) -> bool:
        return code in self.exclude_allergens


class _SharedFacets:
    """The choice lists and cautions every browse surface renders.

    Mixed into each surface's own frozen browse dataclass, which supplies
    `filters` and `preference_flags`. The choice lists carry their own
    labels and selected state so the template stays a layout and the
    wording stays under test.
    """

    filters: _SharedFilters
    preference_flags: list[str]

    @property
    def preference_choices(self) -> list[FilterChoice]:
        return [
            FilterChoice(
                value=flag,
                label=preference_flag_label(flag),
                selected=self.filters.has_flag(flag),
            )
            for flag in self.preference_flags
        ]

    @property
    def allergen_choices(self) -> list[FilterChoice]:
        return [
            FilterChoice(
                value=code.value,
                label=ALLERGEN_LABELS[code],
                selected=self.filters.excludes(code),
            )
            for code in EXCLUDABLE_ALLERGENS
        ]

    def cautions_for(self, item: ItemBase) -> list[str]:
        """Excluded allergens this item declares as a cross-contact risk.

        An item whose `may_contain` names an excluded allergen is shown
        rather than hidden, because hiding it would let the filter read
        as a safety guarantee (04-WORKFLOWS.md). It carries this caution
        instead. Only the allergens the customer themselves excluded are
        named: this answers their query and is not a second copy of the
        item's declaration, which stays on the item page.
        """
        return [
            ALLERGEN_LABELS[code]
            for code in self.filters.exclude_allergens
            if code in item.allergens.may_contain
        ]


# --- components -------------------------------------------------------------


@dataclass(frozen=True)
class ComponentFilters(_SharedFilters):
    """A validated browse selection.

    Every value here has already been checked against the catalogue.
    Unrecognised input is dropped rather than rejected: a stale or
    hand-edited link degrades to a wider result, never to an error page.
    """

    category: ComponentCategory | None = None
    preference_flags: tuple[str, ...] = ()
    exclude_allergens: tuple[AllergenCode, ...] = ()

    @property
    def is_active(self) -> bool:
        return self.category is not None or super().is_active


@dataclass(frozen=True)
class ComponentBrowse(_SharedFacets):
    """Everything the browse page renders."""

    items: list[Component]
    filters: ComponentFilters
    categories: list[ComponentCategory] = field(default_factory=list)
    preference_flags: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def category_choices(self) -> list[FilterChoice]:
        return [
            FilterChoice(
                value=category.value,
                label=COMPONENT_CATEGORY_LABELS[category],
                selected=self.filters.category == category,
            )
            for category in self.categories
        ]

    @property
    def select_facet(self) -> FilterFacet:
        return FilterFacet(
            name="category",
            label="Category",
            all_label="All categories",
            choices=self.category_choices,
        )


def _parse_category(raw: str | None) -> ComponentCategory | None:
    if not raw:
        return None
    try:
        return ComponentCategory(raw)
    except ValueError:
        return None


def browse_components(
    *,
    category: str | None = None,
    preference_flags: Sequence[str] = (),
    exclude_allergens: Sequence[str] = (),
) -> ComponentBrowse:
    """Filtered component listing plus the facets for the filter strip.

    Facets are read from the whole visible catalogue, not from the
    filtered result, so narrowing the list never removes the control that
    would widen it again.
    """
    offered_flags = components_repo.visible_component_preference_flags()
    filters = ComponentFilters(
        category=_parse_category(category),
        preference_flags=_parse_preference_flags(preference_flags, offered_flags),
        exclude_allergens=_parse_excluded_allergens(exclude_allergens),
    )
    return ComponentBrowse(
        items=components_repo.list_visible_components(
            category=filters.category,
            preference_flags=filters.preference_flags,
            exclude_allergens=filters.exclude_allergens,
        ),
        filters=filters,
        categories=components_repo.visible_component_categories(),
        preference_flags=offered_flags,
    )


def get_component_detail(slug: str) -> Component | None:
    """One component, or None when it is not published to customers.

    A draft, an archived item and a slug that never existed are
    indistinguishable to a customer: all three are 'not found'.
    """
    return components_repo.get_visible_component_by_slug(slug)


# --- dishes -----------------------------------------------------------------


@dataclass(frozen=True)
class DishFilters(_SharedFilters):
    """A validated browse selection for the dish catalogue.

    Mirrors `ComponentFilters`, including the rule that matters most:
    unrecognised input is dropped rather than rejected, so a stale or
    hand-edited link degrades to a wider result, never to an error page.
    """

    meal_type: MealType | None = None
    preference_flags: tuple[str, ...] = ()
    exclude_allergens: tuple[AllergenCode, ...] = ()

    @property
    def is_active(self) -> bool:
        return self.meal_type is not None or super().is_active


@dataclass(frozen=True)
class DishBrowse(_SharedFacets):
    """Everything the dish browse page renders."""

    items: list[Dish]
    filters: DishFilters
    meal_types: list[MealType] = field(default_factory=list)
    preference_flags: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def meal_type_choices(self) -> list[FilterChoice]:
        """Meal types as filter options, keyed by slug.

        The slug rather than the id is what travels in the URL: a meal
        type is chef-renameable, and a readable link survives a rename of
        the display name.
        """
        selected = self.filters.meal_type
        selected_slug = selected.slug if selected is not None else None
        return [
            FilterChoice(
                value=meal_type.slug,
                label=meal_type.name,
                selected=meal_type.slug == selected_slug,
            )
            for meal_type in self.meal_types
        ]

    @property
    def select_facet(self) -> FilterFacet:
        return FilterFacet(
            name="meal_type",
            label="Meal type",
            all_label="All meal types",
            choices=self.meal_type_choices,
        )


@dataclass(frozen=True)
class DishDetail:
    """One dish plus the components it is made with.

    `provenance` is display-only. A dish's own four tabs are authoritative
    and are never merged with, or overridden by, a referenced component
    (01-DOMAIN.md), so this list travels beside the dish, not inside it.
    """

    dish: Dish
    provenance: list[Component]


def _parse_meal_type(raw: str | None) -> MealType | None:
    """Resolve a meal-type slug, or None when it names nothing.

    A slug that exists is honoured even when no visible dish carries it:
    that narrows to an empty page, which is the honest answer. Only a slug
    that names no meal type at all is dropped.
    """
    if not raw:
        return None
    return meal_types_repo.get_meal_type_by_slug(raw)


def _offered_meal_types(selected: MealType | None) -> list[MealType]:
    """Meal types worth offering in the filter strip.

    Built from the meal types actually carried by visible dishes, so the
    strip never offers a choice that returns nothing. The current
    selection is always included even when it matches nothing, so a
    bookmarked filter still renders its own state and can be cleared.
    """
    in_use = set(dishes_repo.visible_dish_meal_type_ids())
    if selected is not None:
        in_use.add(selected.id)
    return [
        meal_type
        for meal_type in meal_types_repo.list_meal_types()
        if meal_type.id in in_use
    ]


def browse_dishes(
    *,
    meal_type: str | None = None,
    preference_flags: Sequence[str] = (),
    exclude_allergens: Sequence[str] = (),
) -> DishBrowse:
    """Filtered dish listing plus the facets for the filter strip.

    Facets are read from the whole visible catalogue, not from the
    filtered result, so narrowing the list never removes the control that
    would widen it again.
    """
    offered_flags = dishes_repo.visible_dish_preference_flags()
    selected_meal_type = _parse_meal_type(meal_type)
    selected_id = selected_meal_type.id if selected_meal_type is not None else None
    filters = DishFilters(
        meal_type=selected_meal_type,
        preference_flags=_parse_preference_flags(preference_flags, offered_flags),
        exclude_allergens=_parse_excluded_allergens(exclude_allergens),
    )
    return DishBrowse(
        items=dishes_repo.list_visible_dishes(
            meal_type_id=selected_id,
            preference_flags=filters.preference_flags,
            exclude_allergens=filters.exclude_allergens,
        ),
        filters=filters,
        meal_types=_offered_meal_types(selected_meal_type),
        preference_flags=offered_flags,
    )


def get_dish_detail(slug: str) -> DishDetail | None:
    """One dish and its provenance, or None when it is not published.

    A draft, an archived item and a slug that never existed are
    indistinguishable to a customer: all three are 'not found'.
    """
    dish = dishes_repo.get_visible_dish_by_slug(slug)
    if dish is None:
        return None
    return DishDetail(
        dish=dish,
        provenance=components_repo.list_visible_components_by_ids(
            dish.component_refs
        ),
    )


# --- the meal-type menu -----------------------------------------------------


@dataclass(frozen=True)
class MenuFilters(_SharedFilters):
    """A validated selection for one meal type's menu.

    The meal type is not here: on this surface it is the page, addressed
    in the path, not a filter the customer can clear. Only the controls
    that narrow *within* the menu belong in the filter set.
    """

    preference_flags: tuple[str, ...] = ()
    exclude_allergens: tuple[AllergenCode, ...] = ()


@dataclass(frozen=True)
class MenuBrowse(_SharedFacets):
    """One meal type's dishes.

    The same dishes, the same cards and the same detail pages as
    `/dishes` — the meal type is how the customer arrived, not a
    different catalogue (04-WORKFLOWS.md).
    """

    meal_type: MealType
    items: list[Dish]
    filters: MenuFilters
    preference_flags: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def select_facet(self) -> None:
        """No single-select facet: the meal type is fixed by the path.

        The filter macro renders whatever facet a browse object names, and
        this one names none, so the strip drops the control rather than
        offering a meal-type selector that would contradict the URL.
        """
        return None


def browse_menu(
    meal_type_slug: str,
    *,
    preference_flags: Sequence[str] = (),
    exclude_allergens: Sequence[str] = (),
) -> MenuBrowse | None:
    """One meal type's dishes, or None when the slug names no meal type.

    Unlike the `meal_type` query parameter, an unknown slug here is not
    dropped: it is a path that names nothing, and the honest answer is a
    404 rather than silently serving the whole catalogue under a heading
    the customer did not ask for.
    """
    meal_type = meal_types_repo.get_meal_type_by_slug(meal_type_slug)
    if meal_type is None:
        return None
    offered_flags = dishes_repo.visible_dish_preference_flags()
    filters = MenuFilters(
        preference_flags=_parse_preference_flags(preference_flags, offered_flags),
        exclude_allergens=_parse_excluded_allergens(exclude_allergens),
    )
    return MenuBrowse(
        meal_type=meal_type,
        items=dishes_repo.list_visible_dishes(
            meal_type_id=meal_type.id,
            preference_flags=filters.preference_flags,
            exclude_allergens=filters.exclude_allergens,
        ),
        filters=filters,
        preference_flags=offered_flags,
    )


def list_menu_meal_types() -> list[MealType]:
    """Meal types with at least one visible dish, in the chef's order.

    The entry points the `/menu` surface actually has. A meal type no
    published dish carries is not offered, so a link from the dish
    catalogue never lands on an empty menu.
    """
    return _offered_meal_types(None)
