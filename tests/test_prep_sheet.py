"""The prep sheet.

04-WORKFLOWS.md: "Aggregates all orders for a date into a component-level
pick list — quantities rolled up across dishes and standalone components.
Printable, plain layout, no dependence on colour."

The rollup is the part worth testing hard. A dish contributes its
referenced components (01-DOMAIN.md gives `component_refs` the kitchen-prep
job), portions accumulate across separate orders, a component ordered both
ways is counted both ways without the two being added together, and a
cancelled order never reaches the sheet.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.catalogue import Component, Dish
from app.models.users import Role, User
from app.security.passwords import hash_password
from app.services import accounts, prep_sheet

PASSWORD = "a-long-enough-passphrase"

REVIEWED = {
    "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef",
    "contains": ["milk"],
}

WHEN = date.today() + timedelta(days=3)
ANOTHER_DAY = date.today() + timedelta(days=4)


@pytest.fixture()
def chef(db):
    user = User(
        email="chef@example.com",
        password_hash=hash_password(PASSWORD),
        display_name="Chef",
        role=Role.CHEF_ADMIN,
    )
    db["users"].insert_one(user.to_mongo())
    return user


def _component(db, *, name, slug, unit="250ml", price=850) -> str:
    item = Component.model_validate(
        {
            "name": name, "slug": slug, "category": "sauce", "price_cents": price,
            "unit": unit, "is_available": True, "allergens": dict(REVIEWED),
        }
    )
    return str(db["components"].insert_one(item.to_mongo()).inserted_id)


def _dish(db, *, name, slug, refs, serves=2, price=2400) -> str:
    item = Dish.model_validate(
        {
            "name": name, "slug": slug, "category": "main", "price_cents": price,
            "unit": "portion", "is_available": True, "serves": serves,
            "component_refs": refs, "allergens": dict(REVIEWED),
        }
    )
    return str(db["dishes"].insert_one(item.to_mongo()).inserted_id)


def _register(app, email, **extra):
    with app.app_context():
        accounts.register_customer(
            email=email, password=PASSWORD,
            password_confirmation=PASSWORD, **extra,
        )


def _sign_in(client, email):
    return client.post("/login", data={"email": email, "password": PASSWORD})


def _order(client, lines, *, when=WHEN) -> str:
    """Check out a cart of (item_type, item_id, quantity) lines."""
    for item_type, item_id, quantity in lines:
        client.post(
            "/cart/add",
            data={"item_type": item_type, "item_id": item_id, "quantity": str(quantity)},
        )
    page = client.get("/checkout").get_data(as_text=True)

    def hidden(name):
        match = re.search(rf'name="{re.escape(name)}" value="([^"]*)"', page)
        return match.group(1) if match else ""

    response = client.post(
        "/checkout",
        data={
            "requested_for": when.isoformat(),
            "fulfilment": "collection",
            "checkout_token": hidden("checkout_token"),
            "review_digest": hidden("review_digest"),
        },
    )
    return response.headers["Location"].rsplit("/", 1)[-1]


# --- the page belongs to the chef -------------------------------------------


def test_the_prep_sheet_requires_the_chef(client, app):
    assert client.get(f"/chef/prep/{WHEN.isoformat()}").status_code == 302

    _register(app, "ada@example.com")
    _sign_in(client, "ada@example.com")
    assert client.get(f"/chef/prep/{WHEN.isoformat()}").status_code == 404
    assert client.get("/chef/prep").status_code == 404


def test_a_path_that_is_not_a_date_is_a_404(client, chef):
    """Never a quiet redirect to today: that is how the wrong day is prepped."""
    _sign_in(client, "chef@example.com")

    assert client.get("/chef/prep/not-a-date").status_code == 404
    assert client.get("/chef/prep/2026-13-45").status_code == 404


def test_bare_prep_goes_to_today_and_a_query_goes_to_that_day(client, chef, app):
    _sign_in(client, "chef@example.com")

    with app.app_context():
        from app.services.dates import business_today

        today = business_today()

    response = client.get("/chef/prep")
    assert response.headers["Location"].endswith(f"/chef/prep/{today.isoformat()}")

    response = client.get(f"/chef/prep?on={WHEN.isoformat()}")
    assert response.headers["Location"].endswith(f"/chef/prep/{WHEN.isoformat()}")


def test_a_day_with_no_orders_is_an_empty_sheet_not_a_404(client, chef):
    _sign_in(client, "chef@example.com")
    response = client.get(f"/chef/prep/{WHEN.isoformat()}")

    assert response.status_code == 200
    assert "No orders stand for this date" in response.get_data(as_text=True)


# --- the rollup -------------------------------------------------------------


def test_a_dish_puts_its_referenced_components_on_the_sheet(client, app, db, chef):
    harissa = _component(db, name="Harissa", slug="harissa")
    yoghurt = _component(db, name="Yoghurt", slug="yoghurt")
    tagine = _dish(db, name="Lamb tagine", slug="tagine", refs=[harissa, yoghurt])

    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    _order(client, [("dish", tagine, 4)])
    client.post("/logout")

    with app.app_context():
        sheet = prep_sheet.build_sheet(WHEN)

    assert [d.name for d in sheet.dishes] == ["Lamb tagine"]
    assert sheet.dishes[0].portions == 4

    by_name = {c.name: c for c in sheet.components}
    assert set(by_name) == {"Harissa", "Yoghurt"}
    # Nobody ordered harissa on its own, so its own-unit count stays zero.
    assert by_name["Harissa"].standalone_units == 0
    assert by_name["Harissa"].for_dishes == {"Lamb tagine": 4}


def test_portions_accumulate_across_separate_orders(client, app, db, chef):
    harissa = _component(db, name="Harissa", slug="harissa")
    tagine = _dish(db, name="Lamb tagine", slug="tagine", refs=[harissa])

    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    _order(client, [("dish", tagine, 2)])
    _order(client, [("dish", tagine, 3)])
    client.post("/logout")

    with app.app_context():
        sheet = prep_sheet.build_sheet(WHEN)

    assert sheet.dishes[0].portions == 5
    # The full count, not the first order's: the rollup runs after every
    # portion is counted.
    assert sheet.components[0].for_dishes == {"Lamb tagine": 5}


def test_the_two_kinds_of_demand_are_counted_separately(client, app, db, chef):
    """A jar sold on its own is not a portion of a dish, and vice versa.

    `component_refs` carries no per-dish quantity, so summing the two
    would be arithmetic nobody can stand behind.
    """
    harissa = _component(db, name="Harissa", slug="harissa", unit="250ml")
    tagine = _dish(db, name="Lamb tagine", slug="tagine", refs=[harissa])

    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    _order(client, [("dish", tagine, 3), ("component", harissa, 2)])
    client.post("/logout")

    with app.app_context():
        sheet = prep_sheet.build_sheet(WHEN)

    demand = sheet.components[0]
    assert demand.standalone_units == 2
    assert demand.dish_portions == 3
    assert demand.unit_label == "per 250ml"


def test_a_component_referenced_twice_by_one_dish_counts_once(
    client, app, db, chef
):
    harissa = _component(db, name="Harissa", slug="harissa")
    tagine = _dish(db, name="Lamb tagine", slug="tagine", refs=[harissa, harissa])

    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    _order(client, [("dish", tagine, 2)])
    client.post("/logout")

    with app.app_context():
        sheet = prep_sheet.build_sheet(WHEN)

    assert sheet.components[0].for_dishes == {"Lamb tagine": 2}


def test_a_cancelled_order_is_not_on_the_sheet(client, app, db, chef):
    """The one thing the kitchen must not cook."""
    harissa = _component(db, name="Harissa", slug="harissa")

    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    standing = _order(client, [("component", harissa, 1)])
    scrapped = _order(client, [("component", harissa, 9)])
    client.post(f"/account/orders/{scrapped}/cancel")
    client.post("/logout")

    with app.app_context():
        sheet = prep_sheet.build_sheet(WHEN)

    assert [entry.order.reference for entry in sheet.orders] == [standing]
    assert sheet.components[0].standalone_units == 1


def test_another_days_orders_are_not_on_this_sheet(client, app, db, chef):
    harissa = _component(db, name="Harissa", slug="harissa")

    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    today_order = _order(client, [("component", harissa, 1)])
    _order(client, [("component", harissa, 5)], when=ANOTHER_DAY)
    client.post("/logout")

    with app.app_context():
        sheet = prep_sheet.build_sheet(WHEN)

    assert [entry.order.reference for entry in sheet.orders] == [today_order]
    assert sheet.components[0].standalone_units == 1


def test_the_sheet_reads_the_current_recipe_not_the_snapshot(
    client, app, db, chef
):
    """The kitchen makes the dish as it is now.

    The opposite of the allergen summary, which reads the frozen block
    because that is what the customer was told.
    """
    harissa = _component(db, name="Harissa", slug="harissa")
    zhoug = _component(db, name="Zhoug", slug="zhoug")
    tagine = _dish(db, name="Lamb tagine", slug="tagine", refs=[harissa])

    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    _order(client, [("dish", tagine, 1)])
    client.post("/logout")

    from bson import ObjectId

    db["dishes"].update_one(
        {"_id": ObjectId(tagine)}, {"$set": {"component_refs": [zhoug]}}
    )

    with app.app_context():
        sheet = prep_sheet.build_sheet(WHEN)

    assert [c.name for c in sheet.components] == ["Zhoug"]


def test_an_item_withdrawn_since_the_order_is_still_on_the_sheet(
    client, app, db, chef
):
    """A withdrawn item is still a portion somebody has to cook."""
    harissa = _component(db, name="Harissa", slug="harissa")

    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    _order(client, [("component", harissa, 2)])
    client.post("/logout")

    from bson import ObjectId

    db["components"].update_one(
        {"_id": ObjectId(harissa)},
        {"$set": {"is_archived": True, "is_available": False}},
    )

    with app.app_context():
        sheet = prep_sheet.build_sheet(WHEN)

    assert sheet.components[0].name == "Harissa"
    assert sheet.components[0].standalone_units == 2


def test_a_deleted_item_is_named_from_the_order(client, app, db, chef):
    harissa = _component(db, name="Harissa", slug="harissa")

    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    _order(client, [("component", harissa, 1)])
    client.post("/logout")

    from bson import ObjectId

    db["components"].delete_one({"_id": ObjectId(harissa)})

    with app.app_context():
        sheet = prep_sheet.build_sheet(WHEN)

    assert sheet.components[0].name == "Harissa"
    assert sheet.components[0].is_missing


# --- what the page shows ----------------------------------------------------


def test_the_page_renders_both_pick_lists_and_the_orders(client, app, db, chef):
    harissa = _component(db, name="Harissa", slug="harissa")
    tagine = _dish(db, name="Lamb tagine", slug="tagine", refs=[harissa])

    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    reference = _order(client, [("dish", tagine, 2), ("component", harissa, 1)])
    client.post("/logout")

    _sign_in(client, "chef@example.com")
    page = client.get(f"/chef/prep/{WHEN.isoformat()}").get_data(as_text=True)

    assert "Dishes to make" in page
    assert "Components to make" in page
    assert "Lamb tagine" in page
    assert "Harissa" in page
    assert reference in page
    # The two figures are never presented as one.
    assert "not added" in page


def test_dietary_notes_reach_the_bench(client, app, db, chef):
    """The sheet is the page that gets printed and carried."""
    harissa = _component(db, name="Harissa", slug="harissa")

    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    db["users"].update_one(
        {"email": "ada@example.com"},
        {"$set": {"dietary_notes": "Coeliac — no gluten at all."}},
    )
    _order(client, [("component", harissa, 1)])
    client.post("/logout")

    _sign_in(client, "chef@example.com")
    page = client.get(f"/chef/prep/{WHEN.isoformat()}").get_data(as_text=True)

    assert "Dietary notes" in page
    assert "Coeliac — no gluten at all." in page


def test_the_sheet_needs_no_javascript(client, chef, app, db):
    harissa = _component(db, name="Harissa", slug="harissa")
    _register(app, "ada@example.com", display_name="Ada")
    _sign_in(client, "ada@example.com")
    _order(client, [("component", harissa, 1)])
    client.post("/logout")

    _sign_in(client, "chef@example.com")
    page = client.get(f"/chef/prep/{WHEN.isoformat()}").get_data(as_text=True)

    # The sheet mutates nothing: its only form is the GET date picker.
    # The one POST on the page is the header's sign-out, which every page
    # carries.
    assert page.count('method="post"') == 1
    assert 'action="/logout"' in page
    assert 'method="get"' in page
    assert "onclick" not in page
