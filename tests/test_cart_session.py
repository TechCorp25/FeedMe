"""Where the cart is kept, and what it looks like resolved.

01-DOMAIN.md names six collections and none of them is a cart, so a guest
cart lives in the signed session cookie. What it holds is ids and
quantities; every price a customer is shown is read from the catalogue on
the server, so a tampered cookie cannot alter one.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.catalogue import Component, Dish
from app.models.orders import ItemType
from app.services import cart as cart_service

REVIEWED = {
    "contains": [],
    "may_contain": [],
    "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef",
}


def insert_component(db, **overrides) -> str:
    data = {
        "name": "Harissa",
        "slug": "harissa",
        "category": "sauce",
        "price_cents": 850,
        "unit": "250ml",
        "is_available": True,
        "is_archived": False,
        "allergens": dict(REVIEWED),
    }
    data.update(overrides)
    item = Component.model_validate(data)
    return str(db["components"].insert_one(item.to_mongo()).inserted_id)


def insert_dish(db, **overrides) -> str:
    data = {
        "name": "Lamb ragu",
        "slug": "lamb-ragu",
        "category": "dinner",
        "price_cents": 2400,
        "unit": "portion",
        "is_available": True,
        "is_archived": False,
        "allergens": dict(REVIEWED),
        "serves": 2,
    }
    data.update(overrides)
    item = Dish.model_validate(data)
    return str(db["dishes"].insert_one(item.to_mongo()).inserted_id)


@pytest.fixture()
def request_context(app):
    """A request context, so the session the store writes to exists."""
    with app.test_request_context("/"):
        yield


# --- the store --------------------------------------------------------------


def test_a_session_with_no_cart_loads_as_an_empty_one(request_context):
    assert cart_service.load_cart().lines == []


def test_a_cart_survives_a_save_and_a_load(request_context):
    cart_service.save_cart(
        cart_service.add_line(cart_service.Cart(), ItemType.DISH, "d1", 3)
    )
    loaded = cart_service.load_cart()

    assert [line.item_id for line in loaded.lines] == ["d1"]
    assert loaded.lines[0].quantity == 3
    assert loaded.lines[0].item_type is ItemType.DISH


def test_an_emptied_cart_leaves_no_key_behind(request_context):
    from flask import session

    cart_service.save_cart(
        cart_service.add_line(cart_service.Cart(), ItemType.DISH, "d1")
    )
    cart_service.save_cart(cart_service.Cart())

    assert cart_service.SESSION_KEY not in session


def test_an_unreadable_session_value_degrades_to_an_empty_cart(request_context):
    """An old cookie meeting newer code is not a 500.

    The session is signed, so a malformed value is not something that got
    past the signature — and a customer should meet an empty cart rather
    than an error page.
    """
    from flask import session

    session[cart_service.SESSION_KEY] = [{"nonsense": True}]
    assert cart_service.load_cart().lines == []


def test_the_badge_count_reads_the_session_without_touching_the_database(
    request_context,
):
    cart_service.save_cart(
        cart_service.add_line(cart_service.Cart(), ItemType.DISH, "d1", 4)
    )
    assert cart_service.cart_item_count() == 4


# --- bounds -----------------------------------------------------------------


def test_a_line_quantity_is_capped_rather_than_rejected():
    cart = cart_service.add_line(cart_service.Cart(), ItemType.DISH, "d1", 500)
    assert cart.lines[0].quantity == cart_service.MAX_QUANTITY_PER_LINE

    topped_up = cart_service.add_line(cart, ItemType.DISH, "d1", 10)
    assert topped_up.lines[0].quantity == cart_service.MAX_QUANTITY_PER_LINE


def test_a_cart_refuses_more_lines_than_it_can_hold():
    """The bound keeps the session cookie finite. It is not a business rule."""
    cart = cart_service.Cart()
    for index in range(cart_service.MAX_LINES):
        cart = cart_service.add_line(cart, ItemType.DISH, f"d{index}")

    with pytest.raises(cart_service.CartFullError):
        cart_service.add_line(cart, ItemType.DISH, "one-too-many")

    # An item already in the cart is not a new line, so it still works.
    assert cart_service.add_line(cart, ItemType.DISH, "d0").lines[0].quantity == 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("component", ItemType.COMPONENT), ("dish", ItemType.DISH), ("", None),
     (None, None), ("meal", None)],
)
def test_an_item_type_is_recognised_or_refused_never_guessed(raw, expected):
    assert cart_service.parse_item_type(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("3", 3), (3, 3), ("0", 0), ("500", 99), (" 2 ", 2),
     ("-1", None), ("two", None), ("", None), (None, None)],
)
def test_a_quantity_is_parsed_clamped_or_refused(raw, expected):
    assert cart_service.parse_quantity(raw) == expected


# --- resolution -------------------------------------------------------------


def test_resolution_pairs_each_line_with_its_live_item(app, db):
    component_id = insert_component(db)
    dish_id = insert_dish(db)

    with app.test_request_context("/"):
        cart = cart_service.add_line(
            cart_service.Cart(), ItemType.COMPONENT, component_id, 2
        )
        cart = cart_service.add_line(cart, ItemType.DISH, dish_id)
        view = cart_service.resolve_cart(cart)

    assert [entry.name for entry in view.entries] == ["Harissa", "Lamb ragu"]
    assert [entry.line_total_cents for entry in view.entries] == [1700, 2400]
    assert view.subtotal_cents == 4100
    assert view.item_count == 3
    assert not view.is_blocked


def test_the_same_id_in_each_catalogue_resolves_to_its_own_item(app, db):
    """components and dishes are separate catalogues; ids do not collide."""
    component_id = insert_component(db, name="Green goddess", slug="green-goddess")
    dish_id = insert_dish(db)

    with app.test_request_context("/"):
        cart = cart_service.add_line(
            cart_service.Cart(), ItemType.COMPONENT, component_id
        )
        cart = cart_service.add_line(cart, ItemType.DISH, dish_id)
        view = cart_service.resolve_cart(cart)

    assert [entry.name for entry in view.entries] == ["Green goddess", "Lamb ragu"]


def test_a_withdrawn_item_keeps_its_name_loses_its_price_and_blocks_checkout(
    app, db
):
    """A cart never silently drops a line (04-WORKFLOWS.md)."""
    component_id = insert_component(db)
    priced_id = insert_dish(db)
    db["components"].update_one(
        {"slug": "harissa"}, {"$set": {"is_available": False}}
    )

    with app.test_request_context("/"):
        cart = cart_service.add_line(
            cart_service.Cart(), ItemType.COMPONENT, component_id, 3
        )
        cart = cart_service.add_line(cart, ItemType.DISH, priced_id)
        view = cart_service.resolve_cart(cart)

    withdrawn = view.entries[0]
    assert withdrawn.name == "Harissa"
    assert not withdrawn.is_available
    assert withdrawn.unit_price_cents == 0
    assert withdrawn.line_total_cents == 0
    # Only the orderable line reaches the subtotal.
    assert view.subtotal_cents == 2400
    assert view.is_blocked
    assert view.unavailable == [withdrawn]


def test_an_archived_item_is_withdrawn_too(app, db):
    component_id = insert_component(db)
    db["components"].update_one(
        {"slug": "harissa"}, {"$set": {"is_available": False, "is_archived": True}}
    )

    with app.test_request_context("/"):
        view = cart_service.resolve_cart(
            cart_service.add_line(
                cart_service.Cart(), ItemType.COMPONENT, component_id
            )
        )

    assert view.is_blocked
    assert view.entries[0].name == "Harissa"


def test_an_item_deleted_outright_still_renders_a_line(app, db):
    component_id = insert_component(db)
    db["components"].delete_one({"slug": "harissa"})

    with app.test_request_context("/"):
        view = cart_service.resolve_cart(
            cart_service.add_line(
                cart_service.Cart(), ItemType.COMPONENT, component_id
            )
        )

    assert len(view.entries) == 1
    assert view.entries[0].name == "Item no longer listed"
    assert view.is_blocked


def test_a_malformed_id_is_a_line_that_cannot_resolve_not_an_error(app, db):
    with app.test_request_context("/"):
        view = cart_service.resolve_cart(
            cart_service.add_line(cart_service.Cart(), ItemType.DISH, "not-an-id")
        )

    assert len(view.entries) == 1
    assert view.is_blocked


def test_only_a_published_item_can_be_added(app, db):
    published = insert_component(db)
    insert_component(db, name="Draft", slug="draft", is_available=False,
                     allergens={"contains": [], "may_contain": []})
    draft_id = str(db["components"].find_one({"slug": "draft"})["_id"])

    with app.app_context():
        assert cart_service.find_orderable_item(
            ItemType.COMPONENT, published
        ) is not None
        assert cart_service.find_orderable_item(ItemType.COMPONENT, draft_id) is None
        assert cart_service.find_orderable_item(ItemType.DISH, published) is None


def test_the_guest_cart_merge_is_a_named_seam_not_a_guess():
    """04-WORKFLOWS.md owes a merge on login. There is no login yet."""
    with pytest.raises(NotImplementedError):
        cart_service.merge_into_user_cart("u1", cart_service.Cart())
