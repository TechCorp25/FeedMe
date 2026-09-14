"""Catalogue images: upload, validation, storage and rendering.

`app/storage/` has carried a `StorageBackend` and a filesystem
implementation since the scaffold with no consumer at all; `image_path`
was a free-text field on the chef's form; `03-FRONTEND.md` said what an
image had to render as and nothing rendered one.

`test_an_uploaded_photograph_reaches_the_catalogue_card` is the test that
fails before the fix: it uploads a real JPEG through the chef's form as a
multipart POST and then reads the card as a signed-out visitor.
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone

import pytest
from PIL import Image

from app.models.catalogue import Component
from app.models.users import Role, User
from app.security.passwords import hash_password
from app.services import catalogue_images
from app.storage import LocalStorage, set_storage

PASSWORD = "a-long-enough-passphrase"
REVIEWED = {
    "contains": ["milk"],
    "reviewed_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    "reviewed_by": "chef@example.com",
}
ALT = "A wide bowl of dark red harissa with a spoon resting in it."


@pytest.fixture()
def storage_root(app, tmp_path):
    """Uploads land in a temp directory, never in the working tree."""
    root = tmp_path / "uploads"
    set_storage(app, LocalStorage(root))
    return root


@pytest.fixture()
def chef(db):
    db["users"].insert_one(
        User(
            email="chef@example.com",
            password_hash=hash_password(PASSWORD),
            display_name="Chef",
            role=Role.CHEF_ADMIN,
        ).to_mongo()
    )


@pytest.fixture()
def signed_in(client, chef, storage_root):
    client.post("/login", data={"email": "chef@example.com", "password": PASSWORD})
    return client


def photograph(width=1400, height=1000, fmt="JPEG", mode="RGB") -> bytes:
    """A real image, encoded by a real encoder."""
    image = Image.new(mode, (width, height), (140, 40, 30))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def upload(name="photo.jpg", data=None, content_type="image/jpeg"):
    return (io.BytesIO(data if data is not None else photograph()), name, content_type)


def _component(db, *, name="Harissa rosa", slug="harissa-rosa", available=False) -> str:
    item = Component.model_validate(
        {
            "name": name, "slug": slug, "category": "sauce", "price_cents": 850,
            "unit": "250ml", "is_available": available,
            "allergens": dict(REVIEWED),
        }
    )
    return str(db["components"].insert_one(item.to_mongo()).inserted_id)


def _form(**overrides) -> dict:
    base = {
        "name": "Harissa rosa",
        "slug": "harissa-rosa",
        "summary": "Rose harissa, slow cooked.",
        "description": "",
        "category": "sauce",
        "price": "14.50",
        "unit": "250ml",
        "spice_level": "3",
    }
    base.update(overrides)
    return base


def _stored(db, item_id: str) -> dict:
    from bson import ObjectId

    return db["components"].find_one({"_id": ObjectId(item_id)})


# --- the gap this closes ----------------------------------------------------


def test_an_uploaded_photograph_reaches_the_catalogue_card(client, db, signed_in):
    """End to end: a multipart upload, then the card a visitor reads."""
    item_id = _component(db)
    saved = signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(), image_alt=ALT),
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert saved.status_code == 200

    stored = _stored(db, item_id)
    assert stored["image_path"].startswith("items/harissa-rosa-")
    assert stored["image_alt"] == ALT
    assert stored["image_width"] == 1400
    assert stored["image_height"] == 1000

    signed_in.post(f"/chef/components/{item_id}/availability", data={"available": "1"})
    signed_in.post("/logout")

    page = client.get("/components").get_data(as_text=True)
    assert "card__image" in page
    assert "srcset=" in page and "sizes=" in page


def test_the_served_image_is_the_bytes_that_were_uploaded(client, db, signed_in):
    item_id = _component(db)
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(), image_alt=ALT),
        content_type="multipart/form-data",
    )
    path = _stored(db, item_id)["image_path"]

    response = client.get(f"/media/{path}")
    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"
    # It decodes, and it is the size the item claims.
    assert Image.open(io.BytesIO(response.data)).size == (1400, 1000)


# --- what is rendered -------------------------------------------------------


def _with_image(db, signed_in, **overrides) -> str:
    item_id = _component(db, **overrides)
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(), image_alt=ALT, **{
            key: value for key, value in overrides.items() if key == "slug"
        }),
        content_type="multipart/form-data",
    )
    signed_in.post(f"/chef/components/{item_id}/availability", data={"available": "1"})
    return item_id


def test_the_card_image_carries_every_attribute_the_spec_requires(
    client, db, signed_in
):
    _with_image(db, signed_in)
    signed_in.post("/logout")
    page = client.get("/components").get_data(as_text=True)
    tag = re.search(r"<img[^>]*card__image[^>]*>", page, re.S)
    assert tag, "no card image rendered"
    markup = tag.group(0)
    for attribute in ("srcset=", "sizes=", 'width="1400"', 'height="1000"'):
        assert attribute in markup, attribute


def test_the_srcset_offers_every_rendition_that_was_written(client, db, signed_in):
    """And never one that was not — a 404 candidate is worse than none."""
    _with_image(db, signed_in)
    signed_in.post("/logout")
    page = client.get("/components").get_data(as_text=True)
    tag = re.search(r"<img[^>]*card__image[^>]*>", page, re.S).group(0)
    widths = sorted(int(width) for width in re.findall(r" (\d+)w", tag))
    assert widths == [400, 800, 1200, 1400]

    # Every candidate actually resolves.
    for url in re.findall(r"(/media/[^\s,\"]+)", tag):
        assert client.get(url).status_code == 200


def test_a_narrow_image_advertises_no_rung_wider_than_itself(db, signed_in, client):
    """Upscaling invents detail; a wider candidate is bytes for no picture."""
    item_id = _component(db)
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(data=photograph(600, 400)), image_alt=ALT),
        content_type="multipart/form-data",
    )
    signed_in.post(f"/chef/components/{item_id}/availability", data={"available": "1"})
    signed_in.post("/logout")

    tag = re.search(
        r"<img[^>]*card__image[^>]*>", client.get("/components").get_data(as_text=True), re.S
    ).group(0)
    assert sorted(int(w) for w in re.findall(r" (\d+)w", tag)) == [400, 600]


def test_the_first_card_loads_eagerly_and_the_rest_lazily(client, db, signed_in):
    for index, slug in enumerate(("aaa-first", "zzz-second")):
        item_id = _component(db, name=f"Item {index}", slug=slug)
        signed_in.post(
            f"/chef/components/{item_id}/save",
            data=_form(name=f"Item {index}", slug=slug, image=upload(), image_alt=ALT),
            content_type="multipart/form-data",
        )
        signed_in.post(
            f"/chef/components/{item_id}/availability", data={"available": "1"}
        )
    signed_in.post("/logout")

    page = client.get("/components").get_data(as_text=True)
    loadings = re.findall(r'card__image"[^>]*?loading="(\w+)"', page, re.S)
    assert loadings == ["eager", "lazy"]


def test_the_card_image_is_decorative_and_the_detail_image_is_not(
    client, db, signed_in
):
    """The card's heading says the name; the item page's alt describes it."""
    _with_image(db, signed_in)
    signed_in.post("/logout")

    card = re.search(
        r"<img[^>]*card__image[^>]*>",
        client.get("/components").get_data(as_text=True),
        re.S,
    ).group(0)
    assert 'alt=""' in card

    detail = client.get("/components/harissa-rosa").get_data(as_text=True)
    tag = re.search(r"<img[^>]*item-header__image[^>]*>", detail, re.S).group(0)
    assert ALT in tag
    assert 'loading="eager"' in tag


def test_an_item_with_no_image_renders_no_image_and_no_placeholder(
    client, db, signed_in
):
    """A decision, not an omission: the layout depends on no placeholder."""
    item_id = _component(db)
    signed_in.post(f"/chef/components/{item_id}/availability", data={"available": "1"})
    signed_in.post("/logout")

    page = client.get("/components").get_data(as_text=True)
    assert "card__image" not in page
    assert "<img" not in page
    # The card is still there, and still complete.
    assert "Harissa rosa" in page
    assert "card__price" in page


def test_no_template_builds_a_path_or_a_url_of_its_own():
    """`image_path` is a storage path, and a template never makes it a URL."""
    import pathlib

    for template in pathlib.Path("app/templates").rglob("*.html"):
        body = template.read_text()
        assert "STORAGE_LOCAL_PATH" not in body, template
        # The only way to an image URL is the helper the app registers.
        # Rendered expressions only — a comment may say "image_path".
        for match in re.findall(r"\{\{[^}]*image_path[^}]*\}\}", body, re.S):
            assert "image_url(" in match, f"{template}: {match}"


# --- never on an allergen surface -------------------------------------------


def test_the_allergen_tab_carries_no_image(client, db, signed_in):
    _with_image(db, signed_in)
    signed_in.post("/logout")
    page = client.get("/components/harissa-rosa").get_data(as_text=True)
    panel = page[page.index('id="allergens"'):]
    panel = panel[: panel.index('id="storage"')]
    assert "<img" not in panel


def test_the_prep_sheet_carries_no_image(client, db, signed_in):
    """Printed in monochrome, and a compliance surface besides."""
    _with_image(db, signed_in)
    page = signed_in.get("/chef/prep/2026-09-14").get_data(as_text=True)
    assert "<img" not in page


def test_the_stylesheet_hides_catalogue_images_in_print():
    css = open("app/static/css/tailwind.css").read()
    printed = css[css.index("@media print"):]
    assert ".card__image" in printed
    assert ".item-header__image" in printed


# --- validation: the bytes decide -------------------------------------------


@pytest.mark.parametrize(
    "name, content_type, body",
    [
        # An HTML document called a JPEG and announced as one.
        ("photo.jpg", "image/jpeg", b"<html><script>alert(1)</script></html>"),
        # An SVG, which is a scriptable document and never served from here.
        ("logo.svg", "image/svg+xml", b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
        # An SVG wearing a JPEG's name and content type.
        ("logo.jpg", "image/jpeg", b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
        ("notes.txt", "text/plain", b"just some text"),
        # A real file, truncated: the header says JPEG and the body lies.
        ("broken.jpg", "image/jpeg", photograph()[:200]),
    ],
)
def test_a_file_that_is_not_an_image_is_refused(
    db, signed_in, name, content_type, body
):
    item_id = _component(db)
    response = signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(name, body, content_type), image_alt=ALT),
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert _stored(db, item_id)["image_path"] is None


def test_a_correct_content_type_does_not_rescue_a_non_image(db, signed_in):
    """And a wrong one does not condemn a real image."""
    item_id = _component(db)
    response = signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(
            image=upload("photo.txt", photograph(), "text/plain"), image_alt=ALT
        ),
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    assert _stored(db, item_id)["image_path"] is not None


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_the_three_accepted_formats_are_accepted(db, signed_in, fmt):
    item_id = _component(db)
    response = signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(data=photograph(fmt=fmt)), image_alt=ALT),
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    # Every rendition is a JPEG whatever went in: one output, one path.
    assert _stored(db, item_id)["image_path"].endswith(".jpg")


def test_an_image_too_small_to_be_a_photograph_is_refused(db, signed_in):
    item_id = _component(db)
    response = signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(data=photograph(120, 90)), image_alt=ALT),
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "shortest side" in response.get_data(as_text=True)


def test_an_oversized_upload_is_refused_in_a_sentence_rather_than_a_413(
    db, signed_in
):
    """A 413 is a page the chef cannot act on."""
    item_id = _component(db)
    too_big = b"\xff\xd8\xff" + b"\x00" * (catalogue_images.MAX_UPLOAD_BYTES + 10)
    response = signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(data=too_big), image_alt=ALT),
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "larger than 8MB" in body
    assert "Export it smaller" in body


def test_the_hard_ceiling_leaves_room_for_that_sentence():
    """`MAX_CONTENT_LENGTH` must sit above the image limit, or 413 wins."""
    from app.config import TestingConfig

    assert TestingConfig().MAX_CONTENT_LENGTH > catalogue_images.MAX_UPLOAD_BYTES


def test_a_body_past_the_hard_ceiling_is_still_a_page(signed_in, app):
    """The backstop is rendered, not Werkzeug's bare 413."""
    response = signed_in.post(
        "/chef/components/save",
        data={
            "blob": (
                io.BytesIO(b"x" * (app.config["MAX_CONTENT_LENGTH"] + 64)), "b.bin"
            )
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 413
    assert "too large" in response.get_data(as_text=True)


# --- the description --------------------------------------------------------


def test_an_image_without_a_description_is_refused(db, signed_in):
    item_id = _component(db)
    response = signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(), image_alt="  "),
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "Describe the photograph" in response.get_data(as_text=True)
    assert _stored(db, item_id)["image_path"] is None


def test_the_description_is_never_defaulted_to_the_name(db, signed_in):
    """An alt repeating the heading announces the name twice."""
    with pytest.raises(catalogue_images.ImageError):
        catalogue_images.parse_alt("", required=True)
    assert catalogue_images.parse_alt("", required=False) is None


def test_the_description_can_be_edited_without_re_uploading(db, signed_in):
    item_id = _component(db)
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(), image_alt=ALT),
        content_type="multipart/form-data",
    )
    before = _stored(db, item_id)["image_path"]

    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image_alt="A corrected description of the same bowl."),
        content_type="multipart/form-data",
    )
    after = _stored(db, item_id)
    assert after["image_path"] == before
    assert after["image_alt"] == "A corrected description of the same bowl."


