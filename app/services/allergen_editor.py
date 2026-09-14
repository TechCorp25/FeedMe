"""The chef allergen editor — the one code path that writes an AllergenBlock.

04-WORKFLOWS.md makes this a deliberately separate step, not a section of
the catalogue form, and 01-DOMAIN.md says the block "is never modified by
any code path except the chef allergen editor". That sentence is the
whole shape of this module. `catalogue_admin` reads the stored block to
validate against and writes it never; this writes it and nothing else.

Four rules follow.

**Nothing is inferred.** The chef ticks what is declared. A rollup
warning from a linked component is advisory and is never applied; a
retired code in a stored block is reported and never translated; and no
default, empty or otherwise, is written on the chef's behalf
(00-SYSTEM.md: allergen data is "never inferred, never auto-generated,
never silently defaulted").

**A save is a review.** The form carries an explicit confirmation and
refuses without it, and a save stamps `reviewed_at` and `reviewed_by`
with the moment and the chef who confirmed. Re-reviewing is therefore
also what clears a stale flag: `allergen_review_is_stale` compares the
review against `ingredients_updated_at`, so a fresh review is a fresh
comparison. Nothing here writes `ingredients_updated_at`.

**Only the live vocabulary is offered, and only it is accepted.**
`WRITABLE_CODES` and `WRITABLE_GLUTEN_CEREALS` are what the form draws
and what the parser will take; a posted value outside them is refused
rather than dropped, because dropping a declaration silently is the one
failure mode a compliance form must not have.

**Sulphites are one control.** `sulphites in contains` and
`sulphites_declared` are a biconditional in the model, so the form
exposes a single checkbox and writes both halves from it. The error is
unreachable through the UI rather than reachable and caught.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.db.repositories import components as components_repo
from app.db.repositories import dishes as dishes_repo
from app.models.allergens import (
    ALLERGEN_LABELS,
    GLUTEN_CEREAL_LABELS,
    TREE_NUT_LABELS,
    WRITABLE_CODES,
    WRITABLE_GLUTEN_CEREALS,
    AllergenBlock,
    AllergenCode,
    GlutenCereal,
    TreeNutSpecies,
)
from app.models.base import utcnow
from app.models.catalogue import Dish, ItemBase
from app.models.users import User
from app.services import allergen_rollup
from app.services.catalogue_admin import COMPONENT, PLURAL

MAX_CHEF_NOTE = 500


class AllergenReviewError(ValueError):
    """A refusal the chef can act on, with the wording to show them."""


# --- what the form offers ---------------------------------------------------


@dataclass(frozen=True)
class Choice:
    """One checkbox, already resolved for display."""

    value: str
    label: str
    checked: bool


def _code_choices(selected: list[AllergenCode]) -> list[Choice]:
    chosen = set(selected)
    return [
        Choice(code.value, ALLERGEN_LABELS[code], code in chosen)
        for code in WRITABLE_CODES
    ]


def form_context(kind: str, item: ItemBase) -> dict[str, Any]:
    """Everything the editor template renders from."""
    block = item.allergens
    return {
        "kind": kind,
        "plural_url": PLURAL[kind],
        "item": item,
        "block": block,
        "contains_choices": _code_choices(block.contains),
        "may_contain_choices": _code_choices(block.may_contain),
        "gluten_cereal_choices": [
            Choice(
                cereal.value,
                GLUTEN_CEREAL_LABELS[cereal],
                cereal in set(block.gluten_cereals),
            )
            for cereal in WRITABLE_GLUTEN_CEREALS
        ],
        "tree_nut_choices": [
            Choice(
                species.value,
                TREE_NUT_LABELS[species],
                species in set(block.tree_nut_species),
            )
            for species in TreeNutSpecies
        ],
        "gluten_code": AllergenCode.GLUTEN.value,
        "tree_nuts_code": AllergenCode.TREE_NUTS.value,
        "sulphites_code": AllergenCode.SULPHITES.value,
        "sulphites_label": ALLERGEN_LABELS[AllergenCode.SULPHITES],
        "rollup_warnings": rollup_warnings(kind, item),
        "max_chef_note": MAX_CHEF_NOTE,
    }


def rollup_warnings(kind: str, item: ItemBase) -> list[allergen_rollup.RollupWarning]:
    """Allergens a linked component declares and this dish does not.

    Advisory, always. 01-DOMAIN.md: rollup "never mutates the dish
    document. The chef resolves it manually." Nothing in this module
    reads the result except the template that shows it to the chef.

    Components have no `component_refs`, so they have no rollup.
    """
    if kind == COMPONENT or not isinstance(item, Dish) or not item.component_refs:
        return []
    linked = components_repo.chef_list_components_by_ids(item.component_refs)
    return allergen_rollup.rollup_warnings(
        item, [linked[ref] for ref in item.component_refs if ref in linked]
    )


# --- parsing ----------------------------------------------------------------


def _codes(form, field: str) -> list[AllergenCode]:
    """Ticked codes, in the vocabulary's order, refusing anything else.

    A value outside `WRITABLE_CODES` is a refusal rather than a silent
    drop. Dropping it would save a declaration the chef did not see and
    tell them it succeeded — and the retired codes are exactly the values
    a stale open tab would post.
    """
    posted = set(form.getlist(field))
    offered = {code.value for code in WRITABLE_CODES}
    unknown = posted - offered
    if unknown:
        raise AllergenReviewError(
            "That form offered an allergen this version no longer writes. "
            "Reload the page and make the declaration again."
        )
    return [code for code in WRITABLE_CODES if code.value in posted]


def _cereals(form) -> list[GlutenCereal]:
    posted = set(form.getlist("gluten_cereal"))
    offered = {cereal.value for cereal in WRITABLE_GLUTEN_CEREALS}
    if posted - offered:
        raise AllergenReviewError(
            "That form offered a cereal this version no longer writes. "
            "Reload the page and make the declaration again."
        )
    return [cereal for cereal in WRITABLE_GLUTEN_CEREALS if cereal.value in posted]


def _species(form) -> list[TreeNutSpecies]:
    posted = set(form.getlist("tree_nut_species"))
    offered = {species.value for species in TreeNutSpecies}
    if posted - offered:
        raise AllergenReviewError(
            "That form offered a tree nut this version does not know. "
            "Reload the page and make the declaration again."
        )
    return [species for species in TreeNutSpecies if species.value in posted]


def parse_block(form, *, reviewed_by: str) -> AllergenBlock:
    """The submitted declaration as a validated block. Never a raw form.

    The review stamp goes on here rather than at the write, because a
    block without it is not a declaration this editor is allowed to
    produce: 04-WORKFLOWS.md makes confirming the declaration the act
    that sets `reviewed_at` and `reviewed_by`.
    """
    if not form.get("confirm"):
        raise AllergenReviewError(
            "Confirm that the declaration is accurate before saving it. "
            "Nothing has been changed."
        )
    if not reviewed_by:
        raise AllergenReviewError("A review has to record who made it.")

    contains = _codes(form, "contains")
    note = (form.get("chef_note") or "").strip()
    if len(note) > MAX_CHEF_NOTE:
        raise AllergenReviewError(
            f"The kitchen note is longer than {MAX_CHEF_NOTE} characters."
        )

    try:
        return AllergenBlock(
            contains=contains,
            may_contain=_codes(form, "may_contain"),
            gluten_cereals=_cereals(form),
            tree_nut_species=_species(form),
            # One control, both halves. The model holds these as a
            # biconditional; the form never lets the chef set one.
            sulphites_declared=AllergenCode.SULPHITES in contains,
            chef_note=note or None,
            reviewed_at=utcnow(),
            reviewed_by=reviewed_by,
        )
    except ValidationError as exc:
        raise AllergenReviewError(_first_message(exc)) from exc


def _first_message(error: ValidationError) -> str:
    first = error.errors()[0]
    message = first.get("msg", "That declaration is not valid.")
    return message.removeprefix("Value error, ")


# --- writing ----------------------------------------------------------------


def save_review(kind: str, item: ItemBase, chef: User, form) -> AllergenBlock:
    """Record one reviewed declaration. Raises on a refusal.

    The item's other fields are untouched: this writes `allergens` and
    `updated_at` and nothing else, the mirror of `catalogue_admin` never
    writing `allergens` at all.
    """
    block = parse_block(form, reviewed_by=_reviewer(chef))
    written = (
        components_repo.chef_set_allergens(item.id, block.model_dump(mode="python"))
        if kind == COMPONENT
        else dishes_repo.chef_set_allergens(item.id, block.model_dump(mode="python"))
    )
    if not written:
        raise AllergenReviewError("That item no longer exists.")
    return block


def _reviewer(chef: User) -> str:
    """Who the record names.

    The email rather than the display name: a compliance record has to
    identify a person, and a display name is editable and need not be
    unique. There is exactly one `chef_admin`, so this is one address —
    but it is read from the actor rather than assumed.
    """
    return (getattr(chef, "email", "") or "").strip()
