"""The chef's catalogue editors: create, edit, archive, reorder, availability.

04-WORKFLOWS.md gives `/chef/components` and `/chef/dishes` full CRUD and
puts optional component linking on the dish editor. Four rules shape what
that is allowed to touch.

**This editor never writes an allergen field.** 01-DOMAIN.md: "Allergen
fields are never modified by any code path except the chef allergen
editor." So every write here is an explicit `$set` of the fields this form
owns, never a whole-document replace — a replace would carry a stale copy
of `allergens` back over a review made in another tab, which is exactly
the failure the rule exists to prevent. The allergen block is read for
validation and written back never.

**Editing ingredients flags the review stale, and nothing more.** The item
is not invalidated and not unpublished (04-WORKFLOWS.md); the timestamp
moves and `ItemBase.allergen_review_is_stale` derives the rest. The stamp
moves only when the ingredients actually changed, because a save that
touched the price would otherwise demand a re-review of a declaration
nobody altered.

**Publication is the model's to refuse.** `is_available` on an unreviewed
item is a validation error in `ItemBase`, not a filter here. This service
turns that refusal into a sentence the chef can act on.

**Money is integer minor units, start to finish.** The form takes what a
person types — "14.50" — and converts it by splitting the string. No
float touches a price at any point (00-SYSTEM.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.db.repositories import components as components_repo
from app.db.repositories import dishes as dishes_repo
from app.db.repositories import meal_types as meal_types_repo
from app.models.base import utcnow
from app.models.catalogue import (
    KNOWN_PREFERENCE_FLAGS,
    Component,
    ComponentCategory,
    Dish,
    Ingredient,
    ItemBase,
    PreparationBlock,
    ReheatMethod,
    StorageBlock,
    StorageMethod,
    Unit,
    preference_flag_label,
)

COMPONENT = "component"
DISH = "dish"
KINDS = (COMPONENT, DISH)

#: The URL segment for each catalogue, spelled out rather than derived.
#: "dish" + "s" is "dishs", and a route built that way 404s on every dish
#: page while the component pages work — which is exactly the kind of bug
#: that reaches production because half the surface is fine.
PLURAL: dict[str, str] = {COMPONENT: "components", DISH: "dishes"}
KIND_BY_PLURAL: dict[str, str] = {value: key for key, value in PLURAL.items()}

#: Blank rows offered beyond what is already stored, so a chef can add
#: ingredients or steps without any JavaScript. Running out means saving
#: and getting another three — which is a slower path than a scripted
#: "add row", and a working one, which is the trade 03-FRONTEND.md makes.
BLANK_ROWS = 3

MAX_NAME = 200
MAX_SLUG = 200
MAX_SUMMARY = 300
MAX_DESCRIPTION = 5000
MAX_TEXT = 500
MAX_STEPS = 60
MAX_INGREDIENTS = 100
#: A price nobody will type by accident. $10,000 in cents.
MAX_PRICE_CENTS = 1_000_000


class ItemFormError(ValueError):
    """A refusal the chef can fix, with the wording to show them."""


def _repo(kind: str):
    return components_repo if kind == COMPONENT else dishes_repo


def _model(kind: str) -> type[ItemBase]:
    return Component if kind == COMPONENT else Dish


# --- parsing ----------------------------------------------------------------


def slugify(raw: str) -> str:
    """A slug in the shape `ItemBase.slug` accepts, or ''.

    Offered when the chef leaves the field blank. Never applied over a
    slug they typed, and never re-derived on an edit: a slug is in the
    customer-facing URL, and silently changing it when a name is
    corrected breaks every link anybody saved.
    """
    lowered = re.sub(r"[^a-z0-9]+", "-", raw.strip().lower())
    return lowered.strip("-")


def parse_price_cents(raw: str | None) -> int:
    """Dollars as typed to integer cents, without a float anywhere.

    `float("14.50") * 100` is 1449.9999999999998, and 01-DOMAIN.md says
    prices are integer minor units and never a float. So the string is
    split on the point and the two halves are read as integers.
    """
    text = (raw or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not text:
        raise ItemFormError("Give the item a price, even if it is 0.")

    whole, point, fraction = text.partition(".")
    if point and len(fraction) > 2:
        raise ItemFormError("A price has at most two decimal places.")
    whole = whole or "0"
    fraction = (fraction + "00")[:2] if point else "00"
    if not (whole.isdigit() and fraction.isdigit()):
        raise ItemFormError("A price is a number, like 14.50.")

    cents = int(whole) * 100 + int(fraction)
    if cents > MAX_PRICE_CENTS:
        raise ItemFormError("That price looks wrong — check it and try again.")
    return cents


def format_price_input(cents: int) -> str:
    """Cents back into the form field, as the chef would type it."""
    return f"{cents // 100}.{cents % 100:02d}"


def _bounded(raw: str | None, limit: int, field: str) -> str:
    value = (raw or "").strip()
    if len(value) > limit:
        raise ItemFormError(f"{field} is longer than {limit} characters.")
    return value


def _optional(raw: str | None, limit: int, field: str) -> str | None:
    return _bounded(raw, limit, field) or None


def _int(raw: str | None, field: str, *, minimum: int = 0, maximum: int | None = None):
    text = (raw or "").strip()
    if not text:
        return None
    if not text.isdigit():
        raise ItemFormError(f"{field} is a whole number.")
    value = int(text)
    if value < minimum or (maximum is not None and value > maximum):
        raise ItemFormError(f"{field} is out of range.")
    return value


def _indexed(form, prefix: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
    """Rows posted as `prefix-0-name`, `prefix-1-name`, … in index order.

    Read by index rather than by `getlist`, so a row left blank in the
    middle cannot silently shift a note onto somebody else's ingredient.
    """
    indices = set()
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)-(?:{'|'.join(fields)})$")
    for key in form:
        match = pattern.match(key)
        if match:
            indices.add(int(match.group(1)))
    return [
        {field: (form.get(f"{prefix}-{index}-{field}") or "").strip() for field in fields}
        for index in sorted(indices)
    ]


def parse_ingredients(form) -> list[Ingredient]:
    """The ingredients tab. Authored order is kept, never alphabetised."""
    rows = _indexed(form, "ingredient", ("name", "quantity", "note", "optional"))
    ingredients: list[Ingredient] = []
    for row in rows:
        if not row["name"]:
            # A row with a quantity but no name is a half-typed line, not
            # an ingredient. Saying so beats dropping it quietly.
            if row["quantity"] or row["note"]:
                raise ItemFormError(
                    "An ingredient row has a quantity or a note but no name."
                )
            continue
        ingredients.append(
            Ingredient(
                name=_bounded(row["name"], MAX_TEXT, "An ingredient name"),
                quantity=_optional(row["quantity"], MAX_TEXT, "A quantity"),
                note=_optional(row["note"], MAX_TEXT, "An ingredient note"),
                is_optional=bool(row["optional"]),
            )
        )
    if len(ingredients) > MAX_INGREDIENTS:
        raise ItemFormError(f"An item holds at most {MAX_INGREDIENTS} ingredients.")
    return ingredients


def parse_storage(form) -> StorageBlock | None:
    """The storage tab, or None when the chef has not filled it in.

    `ItemBase.storage` is optional, so a half-filled block is refused
    rather than defaulted: a shelf life of zero days that nobody typed
    is a use-by date the kitchen would stand behind by accident.
    """
    method = (form.get("storage-method") or "").strip()
    if not method:
        return None
    try:
        parsed_method = StorageMethod(method)
    except ValueError as exc:
        raise ItemFormError("That is not a storage method.") from exc

    days = _int(form.get("storage-shelf-life-days"), "A shelf life", maximum=3650)
    if days is None:
        raise ItemFormError(
            "A storage method needs a shelf life in days — it is what the "
            "customer's use-by is counted from."
        )
    temperature = _bounded(form.get("storage-temperature"), 60, "A temperature")
    if not temperature:
        raise ItemFormError("A storage method needs a temperature, like 0-4.")

    freezable = bool(form.get("storage-freezable"))
    freezer_days = _int(
        form.get("storage-freezer-life-days"), "A freezer life", maximum=3650
    )
    if freezer_days is not None and not freezable:
        raise ItemFormError(
            "A freezer life only means something on an item marked freezable."
        )

    return StorageBlock(
        method=parsed_method,
        temperature_c=temperature,
        shelf_life_days=days,
        shelf_life_note=_optional(
            form.get("storage-shelf-life-note"), MAX_TEXT, "A shelf-life note"
        ),
        freezable=freezable,
        freezer_life_days=freezer_days,
    )


def parse_preparation(form) -> PreparationBlock | None:
    """The preparation tab, or None when nothing in it was filled in."""
    rows = _indexed(form, "step", ("text",))
    steps = [
        _bounded(row["text"], MAX_TEXT, "A step") for row in rows if row["text"]
    ]
    if len(steps) > MAX_STEPS:
        raise ItemFormError(f"An item holds at most {MAX_STEPS} steps.")

    method = (form.get("prep-reheat-method") or "").strip()
    parsed_method: ReheatMethod | None = None
    if method:
        try:
            parsed_method = ReheatMethod(method)
        except ValueError as exc:
            raise ItemFormError("That is not a reheating method.") from exc

    minutes = _int(form.get("prep-reheat-minutes"), "Reheating minutes", maximum=600)
    note = _optional(form.get("prep-reheat-note"), MAX_TEXT, "A reheating note")
    suggestion = _optional(
        form.get("prep-serving-suggestion"), MAX_TEXT, "A serving suggestion"
    )

    if not any([steps, parsed_method, minutes, note, suggestion]):
        return None
    return PreparationBlock(
        steps=steps,
        reheat_method=parsed_method,
        reheat_minutes=minutes,
        reheat_note=note,
        serving_suggestion=suggestion,
    )


def parse_preference_flags(form, *, stored: list[str]) -> list[str]:
    """Ticked flags plus any new ones typed in.

    The vocabulary is chef-extensible (01-DOMAIN.md), so a free-text
    field adds to it. Flags are normalised the way a slug is, so
    "High Protein" and "high_protein" cannot both exist and split a
    filter between them.
    """
    # Ticked boxes are checked against what the form actually offered, so
    # a posted value nobody was shown is dropped rather than stored.
    offered = set(offered_flags(stored))
    chosen = [flag for flag in form.getlist("preference") if flag in offered]

    # Typed flags are new by definition, so they are normalised rather
    # than checked against the existing set — the vocabulary is
    # chef-extensible (01-DOMAIN.md). Normalising the way a slug is
    # normalised stops "High Protein" and "high_protein" both existing
    # and splitting one filter between them.
    for raw in re.split(r"[,\n]", form.get("preference-new") or ""):
        flag = slugify(raw).replace("-", "_")
        if not flag:
            continue
        if len(flag) > 40:
            raise ItemFormError("A preference flag is at most 40 characters.")
        chosen.append(flag)

    return list(dict.fromkeys(chosen))


def offered_flags(stored: list[str]) -> list[str]:
    """Every flag the form may set: the known set, plus what is in use."""
    return list(dict.fromkeys([*KNOWN_PREFERENCE_FLAGS, *stored]))


def parse_item_form(kind: str, form, *, existing: ItemBase | None) -> dict[str, Any]:
    """Everything the editor owns, parsed and bounded. Never a raw form.

    Deliberately returns a plain dict rather than a model: the caller
    validates it against the *stored* allergen block, and writes back
    only these keys.
    """
    name = _bounded(form.get("name"), MAX_NAME, "The name")
    if not name:
        raise ItemFormError("Give the item a name.")

    slug = _bounded(form.get("slug"), MAX_SLUG, "The slug") or slugify(name)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ItemFormError(
            "A slug is lowercase letters, numbers and single hyphens, "
            "like 'harissa-rosa'."
        )

    stored_flags = list(existing.preference_flags) if existing else []

    fields: dict[str, Any] = {
        "name": name,
        "slug": slug,
        "summary": _bounded(form.get("summary"), MAX_SUMMARY, "The summary"),
        "description": _bounded(
            form.get("description"), MAX_DESCRIPTION, "The description"
        ),
        "image_path": _optional(form.get("image_path"), MAX_TEXT, "The image path"),
        "price_cents": parse_price_cents(form.get("price")),
        "unit": _unit(form.get("unit")),
        "ingredients": parse_ingredients(form),
        "storage": parse_storage(form),
        "preparation": parse_preparation(form),
        "preference_flags": parse_preference_flags(form, stored=stored_flags),
        "spice_level": _int(form.get("spice_level"), "The spice level", maximum=5) or 0,
    }

    if kind == COMPONENT:
        fields["category"] = _component_category(form.get("category")).value
    else:
        fields["category"] = _bounded(form.get("category"), MAX_NAME, "The category")
        fields["serves"] = _int(form.get("serves"), "Serves", minimum=1, maximum=99) or 1
        fields["meal_type_ids"] = _known_meal_type_ids(form.getlist("meal_type"))
        fields["component_refs"] = _known_component_refs(form.getlist("component_ref"))

    return fields


def _unit(raw: str | None) -> str:
    try:
        return Unit((raw or "").strip()).value
    except ValueError as exc:
        raise ItemFormError("That is not a unit.") from exc


def _component_category(raw: str | None) -> ComponentCategory:
    try:
        return ComponentCategory((raw or "").strip())
    except ValueError as exc:
        raise ItemFormError("That is not a component category.") from exc


def _known_meal_type_ids(raw: list[str]) -> list[str]:
    """Only meal types that exist. A stale id is dropped, not stored."""
    known = {meal_type.id for meal_type in meal_types_repo.list_meal_types()}
    return [value for value in dict.fromkeys(raw) if value in known]


def _known_component_refs(raw: list[str]) -> list[str]:
    """Only components that exist, in the order they were ticked.

    A reference is provenance and kitchen prep (01-DOMAIN.md), never a
    substitute for the dish's own tabs — so a dead id is dropped rather
    than stored to render as a broken link later.
    """
    wanted = list(dict.fromkeys(value for value in raw if value))
    if not wanted:
        return []
    known = components_repo.chef_list_components_by_ids(wanted)
    return [value for value in wanted if value in known]


# --- reading ----------------------------------------------------------------


@dataclass(frozen=True)
class AdminRow:
    """One item in the editor list, with the state the chef acts on."""

    item: ItemBase
    kind: str

    @property
    def status_label(self) -> str:
        """What this item is, in words. Never a colour on its own."""
        if self.item.is_archived:
            return "Archived"
        if not self.item.allergens.is_reviewed:
            return "Draft — allergens not reviewed"
        if not self.item.is_available:
            return "Draft — not available"
        return "Published"

    @property
    def status_key(self) -> str:
        if self.item.is_archived:
            return "archived"
        if not self.item.allergens.is_reviewed:
            return "unreviewed"
        return "available" if self.item.is_available else "draft"

    @property
    def can_be_made_available(self) -> bool:
        """Publication is gated on the allergen review (01-DOMAIN.md)."""
        return self.item.allergens.is_reviewed and not self.item.is_archived


@dataclass(frozen=True)
class AdminList:
    kind: str
    rows: list[AdminRow]
    include_archived: bool

    @property
    def plural_url(self) -> str:
        return PLURAL[self.kind]

    @property
    def is_empty(self) -> bool:
        return not self.rows


def admin_list(kind: str, *, include_archived: bool = False) -> AdminList:
    items = (
        components_repo.chef_list_components(include_archived)
        if kind == COMPONENT
        else dishes_repo.chef_list_dishes(include_archived)
    )
    return AdminList(
        kind=kind,
        rows=[AdminRow(item=item, kind=kind) for item in items],
        include_archived=include_archived,
    )


def get_item(kind: str, item_id: str) -> ItemBase | None:
    return (
        components_repo.chef_get_component(item_id)
        if kind == COMPONENT
        else dishes_repo.chef_get_dish(item_id)
    )


# --- writing ----------------------------------------------------------------


def _validate(kind: str, fields: dict[str, Any], existing: ItemBase | None) -> ItemBase:
    """Run the parsed fields through the model that owns the schema.

    The stored allergen block and the stored availability come along so
    the publication rule is checked against reality — an edit to a
    published item must not be allowed to slip past the gate simply
    because the form does not carry `is_available`.
    """
    document: dict[str, Any] = {
        # A new item has no declaration yet, so it validates against an
        # empty block — and an empty block is unreviewed, which is why a
        # new item cannot be created already available.
        "allergens": existing.allergens if existing else {},
        "is_available": existing.is_available if existing else False,
        "is_archived": existing.is_archived if existing else False,
        "sort_order": existing.sort_order if existing else 0,
        **fields,
    }
    try:
        return _model(kind).model_validate(document)
    except ValidationError as exc:
        raise ItemFormError(_first_message(exc)) from exc


def _first_message(error: ValidationError) -> str:
    first = error.errors()[0]
    message = first.get("msg", "That is not valid.")
    return message.removeprefix("Value error, ")


def save_item(kind: str, item_id: str | None, form) -> ItemBase:
    """Create or update one item. Raises `ItemFormError` on a refusal."""
    existing = get_item(kind, item_id) if item_id else None
    if item_id and existing is None:
        raise ItemFormError("That item no longer exists.")

    fields = parse_item_form(kind, form, existing=existing)
    validated = _validate(kind, fields, existing)

    if existing is None:
        return _create(kind, validated, fields)
    return _update(kind, existing, fields)


def _create(kind: str, validated: ItemBase, fields: dict[str, Any]) -> ItemBase:
    repo = _repo(kind)
    payload = validated.model_copy(
        update={
            "sort_order": repo.chef_next_sort_order(),
            # A new item's ingredients were written now, so the stamp is
            # now. It is never stale on creation — there is no review yet
            # for it to be stale against.
            "ingredients_updated_at": utcnow() if fields["ingredients"] else None,
        }
    )
    try:
        return (
            components_repo.chef_create_component(payload)
            if kind == COMPONENT
            else dishes_repo.chef_create_dish(payload)
        )
    except repo.SlugTaken as exc:
        raise ItemFormError(
            f"Another item already uses the slug '{payload.slug}'."
        ) from exc


def _update(kind: str, existing: ItemBase, fields: dict[str, Any]) -> ItemBase:
    repo = _repo(kind)
    update = {
        key: _encode(value) for key, value in fields.items()
    }
    update["updated_at"] = utcnow()

    # The stamp moves only when the ingredients actually changed. A save
    # that corrected a price would otherwise demand a re-review of a
    # declaration nobody touched — and a prompt that fires on every save
    # is a prompt the chef learns to dismiss.
    if fields["ingredients"] != existing.ingredients:
        update["ingredients_updated_at"] = utcnow()

    try:
        written = (
            components_repo.chef_update_component(existing.id, update)
            if kind == COMPONENT
            else dishes_repo.chef_update_dish(existing.id, update)
        )
    except repo.SlugTaken as exc:
        raise ItemFormError(
            f"Another item already uses the slug '{fields['slug']}'."
        ) from exc
    if not written:
        raise ItemFormError("That item no longer exists.")

    saved = get_item(kind, existing.id)
    if saved is None:
        raise ItemFormError("That item no longer exists.")
    return saved


def _encode(value: Any) -> Any:
    """Models to documents. `$set` takes plain values, not Pydantic."""
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    return value


def set_availability(kind: str, item: ItemBase, available: bool) -> None:
    """Toggle `is_available`, refusing what the model would refuse.

    The check is here *and* in the model. The model's is the rule; this
    one turns it into a sentence the chef can act on rather than a 500
    (01-DOMAIN.md gates publication on the allergen review).
    """
    if available and not item.allergens.is_reviewed:
        raise ItemFormError(
            f"{item.name} cannot be made available until its allergen "
            "declaration has been reviewed."
        )
    if available and item.is_archived:
        raise ItemFormError(
            f"{item.name} is archived. Restore it before making it available."
        )
    _write_flags(kind, item, {"is_available": available})


def set_archived(kind: str, item: ItemBase, archived: bool) -> None:
    """Archive or restore. Archiving withdraws the item from customers.

    Archiving also clears `is_available`: 01-DOMAIN.md derives publication
    from both flags, and leaving an archived item marked available stores
    a contradiction that only shows up the day somebody restores it.
    """
    update: dict[str, Any] = {"is_archived": archived}
    if archived:
        update["is_available"] = False
    _write_flags(kind, item, update)


def _write_flags(kind: str, item: ItemBase, update: dict[str, Any]) -> None:
    update["updated_at"] = utcnow()
    written = (
        components_repo.chef_update_component(item.id, update)
        if kind == COMPONENT
        else dishes_repo.chef_update_dish(item.id, update)
    )
    if not written:
        raise ItemFormError("That item no longer exists.")


def move(kind: str, item: ItemBase, direction: str) -> None:
    """Swap this item's position with its neighbour in the chef's order.

    A swap rather than a renumber: only two documents are written, and an
    item the chef has not touched keeps the number it had.
    """
    if direction not in {"up", "down"}:
        raise ItemFormError("That is not a direction.")

    listing = admin_list(kind, include_archived=True).rows
    index = next(
        (position for position, row in enumerate(listing) if row.item.id == item.id),
        None,
    )
    if index is None:
        raise ItemFormError("That item no longer exists.")

    target = index - 1 if direction == "up" else index + 1
    if not 0 <= target < len(listing):
        # Already at the end it was asked to move towards. Not an error —
        # the control is simply not offered there, and a stale page that
        # submits it anyway should do nothing rather than complain.
        return

    neighbour = listing[target].item
    repo_set = (
        components_repo.chef_set_sort_order
        if kind == COMPONENT
        else dishes_repo.chef_set_sort_order
    )
    # The two may share a `sort_order` — the list falls back to name — so
    # the pair is renumbered from their positions rather than swapped
    # blindly, which would be a no-op whenever they were equal.
    repo_set(item.id, target)
    repo_set(neighbour.id, index)


# --- form rendering ---------------------------------------------------------


@dataclass(frozen=True)
class FormChoice:
    value: str
    label: str
    selected: bool = False


def form_context(kind: str, item: ItemBase | None) -> dict[str, Any]:
    """Everything the editor template renders from."""
    stored_flags = list(item.preference_flags) if item else []
    selected_flags = set(stored_flags)
    selected_meal_types = set(getattr(item, "meal_type_ids", []) or [])
    selected_refs = list(getattr(item, "component_refs", []) or [])

    return {
        "kind": kind,
        "plural_url": PLURAL[kind],
        "item": item,
        "units": [
            FormChoice(unit.value, unit.value, item is not None and item.unit is unit)
            for unit in Unit
        ],
        "categories": [
            FormChoice(
                category.value,
                category.value.replace("_", " ").capitalize(),
                item is not None and item.category == category.value,
            )
            for category in ComponentCategory
        ],
        "storage_methods": [
            FormChoice(
                method.value,
                method.value.replace("_", " ").capitalize(),
                item is not None
                and item.storage is not None
                and item.storage.method is method,
            )
            for method in StorageMethod
        ],
        "reheat_methods": [
            FormChoice(
                method.value,
                method.value.replace("_", " ").capitalize(),
                item is not None
                and item.preparation is not None
                and item.preparation.reheat_method is method,
            )
            for method in ReheatMethod
        ],
        "preference_choices": [
            FormChoice(flag, preference_flag_label(flag), flag in selected_flags)
            for flag in offered_flags(stored_flags)
        ],
        "meal_types": [
            FormChoice(
                meal_type.id, meal_type.name, meal_type.id in selected_meal_types
            )
            for meal_type in meal_types_repo.list_meal_types()
        ],
        "linkable_components": [
            FormChoice(
                component.id,
                f"{component.name} ({component.category.value})",
                component.id in selected_refs,
            )
            for component in components_repo.chef_list_components()
        ],
        "blank_rows": BLANK_ROWS,
        "price_value": format_price_input(item.price_cents) if item else "",
        "limits": {
            "name": MAX_NAME,
            "summary": MAX_SUMMARY,
            "description": MAX_DESCRIPTION,
            "text": MAX_TEXT,
        },
    }
