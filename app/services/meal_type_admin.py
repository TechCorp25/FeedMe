"""The chef's meal-type editor: create, rename, reorder, delete.

01-DOMAIN.md names `meal_types` as one of the six collections — chef-owned,
"ordered, renameable" — and `/menu/<meal_type_slug>` is one of the three
ordering entry points 04-WORKFLOWS.md gives the customer. Until this
module existed the collection had two read functions and nothing that
wrote to it: `MealType(...)` was constructed nowhere in `app/` or
`scripts/`, so in a real deployment `/menu/<anything>` reached nothing and
the dish editor's meal-type checkboxes were permanently empty.

Three decisions shape what this is allowed to do.

**A meal type is deleted, not archived.** `MealType` has no `is_archived`
field and this does not add one. An archive flag on a catalogue item
exists so a withdrawn item keeps its identity on the orders that already
reference it — an `OrderLine` snapshots a name and a price, and the item
behind it has to stay resolvable. A meal type is referenced by nothing
except a dish's `meal_type_ids`, carries no content of its own, and is
never snapshotted onto an order. There is nothing for a flag to preserve,
and an archived-but-present meal type would be a second way for a label to
be invisible — one that `/menu` would have to learn about and the dish
editor would have to filter on.

**What protects `/menu` is a refusal, not a flag.** Deleting a meal type a
dish still points at leaves that dish carrying a dead id, which the dish
editor silently drops on the next save and which renders nothing anywhere
in between. So deletion is refused while any dish references the type, and
the refusal names the dishes to detach. Archived dishes count: an archived
dish is withdrawn, not deleted, and restoring one whose meal type had been
removed underneath it would be exactly the broken link this prevents.

**A slug is never re-derived on an edit.** `catalogue_admin.slugify` is
reused verbatim and applied on the same terms: offered when the chef
leaves the field blank, never written over one they typed, and never
recomputed from a changed name. A meal-type slug is in a customer-facing
URL, and silently changing it when a label is corrected breaks every link
anybody saved — the same rule, for the same reason, as a component's.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from app.db.repositories import dishes as dishes_repo
from app.db.repositories import meal_types as meal_types_repo
from app.models.base import utcnow
from app.models.catalogue import MealType
from app.services.catalogue_admin import ItemFormError, slugify

MAX_NAME = 120
MAX_SLUG = 120

#: How many dish names a refusal lists before it stops and gives a count.
NAMED_IN_REFUSAL = 5


class MealTypeFormError(ItemFormError):
    """A refusal the chef can fix, with the wording to show them.

    A subclass of `ItemFormError` rather than a sibling, because the chef
    area already renders that exception as a flash and the two carry the
    same contract: a sentence, addressed to the person who typed.
    """


@dataclass(frozen=True)
class MealTypeRow:
    """One meal type in the editor list, with what the chef acts on."""

    meal_type: MealType
    dish_count: int

    @property
    def can_be_deleted(self) -> bool:
        return self.dish_count == 0

    @property
    def usage_label(self) -> str:
        """How many dishes use this, in words. Never a colour, never a dot."""
        if self.dish_count == 0:
            return "No dishes use this meal type"
        if self.dish_count == 1:
            return "1 dish uses this meal type"
        return f"{self.dish_count} dishes use this meal type"


@dataclass(frozen=True)
class MealTypeListing:
    rows: list[MealTypeRow]

    @property
    def is_empty(self) -> bool:
        return not self.rows


def listing() -> MealTypeListing:
    """Every meal type, with the dish count that decides its controls.

    One count query per meal type. This collection is a handful of
    navigation labels — breakfast, lunch, dinner, snack — not a catalogue,
    so a per-row count is a handful of counted queries rather than the
    aggregation `chef_balances_by_user` needs for an unbounded customer
    list.
    """
    return MealTypeListing(
        rows=[
            MealTypeRow(
                meal_type=meal_type,
                dish_count=dishes_repo.chef_count_dishes_by_meal_type(meal_type.id),
            )
            for meal_type in meal_types_repo.list_meal_types()
        ]
    )


def get_meal_type(meal_type_id: str) -> MealType | None:
    return meal_types_repo.chef_get_meal_type(meal_type_id)


# --- parsing ----------------------------------------------------------------


def parse_form(form, *, existing: MealType | None) -> dict:
    """The two fields this editor owns, parsed and bounded.

    `sort_order` is deliberately not among them: position is moved by the
    reorder control, and a hidden field carrying it through an edit would
    let a stale form put a label back where it used to be.
    """
    name = (form.get("name") or "").strip()
    if not name:
        raise MealTypeFormError("Give the meal type a name.")
    if len(name) > MAX_NAME:
        raise MealTypeFormError(f"The name is longer than {MAX_NAME} characters.")

    typed = (form.get("slug") or "").strip()
    if len(typed) > MAX_SLUG:
        raise MealTypeFormError(f"The slug is longer than {MAX_SLUG} characters.")

    # Offered when blank, and only when blank. On an edit the stored slug
    # is what fills the field, so leaving it alone keeps it — a name
    # corrected from "Diner" to "Dinner" does not move `/menu/diner`.
    slug = typed or (existing.slug if existing else slugify(name))
    if not slug:
        raise MealTypeFormError(
            "That name gives no slug. Type one, like 'breakfast'."
        )

    try:
        MealType.model_validate(
            {"name": name, "slug": slug, "sort_order": existing.sort_order if existing else 0}
        )
    except ValidationError as exc:
        raise MealTypeFormError(
            "A slug is lowercase letters, numbers and single hyphens, "
            "like 'weekend-brunch'."
        ) from exc

    return {"name": name, "slug": slug}


# --- writing ----------------------------------------------------------------


def save(meal_type_id: str | None, form) -> MealType:
    """Create or rename one meal type. Raises `MealTypeFormError`."""
    existing = get_meal_type(meal_type_id) if meal_type_id else None
    if meal_type_id and existing is None:
        raise MealTypeFormError("That meal type no longer exists.")

    fields = parse_form(form, existing=existing)

    if existing is None:
        return _create(fields)
    return _update(existing, fields)


def _create(fields: dict) -> MealType:
    meal_type = MealType.model_validate(
        {**fields, "sort_order": meal_types_repo.chef_next_sort_order()}
    )
    try:
        return meal_types_repo.chef_create_meal_type(meal_type)
    except meal_types_repo.SlugTaken as exc:
        raise MealTypeFormError(
            f"Another meal type already uses the slug '{meal_type.slug}'."
        ) from exc


def _update(existing: MealType, fields: dict) -> MealType:
    update = {**fields, "updated_at": utcnow()}
    try:
        written = meal_types_repo.chef_update_meal_type(existing.id, update)
    except meal_types_repo.SlugTaken as exc:
        raise MealTypeFormError(
            f"Another meal type already uses the slug '{fields['slug']}'."
        ) from exc
    if not written:
        raise MealTypeFormError("That meal type no longer exists.")

    saved = get_meal_type(existing.id)
    if saved is None:
        raise MealTypeFormError("That meal type no longer exists.")
    return saved


def delete(meal_type: MealType) -> None:
    """Remove one meal type, unless a dish still points at it.

    The refusal names dishes rather than counting them, because "3 dishes
    use this" leaves the chef to find which three. It names at most
    `NAMED_IN_REFUSAL` and says how many more there are.
    """
    count = dishes_repo.chef_count_dishes_by_meal_type(meal_type.id)
    if count:
        raise MealTypeFormError(
            f"{meal_type.name} cannot be deleted while "
            f"{_dish_phrase(meal_type.id, count)}. Take the meal type off "
            "them first, on each dish's own editor."
        )
    if not meal_types_repo.chef_delete_meal_type(meal_type.id):
        raise MealTypeFormError("That meal type no longer exists.")


def _dish_phrase(meal_type_id: str, count: int) -> str:
    names = dishes_repo.chef_dish_names_by_meal_type(
        meal_type_id, limit=NAMED_IN_REFUSAL
    )
    listed = ", ".join(name for name in names if name)
    remaining = count - len(names)
    if remaining > 0:
        listed = f"{listed} and {remaining} other{'' if remaining == 1 else 's'}"
    verb = "uses" if count == 1 else "use"
    return f"{listed} {verb} it"


def move(meal_type: MealType, direction: str) -> None:
    """Swap this meal type's position with its neighbour.

    A swap rather than a renumber, the same shape as the catalogue
    editors': only two documents are written, and a label the chef has not
    touched keeps the number it had. The pair is renumbered from their
    positions rather than swapped blindly, because two labels may share a
    `sort_order` — the list falls back to name — and swapping equal
    numbers is a no-op that looks like a broken button.
    """
    if direction not in {"up", "down"}:
        raise MealTypeFormError("That is not a direction.")

    ordered = meal_types_repo.list_meal_types()
    index = next(
        (position for position, row in enumerate(ordered) if row.id == meal_type.id),
        None,
    )
    if index is None:
        raise MealTypeFormError("That meal type no longer exists.")

    target = index - 1 if direction == "up" else index + 1
    if not 0 <= target < len(ordered):
        # Already at the end it was asked to move towards. Not an error:
        # the control is not offered there, and a stale page that submits
        # it anyway should do nothing rather than complain.
        return

    neighbour = ordered[target]
    meal_types_repo.chef_set_sort_order(meal_type.id, target)
    meal_types_repo.chef_set_sort_order(neighbour.id, index)
