"""Catalogue images: what is accepted, what is stored, what is rendered.

`app/storage/` has carried a `StorageBackend` interface and a filesystem
implementation since the scaffold and has never had a consumer.
`image_path` was typed in by hand as free text. `03-FRONTEND.md` says what
an image has to render as; nothing rendered one. This is that consumer.

**An `image_path` is a storage-interface path, not a URL** (01-DOMAIN.md),
and it stays one. Nothing here returns a filesystem path or a bucket
address, and no template builds either — a rendered `src` is always a
route on this application, which reads the object back through the
backend. That indirection is the whole point of the interface: the day
this moves to object storage, no catalogue document changes.

**What is accepted is decided by the bytes.** A content type is whatever
the client typed in a header and an extension is whatever the file was
called, so neither is consulted for the decision. The upload is decoded —
actually decoded, by Pillow — and it is accepted only if what comes back
is a JPEG, PNG or WebP raster of plausible dimensions. Anything that does
not decode is refused, whatever it claims to be.

SVG is refused *by construction* and deliberately: an SVG is a document
that can carry script and external references, and this application serves
uploaded bytes back to browsers. There is no safe way to serve a
chef-uploaded SVG from the same origin as a signed-in session, and no
reason to want one for a photograph of food.

**Derivatives are generated at upload, not at render.** `03-FRONTEND.md`
requires `srcset` + `sizes` and explicit `width`/`height`. That needs more
than one rendition and it needs the intrinsic size known server-side, so
each upload is resized once into a fixed ladder and the dimensions are
stored on the item. Resizing on request would put image processing in the
path of a page load, and storing no dimensions would mean the layout
shifts as images land — which is the thing the attributes exist to stop.

**An image never appears in an allergen surface.** Not in the allergen
tab, not in an order's consolidated declaration, not on the prep sheet —
which is printed in monochrome. Allergen surfaces are compliance surfaces:
an image carries no information that is not also in text, and a picture in
the middle of a declaration invites reading the picture instead.
"""

from __future__ import annotations

import io
import logging
import secrets
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from app.storage import get_storage

logger = logging.getLogger(__name__)

#: What an upload may weigh. Comfortably above a phone photograph that has
#: been through any export, and far below anything that would be a
#: photograph by accident.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

#: Pillow format names this accepts. Decided from the decode, never from
#: the filename or the `Content-Type`. SVG is not a raster format and
#: Pillow does not decode it, so it cannot appear here even by mistake.
ACCEPTED_FORMATS: dict[str, str] = {
    "JPEG": "jpg",
    "PNG": "png",
    "WEBP": "webp",
}

#: Refuse an image too small to be a photograph of anything, and one large
#: enough that decoding it is the attack. 40 megapixels is well past any
#: phone camera; `Image.MAX_IMAGE_PIXELS` would raise on its own at ~89,
#: which is a decompression bomb rather than a big photo.
MIN_EDGE = 200
MAX_PIXELS = 40_000_000

#: The rendition ladder. Four widths, because the catalogue grid is one
#: column at `xs` and four at `xl` (03-FRONTEND.md): the narrowest covers a
#: card on a small phone and the widest covers the item detail page on a
#: desktop at 2x. Every rendition is a JPEG — a catalogue photograph has no
#: transparency to preserve, and one output format means one decode path.
WIDTHS: tuple[int, ...] = (400, 800, 1200, 1600)

#: What the browser is told about layout, per surface. The card is the
#: catalogue grid's column; the detail image is the reading column.
CARD_SIZES = "(min-width: 1280px) 296px, (min-width: 1024px) 31vw, (min-width: 640px) 46vw, 92vw"
DETAIL_SIZES = "(min-width: 768px) 704px, 92vw"

#: JPEG quality. High enough that a plated dish does not band, low enough
#: that a card on a phone is not a megabyte.
QUALITY = 82

MAX_ALT = 300

#: The one prefix served by `public.item_image`. Declared here, beside the
#: code that writes under it, so the route and the writer cannot drift.
ITEM_IMAGE_PREFIX = "items"


class ImageError(ValueError):
    """A refusal the chef can fix, with the wording to show them."""


