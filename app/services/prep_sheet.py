"""The prep sheet: one day's orders rolled up into a pick list.

04-WORKFLOWS.md asks for "a component-level pick list — quantities rolled
up across dishes and standalone components". Three things shape how that
is built here.

**A dish is rolled down to the components it references.** 01-DOMAIN.md
gives `component_refs` exactly this job — "provenance, kitchen prep and
'contains our harissa' style display" — so an ordered dish puts its
referenced components on the sheet as well as itself.

**Two kinds of demand are never added together.** `component_refs` is a
list of ids and carries no quantity, so there is no honest arithmetic
that turns "four portions of the tagine" into millilitres of harissa.
The sheet therefore reports a component's standalone units and the dish
portions that call for it as two separate figures, itemised by dish. A
single summed number would be invented, and an invented number on a
kitchen's pick list is worse than two true ones.

**The recipe is read live; the declaration is not.** The kitchen makes
what the dish is *now*, so `component_refs` comes from the current
document. That is the opposite of the order queue's allergen summary,
which reads the frozen snapshot because it is about what the customer
was told. Where the two disagree the sheet says so rather than choosing:
a dish whose recipe has changed since an order was placed is something
the chef has to look at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.db.repositories import components as components_repo
from app.db.repositories import dishes as dishes_repo
from app.db.repositories import orders as orders_repo
from app.db.repositories import users as users_repo
from app.models.catalogue import (
    COMPONENT_CATEGORY_LABELS,
    Component,
    Dish,
)
from app.models.orders import ItemType, Order


@dataclass
class DishDemand:
    """One dish to make, and how many portions of it."""

    item_id: str
    name: str
    portions: int = 0
    #: References the current dish document carries. Absent when the dish
    #: has been deleted since the order was placed.
    dish: Dish | None = None
    references: list["ComponentDemand"] = field(default_factory=list)

    @property
    def serves(self) -> int | None:
        return self.dish.serves if self.dish else None

    @property
    def is_missing(self) -> bool:
        """True when the dish is no longer in the catalogue.

        The portion still has to be cooked — the sheet names it from the
        order's own snapshot and says the recipe could not be read.
        """
        return self.dish is None


@dataclass
class ComponentDemand:
    """One component to make, and everything that is asking for it."""

    item_id: str
    name: str
    #: Units ordered on their own, in the component's own unit.
    standalone_units: int = 0
    unit_label: str = ""
    component: Component | None = None
    #: Dish name -> portions of that dish calling for this component.
    #: Deliberately not folded into `standalone_units`: a portion of a
    #: dish is not a unit of the component, and adding them would be
    #: arithmetic nobody can stand behind.
    for_dishes: dict[str, int] = field(default_factory=dict)

    @property
    def is_missing(self) -> bool:
        return self.component is None

    @property
    def dish_portions(self) -> int:
        return sum(self.for_dishes.values())

    @property
    def category_label(self) -> str:
        if self.component is None:
            return "Not in the catalogue"
        return COMPONENT_CATEGORY_LABELS[self.component.category]

    @property
    def is_standalone_only(self) -> bool:
        return bool(self.standalone_units) and not self.for_dishes


@dataclass(frozen=True)
class OrderOnSheet:
    """One order on the sheet, with the note the kitchen has to read.

    Dietary notes are on the order queue by name in 04-WORKFLOWS.md and
    not on the prep sheet, but the sheet is the page that goes to the
    bench — leaving "coeliac, no gluten at all" on a screen in another
    room is the wrong place for it. It is free text the customer wrote
    about their own eating, so it is set apart from anything that reads
    as a declaration and never rendered as an allergen.
    """

    order: Order
    dietary_notes: str | None = None


@dataclass(frozen=True)
class PrepSheet:
    """One day's work, as the kitchen reads it."""

    on: date
    orders: list[OrderOnSheet]
    dishes: list[DishDemand]
    components: list[ComponentDemand]

    @property
    def is_empty(self) -> bool:
        return not self.orders

    @property
    def order_count(self) -> int:
        return len(self.orders)

    @property
    def portion_count(self) -> int:
        return sum(
            line.quantity for entry in self.orders for line in entry.order.lines
        )