# --- image_path is never typed ----------------------------------------------


def test_the_form_no_longer_offers_a_path_field(signed_in):
    page = signed_in.get("/chef/components/new").get_data(as_text=True)
    assert 'name="image_path"' not in page
    assert 'name="image"' in page
    assert 'enctype="multipart/form-data"' in page


def test_a_posted_image_path_is_ignored(db, signed_in):
    """It is a storage path, and the application decides what it is."""
    item_id = _component(db)
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image_path="https://example.com/not-a-path.jpg"),
        content_type="multipart/form-data",
    )
    assert _stored(db, item_id)["image_path"] is None


# --- replacing and removing -------------------------------------------------


def test_replacing_an_image_writes_a_new_stem_and_deletes_the_old(
    db, signed_in, storage_root
):
    """A cache at the old URL must never serve the picture just replaced."""
    item_id = _component(db)
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(), image_alt=ALT),
        content_type="multipart/form-data",
    )
    first = _stored(db, item_id)["image_path"]

    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(data=photograph(900, 600)), image_alt=ALT),
        content_type="multipart/form-data",
    )
    second = _stored(db, item_id)["image_path"]

    assert second != first
    # Every rendition of the old set, not just the widest. The top rung is
    # the original's own width and is usually not one of the fixed ones,
    # so deleting only those left it on disk and still fetchable.
    old_stem = first.rpartition("-")[0]
    assert not list((storage_root / "items").glob(f"{old_stem.split('/')[-1]}-*.jpg"))
    assert (storage_root / second).exists()