@dataclass(frozen=True)
class StoredImage:
    """What an upload produced: a path prefix and the intrinsic size.

    `path` is the widest rendition and the one an `src` falls back to.
    `width` and `height` are that rendition's, which is what makes the
    rendered `width`/`height` attributes an aspect ratio the browser can
    reserve space from before a byte of image arrives.
    """

    path: str
    width: int
    height: int


def read_upload(storage_file) -> bytes | None:
    """The bytes of an uploaded file, or None when nothing was chosen.

    Bounded before anything else looks at it. A file field left empty
    arrives as an empty filename, which is not an error — most catalogue
    saves do not change the image.
    """
    if storage_file is None or not (storage_file.filename or "").strip():
        return None

    # One read, bounded. `MAX_UPLOAD_BYTES + 1` is enough to know it is
    # over without holding the whole of an oversized body.
    head = storage_file.read(MAX_UPLOAD_BYTES + 1)
    if len(head) > MAX_UPLOAD_BYTES:
        raise ImageError(
            f"That image is larger than "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)}MB. Export it smaller and "
            "try again."
        )
    if not head:
        raise ImageError("That file is empty.")
    return head


def inspect(raw: bytes) -> Image.Image:
    """Decode the upload and refuse it if it is not an image we serve.

    Decided entirely from the bytes. Neither the `Content-Type` the client
    sent nor the name the file happened to have is consulted — both are
    the uploader's to choose, and a `.jpg` that is really an HTML document
    served back from this origin is the whole reason not to trust them.
    """
    try:
        image = Image.open(io.BytesIO(raw))
        image.verify()
        # `verify()` consumes the file object, so the image is reopened to
        # be used. This is Pillow's documented dance, not a workaround.
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise ImageError(
            "That file is not an image this application can read. Upload a "
            "JPEG, PNG or WebP photograph."
        ) from exc

    if image.format not in ACCEPTED_FORMATS:
        raise ImageError(
            f"That is a {image.format or 'unrecognised'} image. Upload a "
            "JPEG, PNG or WebP."
        )
    width, height = image.size
    if width * height > MAX_PIXELS:
        raise ImageError("That image has too many pixels. Export it smaller.")
    if min(width, height) < MIN_EDGE:
        raise ImageError(
            f"That image is {width}x{height}. The shortest side needs to be "
            f"at least {MIN_EDGE} pixels."
        )
    return image


def parse_alt(raw: str | None, *, required: bool) -> str | None:
    """The chef's description of the picture.

    Required whenever an image is stored, and it is required rather than
    defaulted **on purpose**. Filling it with the item's name would
    announce the name twice to a screen reader on the detail page, where
    the `<h1>` has just said it, and would describe nothing. An `alt` that
    is worth having is one a person wrote about this photograph.
    """
    text = (raw or "").strip()
    if len(text) > MAX_ALT:
        raise ImageError(f"The image description is longer than {MAX_ALT} characters.")
    if not text and required:
        raise ImageError(
            "Describe the photograph for somebody who cannot see it — what "
            "is in the picture, not the item's name."
        )
    return text or None


def store(raw: bytes, *, slug: str) -> StoredImage:
    """Resize into the ladder, write every rendition, return the widest.

    Renditions share one path stem so a single stored `image_path` names
    the whole set: `items/<slug>-<token>` yields `…-400.jpg` through
    `…-1600.jpg`. The random token is what makes a re-upload a new set of
    objects rather than an overwrite — a cached `…-800.jpg` at the old URL
    would otherwise keep serving the picture the chef just replaced.

    An image narrower than a rung is never enlarged. Upscaling invents
    detail, and a `srcset` candidate that is the same pixels at a bigger
    number is a larger download for no more image.
    """
    image = inspect(raw)
    if image.mode not in ("RGB", "L"):
        # Flatten to RGB: the ladder is JPEG, which has no alpha channel,
        # and a PNG with transparency would otherwise composite onto black.
        background = Image.new("RGB", image.size, (255, 255, 255))
        source = image.convert("RGBA")
        background.paste(source, mask=source.split()[-1])
        image = background
    elif image.mode == "L":
        image = image.convert("RGB")

    stem = f"{ITEM_IMAGE_PREFIX}/{slug}-{secrets.token_hex(4)}"
    storage = get_storage()
    widest: StoredImage | None = None

    for width in _ladder(image.width):
        rendition = _resized(image, width)
        buffer = io.BytesIO()
        rendition.save(buffer, format="JPEG", quality=QUALITY, optimize=True)
        buffer.seek(0)
        path = f"{stem}-{rendition.width}.jpg"
        storage.save(path, buffer)
        widest = StoredImage(
            path=path, width=rendition.width, height=rendition.height
        )

    assert widest is not None
    logger.info(
        "stored a catalogue image",
        extra={"stem": stem, "renditions": len(candidate_widths(widest))},
    )
    return widest


