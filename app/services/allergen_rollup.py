"""Authoring-time allergen rollup.

Advisory only. This module reports a discrepancy between a dish and the
components it references; it never mutates a document, never merges a
component's declaration into a dish, and is never consulted at render
time. The chef resolves every warning by hand (01-DOMAIN.md).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.models.allergens import ALLERGEN_LABELS, AllergenCode
from app.models.catalogue import Component, Dish


@dataclass(frozen=True)
class RollupWarning:
    """One allergen a linked component declares and the dish does not."""

    code: AllergenCode
    component_name: str

    @property
    def message(self) -> str:
        return (
            f"{self.component_name} declares {ALLERGEN_LABELS[self.code]}, "
            f"which this dish does not declare."
        )


def rollup_warnings(dish: Dish, components: Iterable[Component]) -> list[RollupWarning]:
    """Warnings for the chef editor. Returns [] when nothing is missing."""
    declared = set(dish.allergens.contains)
    warnings = [
        RollupWarning(code=code, component_name=component.name)
        for component in components
        for code in component.allergens.contains
        if code not in declared
    ]
    return sorted(warnings, key=lambda w: (w.code.value, w.component_name))