def test_removing_an_image_clears_all_four_fields_together(db, signed_in):
    item_id = _component(db)
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(), image_alt=ALT),
        content_type="multipart/form-data",
    )
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image_remove="1"),
        content_type="multipart/form-data",
    )
    stored = _stored(db, item_id)
    assert stored["image_path"] is None
    assert stored["image_alt"] is None
    assert stored["image_width"] is None
    assert stored["image_height"] is None


def test_removing_an_image_deletes_every_rendition(db, signed_in, storage_root):
    """Not just the fixed rungs — the top one is the original's width."""
    item_id = _component(db)
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(), image_alt=ALT),
        content_type="multipart/form-data",
    )
    assert list((storage_root / "items").glob("*.jpg"))

    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image_remove="1"),
        content_type="multipart/form-data",
    )
    assert list((storage_root / "items").glob("*.jpg")) == []


def test_a_replaced_rendition_is_no_longer_served(client, db, signed_in):
    """The URL a cache might still hold has to stop resolving."""
    item_id = _component(db)
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(), image_alt=ALT),
        content_type="multipart/form-data",
    )
    first = _stored(db, item_id)["image_path"]
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(data=photograph(900, 600)), image_alt=ALT),
        content_type="multipart/form-data",
    )
    assert client.get(f"/media/{first}").status_code == 404