def _ladder(source_width: int) -> list[int]:
    """The widths to write for an image this wide, narrowest first.

    Every rung below the original, and then the original itself — capped
    at the widest rung, because a 6000px photograph is not worth storing
    at 6000px to serve into a 704px column.

    The top of the ladder is always the width that ends up stored as
    `image_width`, which is what makes `srcset_for` able to derive the
    candidate list from that one number without storing a second one.
    Deliberately *not* a loop that stops at the first rung past the
    original: that drops the original's own width, so a 1400px upload
    would be served at 1200px and claim to be 1200px wide.
    """
    top = min(source_width, WIDTHS[-1])
    return sorted({width for width in WIDTHS if width < top} | {top})


def _resized(image: Image.Image, width: int) -> Image.Image:
    if width >= image.width:
        return image.copy()
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.LANCZOS)


def candidate_widths(stored: StoredImage | None) -> list[int]:
    """The rungs that exist for a stored image, narrowest first."""
    if stored is None:
        return []
    return _ladder(stored.width)


def path_for_width(image_path: str, width: int) -> str:
    """One rendition's path, derived from the widest one's.

    The stem is shared, so this is a substitution rather than a second
    thing to store. Still a storage-interface path — the route that serves
    it is what turns it into something a browser can fetch.
    """
    stem, _, _ = image_path.rpartition("-")
    return f"{stem}-{width}.jpg"


def stored_width(image_path: str) -> int | None:
    """The width a rendition path names, or None if it names none."""
    tail = image_path.rpartition("-")[2].removesuffix(".jpg")
    return int(tail) if tail.isdigit() else None


def delete(image_path: str | None) -> None:
    """Remove every rendition of a stored image. Missing ones are fine.

    The ladder's top rung is the *original's* width, which is usually not
    one of `WIDTHS` — a 1400px upload is stored at 400, 800, 1200 and
    1400. Deleting only the fixed rungs therefore left that top rendition
    behind, still on disk and still fetchable at its URL after the chef
    had replaced or removed the picture. So the width is read back out of
    the path and the whole ladder is removed, plus every fixed rung, which
    costs nothing: `StorageBackend.delete` treats a missing object as
    success by contract.
    """
    if not image_path:
        return
    storage = get_storage()
    stem, _, _ = image_path.rpartition("-")
    width = stored_width(image_path)
    rungs = set(WIDTHS) | (set(_ladder(width)) if width else set())
    for rung in sorted(rungs):
        storage.delete(f"{stem}-{rung}.jpg")


# --- rendering --------------------------------------------------------------
#
# The template asks for a URL and a `srcset` and gets strings. It never
# slices a path, never joins one, and never learns where the bytes live —
# which is what keeps `image_path` a storage-interface path rather than a
# URL that has leaked into a document (01-DOMAIN.md).


def image_url(image_path: str) -> str:
    """The route that serves one rendition."""
    from flask import url_for

    return url_for("public.item_image", stored_path=image_path)


def srcset_for(item) -> str:
    """Every rendition of an item's image, as a `srcset` value.

    Built from the widest rendition's width: the narrower rungs exist only
    where they were actually written, so an image uploaded at 900px
    advertises 400w and 900w and never an 1200w that would 404.
    """
    if not item.has_image:
        return ""
    return ", ".join(
        f"{image_url(path_for_width(item.image_path, width))} {width}w"
        for width in _ladder(item.image_width)
    )
