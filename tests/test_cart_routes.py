"""The cart's HTML surface: forms that post and redirect, no JavaScript.

Every mutation is a real form. `cart.js` intercepts the add control and
posts the same intent to the JSON API instead, but nothing here depends
on it (03-FRONTEND.md).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.catalogue import Component, Dish

REVIEWED = {
    "contains": [],
    "may_contain": [],
    "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef",
}


@pytest.fixture()
def harissa(db) -> str:
    item = Component.model_validate(
        {
            "name": "Harissa",
            "slug": "harissa",
            "summary": "Smoky roasted chilli paste.",
            "category": "sauce",
            "price_cents": 850,
            "unit": "250ml",
            "is_available": True,
            "is_archived": False,
            "allergens": dict(REVIEWED),
        }
    )
    return str(db["components"].insert_one(item.to_mongo()).inserted_id)


@pytest.fixture()
def ragu(db) -> str:
    item = Dish.model_validate(
        {
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
    )
    return str(db["dishes"].insert_one(item.to_mongo()).inserted_id)


@pytest.fixture()
def draft(db) -> str:
    item = Component.model_validate(
        {
            "name": "Unpublished draft",
            "slug": "unpublished-draft",
            "category": "sauce",
            "price_cents": 500,
            "is_available": False,
            "is_archived": False,
            "allergens": {"contains": [], "may_contain": []},
        }
    )
    return str(db["components"].insert_one(item.to_mongo()).inserted_id)


def add(client, item_type: str, item_id: str, **extra):
    form = {"item_type": item_type, "item_id": item_id}
    form.update(extra)
    return client.post("/cart/add", data=form)


# --- the add control --------------------------------------------------------


def test_the_item_page_carries_a_real_form_that_posts(client, harissa):
    html = client.get("/components/harissa").get_data(as_text=True)

    assert '<form\n    class="cart-add"\n    method="post"' in html
    assert 'action="/cart/add"' in html
    assert 'name="csrf_token"' in html
    assert 'value="{}"'.format(harissa) in html


def test_the_add_control_sits_on_the_page_that_carries_the_declaration(
    client, harissa
):
    """A card has no declaration; the item page has both price and tabs."""
    page = client.get("/components/harissa").get_data(as_text=True)
    assert page.index('id="add-to-cart"') < page.index('id="allergens"')

    browse = client.get("/components").get_data(as_text=True)
    assert 'action="/cart/add"' not in browse


def test_adding_an_item_puts_it_in_the_cart_and_returns_to_the_item(
    client, harissa
):
    response = add(
        client, "component", harissa, quantity="2",
        return_to="/components/harissa#add-to-cart",
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/components/harissa#add-to-cart"

    cart = client.get("/cart").get_data(as_text=True)
    assert "Harissa" in cart
    assert "$17.00" in cart


def test_adding_the_same_item_twice_increments_one_line(client, harissa):
    add(client, "component", harissa, quantity="2")
    add(client, "component", harissa, quantity="3")

    html = client.get("/cart").get_data(as_text=True)
    assert html.count('class="cart-line ') == 1
    assert 'value="5"' in html


def test_the_two_catalogues_add_as_two_lines(client, harissa, ragu):
    add(client, "component", harissa)
    add(client, "dish", ragu)

    html = client.get("/cart").get_data(as_text=True)
    assert "Harissa" in html
    assert "Lamb ragu" in html
    assert html.count('class="cart-line ') == 2


def test_an_unpublished_item_cannot_be_added_and_says_so(client, draft):
    response = add(client, "component", draft, return_to="/components")
    assert response.status_code == 302

    html = client.get("/cart").get_data(as_text=True)
    assert "Unpublished draft" not in html
    assert "Your cart is empty." in html


def test_a_mutation_that_cannot_be_applied_never_passes_silently(client, harissa):
    """A quiet no-op leaves the customer believing they added something."""
    add(client, "not-a-catalogue", harissa)
    add(client, "component", "")
    add(client, "component", harissa, quantity="nonsense")

    html = client.get("/cart").get_data(as_text=True)
    assert "Your cart is empty." in html
    assert "That could not be added to your cart." in html


def test_the_confirmation_is_announced_not_merely_shown(client, harissa):
    add(client, "component", harissa, return_to="/components/harissa")
    html = client.get("/components/harissa").get_data(as_text=True)

    assert 'aria-live="polite"' in html
    assert "Harissa added to your cart." in html


# --- return_to --------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "https://example.test/phish",
        "//example.test/phish",
        "http://example.test",
        "/\\example.test",
        "javascript:alert(1)",
    ],
)
def test_a_return_target_off_this_site_is_discarded(client, harissa, target):
    """The field decides where the customer lands, so it is untrusted."""
    response = add(client, "component", harissa, return_to=target)

    assert response.status_code == 302
    assert response.headers["Location"] == "/cart"


def test_a_same_site_return_target_keeps_its_fragment(client, harissa):
    """A fragment never reaches the server, so the form carries it."""
    response = add(
        client, "component", harissa, return_to="/components/harissa#add-to-cart"
    )
    assert response.headers["Location"] == "/components/harissa#add-to-cart"


# --- the cart page ----------------------------------------------------------


def test_an_empty_cart_says_so_and_offers_a_way_out(client):
    html = client.get("/cart").get_data(as_text=True)

    assert "Your cart is empty." in html
    assert 'href="/dishes"' in html
    assert 'href="/components"' in html


def test_the_cart_shows_prices_and_a_subtotal_in_integer_minor_units(
    client, harissa, ragu
):
    add(client, "component", harissa, quantity="2")
    add(client, "dish", ragu)

    html = client.get("/cart").get_data(as_text=True)
    assert "$8.50" in html
    assert "$17.00" in html
    assert "$24.00" in html
    assert "$41.00" in html


def test_a_quantity_can_be_changed_from_the_cart(client, harissa):
    add(client, "component", harissa)
    client.post(
        "/cart/update",
        data={"item_type": "component", "item_id": harissa, "quantity": "4"},
    )

    html = client.get("/cart").get_data(as_text=True)
    assert "$34.00" in html


def test_a_quantity_of_zero_removes_the_line(client, harissa):
    add(client, "component", harissa)
    client.post(
        "/cart/update",
        data={"item_type": "component", "item_id": harissa, "quantity": "0"},
    )

    assert "Your cart is empty." in client.get("/cart").get_data(as_text=True)


def test_a_line_can_be_removed(client, harissa, ragu):
    add(client, "component", harissa)
    add(client, "dish", ragu)
    client.post(
        "/cart/remove", data={"item_type": "component", "item_id": harissa}
    )

    # Scoped to the lines: the queued "added to your cart" confirmations
    # are flushed onto this page too, and they name what was added.
    lines = client.get("/cart").get_data(as_text=True)
    lines = lines.split('class="cart-lines"')[1].split("</ul>")[0]
    assert "Harissa" not in lines
    assert "Lamb ragu" in lines


def test_the_cart_renders_without_javascript(client, harissa):
    add(client, "component", harissa)
    html = client.get("/cart").get_data(as_text=True)

    assert html.count("<h1") == 1
    assert 'action="/cart/update"' in html
    assert 'action="/cart/remove"' in html
    assert 'method="post"' in html


# --- a withdrawn line -------------------------------------------------------


def test_a_withdrawn_item_is_struck_through_and_never_dropped(client, db, harissa):
    add(client, "component", harissa, quantity="2")
    db["components"].update_one(
        {"slug": "harissa"}, {"$set": {"is_available": False}}
    )

    html = client.get("/cart").get_data(as_text=True)
    assert "<s>Harissa</s>" in html
    assert "No longer available." in html
    assert "cart-line--unavailable" in html


def test_a_withdrawn_line_blocks_checkout_until_it_is_removed(
    client, db, harissa, ragu
):
    add(client, "component", harissa)
    add(client, "dish", ragu)
    db["components"].update_one(
        {"slug": "harissa"}, {"$set": {"is_available": False}}
    )

    blocked = client.get("/cart").get_data(as_text=True)
    assert "no longer available. Remove" in blocked
    assert "Remove the unavailable item" in blocked
    # It is not priced, so the subtotal is the ragu alone.
    assert "$24.00" in blocked

    client.post(
        "/cart/remove", data={"item_type": "component", "item_id": harissa}
    )
    cleared = client.get("/cart").get_data(as_text=True)
    assert "no longer available. Remove" not in cleared


# --- the badge --------------------------------------------------------------


def test_the_badge_is_rendered_by_the_server_on_every_page(client, harissa):
    add(client, "component", harissa, quantity="3")

    for url in ("/", "/components", "/dishes", "/components/harissa"):
        html = client.get(url).get_data(as_text=True)
        assert "data-cart-count" in html
        assert ">3<" in html.split("data-cart-count")[1][:40]


def test_the_badge_reads_zero_before_anything_is_added(client):
    html = client.get("/").get_data(as_text=True)
    assert "0 items" in html