def parse_sheet_date(raw: str) -> date | None:
    """The date in the URL, or None when it is not one.

    A path segment that is not a date is a 404 at the route. Parsing it
    here keeps the rule beside the sheet it governs.
    """
    try:
        return date.fromisoformat(raw.strip())
    except (ValueError, AttributeError):
        return None


def build_sheet(on: date) -> PrepSheet:
    """Roll every order standing for `on` into a pick list."""
    orders = orders_repo.chef_list_orders_for_date(on)

    dish_ids = {
        line.item_id
        for order in orders
        for line in order.lines
        if line.item_type is ItemType.DISH
    }
    component_ids = {
        line.item_id
        for order in orders
        for line in order.lines
        if line.item_type is ItemType.COMPONENT
    }

    dish_documents = dishes_repo.chef_list_dishes_by_ids(dish_ids)

    # A dish's referenced components are needed too, and they may be
    # components nobody ordered on their own — so the component read
    # happens after the dishes are known, not alongside them.
    for dish in dish_documents.values():
        component_ids.update(dish.component_refs)
    component_documents = components_repo.chef_list_components_by_ids(component_ids)

    dishes: dict[str, DishDemand] = {}
    components: dict[str, ComponentDemand] = {}

    def component_demand(item_id: str, fallback_name: str) -> ComponentDemand:
        demand = components.get(item_id)
        if demand is None:
            document = component_documents.get(item_id)
            demand = ComponentDemand(
                item_id=item_id,
                name=document.name if document else fallback_name,
                unit_label=document.unit_label if document else "",
                component=document,
            )
            components[item_id] = demand
        return demand

    for order in orders:
        for line in order.lines:
            if line.item_type is ItemType.COMPONENT:
                demand = component_demand(line.item_id, line.name_snapshot)
                demand.standalone_units += line.quantity
                continue

            dish_demand = dishes.get(line.item_id)
            if dish_demand is None:
                document = dish_documents.get(line.item_id)
                dish_demand = DishDemand(
                    item_id=line.item_id,
                    name=document.name if document else line.name_snapshot,
                    dish=document,
                )
                dishes[line.item_id] = dish_demand
            dish_demand.portions += line.quantity

    # Second pass over the dishes, once every portion count is final: a
    # dish ordered on three separate orders must contribute its full
    # count to each component it references, not the first order's.
    for dish_demand in dishes.values():
        if dish_demand.dish is None:
            continue
        # A repeated reference is the same component, not twice as much
        # of it: `component_refs` is an authored list and may name one
        # component twice.
        seen: set[str] = set()
        for reference in dish_demand.dish.component_refs:
            if reference in seen:
                continue
            seen.add(reference)
            document = component_documents.get(reference)
            demand = component_demand(
                reference, document.name if document else "Unknown component"
            )
            demand.for_dishes[dish_demand.name] = (
                demand.for_dishes.get(dish_demand.name, 0) + dish_demand.portions
            )
            dish_demand.references.append(demand)

    customers = users_repo.chef_list_customers_by_ids(
        [order.user_id for order in orders]
    )

    return PrepSheet(
        on=on,
        orders=[
            OrderOnSheet(
                order=order,
                dietary_notes=(
                    customers[order.user_id].dietary_notes
                    if order.user_id in customers
                    else None
                )
                or None,
            )
            for order in orders
        ],
        # Name order, so the same day's sheet prints the same way twice.
        dishes=sorted(dishes.values(), key=lambda demand: demand.name.lower()),
        components=sorted(
            components.values(), key=lambda demand: demand.name.lower()
        ),
    )
