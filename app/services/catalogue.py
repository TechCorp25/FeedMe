"""Catalogue browsing rules.

Sits between the public blueprint and the catalogue repositories: it
turns untrusted query-string input into a validated filter set, and it
pairs the filtered result with the facets the filter strip needs.

Only the components catalogue is wired up here. `dishes` is a separate
catalogue with its own browse page (01-DOMAIN.md) and lands next; it
reuses this shape rather than sharing a query.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.db.repositories import components as components_repo
from app.models.catalogue import (
    COMPONENT_CATEGORY_LABELS,
    Component,
    ComponentCategory,
    preference_flag_label,
)


@dataclass(frozen=True)
class FilterChoice:
    """One option in the filter strip, already resolved for display."""

    value: str
    label: str
    selected: bool


@dataclass(frozen=True)
class ComponentFilters:
    """A validated browse selection.

    Every value here has already been checked against the catalogue.
    Unrecognised input is dropped rather than rejected: a stale or
    hand-edited link degrades to a wider result, never to an error page.
    """

    category: ComponentCategory | None = None
    preference_flags: tuple[str, ...] = ()

    @property
    def is_active(self) -> bool:
        return self.category is not None or bool(self.preference_flags)

    def has_flag(self, flag: str) -> bool:
        return flag in self.preference_flags


@dataclass(frozen=True)
class ComponentBrowse:
    """Everything the browse page renders.

    The choice lists carry their own labels and selected state so the
    template stays a layout and the wording stays under test.
    """

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
    def preference_choices(self) -> list[FilterChoice]:
        return [
            FilterChoice(
                value=flag,
                label=preference_flag_label(flag),
                selected=self.filters.has_flag(flag),
            )
            for flag in self.preference_flags
        ]


def _parse_category(raw: str | None) -> ComponentCategory | None:
    if not raw:
        return None
    try:
        return ComponentCategory(raw)
    except ValueError:
        return None


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


def browse_components(
    *,
    category: str | None = None,
    preference_flags: Sequence[str] = (),
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
    )
    return ComponentBrowse(
        items=components_repo.list_visible_components(
            category=filters.category,
            preference_flags=filters.preference_flags,
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