def test_a_half_stored_image_renders_as_no_image(db):
    """All four fields or none — never a picture with no description."""
    item = Component.model_validate(
        {
            "name": "Half", "slug": "half", "category": "sauce",
            "price_cents": 100, "unit": "each",
            "image_path": "items/half-abcd-800.jpg", "image_width": 800,
            "allergens": dict(REVIEWED),
        }
    )
    assert item.has_image is False


# --- the media route --------------------------------------------------------


def test_the_media_route_is_public(client, db, signed_in):
    """The catalogue is public; an image behind a login is a broken card."""
    item_id = _component(db)
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(), image_alt=ALT),
        content_type="multipart/form-data",
    )
    path = _stored(db, item_id)["image_path"]
    signed_in.post("/logout")
    assert client.get(f"/media/{path}").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "items/../../etc/passwd",
        "../etc/passwd",
        "etc/passwd",
        "items/never-written-800.jpg",
        "uploads/items/x-800.jpg",
    ],
)
def test_a_path_that_names_nothing_is_a_404(client, storage_root, path):
    assert client.get(f"/media/{path}").status_code == 404


def test_renditions_are_served_immutable(client, db, signed_in):
    item_id = _component(db)
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(), image_alt=ALT),
        content_type="multipart/form-data",
    )
    path = _stored(db, item_id)["image_path"]
    cache = client.get(f"/media/{path}").headers["Cache-Control"]
    assert "immutable" in cache and "max-age=31536000" in cache


# --- the backend is the only way to the bytes -------------------------------


def test_the_upload_goes_through_the_storage_backend(app, db, signed_in, tmp_path):
    """Not to a path a blueprint built for itself."""
    from app.storage.base import StorageBackend

    written: list[str] = []

    class Recording(StorageBackend):
        def __init__(self, inner):
            self.inner = inner

        def save(self, path, stream):
            written.append(path)
            return self.inner.save(path, stream)

        def open(self, path):
            return self.inner.open(path)

        def delete(self, path):
            self.inner.delete(path)

        def exists(self, path):
            return self.inner.exists(path)

    set_storage(app, Recording(LocalStorage(tmp_path / "recorded")))
    item_id = _component(db)
    signed_in.post(
        f"/chef/components/{item_id}/save",
        data=_form(image=upload(), image_alt=ALT),
        content_type="multipart/form-data",
    )
    assert written, "nothing reached the storage backend"
    assert all(path.startswith("items/") for path in written)


def test_an_unknown_backend_fails_loudly(app):
    """Rather than falling back to a disk that a deploy will wipe."""
    from app.storage import UnknownBackend, get_storage

    app.extensions.pop("feedme_storage", None)
    app.config["STORAGE_BACKEND"] = "s4"
    with pytest.raises(UnknownBackend):
        get_storage(app)
