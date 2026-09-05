"""The design system's invariants, read off the compiled stylesheet.

`app/static/css/app.css` is the artifact the browser reads, so it is the
artifact these tests read. Three of the four guards below catch a class of
mistake that is invisible in a template and invisible in a diff:

- a colour token defined for one theme only, which silently keeps its light
  value in the dark, and which the delivered system itself shipped;
- a text pair that drops under the contrast floor when a token is retuned;
- a font reference that resolves to nothing, or to somebody else's CDN.

The fourth reads the source stylesheet rather than the compiled one, because
`border-style` survives compilation only as part of a shorthand.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.css_cascade import strip_comments

ROOT = Path(__file__).resolve().parents[1]
COMPILED = ROOT / "app/static/css/app.css"
SOURCE = ROOT / "app/static/css/tailwind.css"
FONT_DIR = ROOT / "app/static/fonts"

#: Every pair that carries text, as (foreground token, background token).
#: 03-FRONTEND.md fixes the floor at WCAG AA for body text.
TEXT_PAIRS = [
    ("ink", "ground"),
    ("ink", "surface"),
    ("ink-muted", "ground"),
    ("ink-muted", "surface"),
    ("accent", "ground"),
    ("accent", "surface"),
    ("stamp", "ground"),
    ("stamp", "surface"),
    ("accent-ink", "accent"),
]

CONTRAST_FLOOR = 4.5


def _token_block(css: str, selector: str) -> dict[str, tuple[int, int, int]]:
    """The `--color-*` custom properties declared in one selector's block."""
    match = re.search(
        re.escape(selector) + r"\s*\{(.*?)\}", strip_comments(css), re.S
    )
    assert match is not None, f"{selector} declares no block"
    return {
        name: tuple(int(part) for part in value.split())
        for name, value in re.findall(
            r"--color-([a-z-]+)\s*:\s*(\d+\s+\d+\s+\d+)\s*(?:;|$)", match.group(1)
        )
    }


@pytest.fixture(scope="module")
def compiled() -> str:
    return COMPILED.read_text()


@pytest.fixture(scope="module")
def themes(compiled: str) -> dict[str, dict[str, tuple[int, int, int]]]:
    return {
        "light": _token_block(compiled, ":root"),
        "dark": _token_block(compiled, ".dark"),
    }


def _relative_luminance(channels: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        srgb = value / 255
        return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(foreground: tuple[int, int, int], background: tuple[int, int, int]) -> float:
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_every_colour_token_is_defined_for_both_themes(themes):
    """A token defined only in `:root` keeps its light value in the dark.

    Nothing about that failure is visible in a template or a diff: the dark
    page simply renders one colour from the light palette. The delivered
    design system shipped exactly this bug on two of its own tokens.
    """
    assert themes["light"], "no colour tokens found in :root"
    assert themes["light"].keys() == themes["dark"].keys()


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(("foreground", "background"), TEXT_PAIRS)
def test_text_pairs_clear_the_contrast_floor(themes, theme, foreground, background):
    palette = themes[theme]
    for token in (foreground, background):
        assert token in palette, f"{token} is not defined for the {theme} theme"

    ratio = contrast(palette[foreground], palette[background])
    assert ratio >= CONTRAST_FLOOR, (
        f"{foreground} on {background} is {ratio:.2f}:1 in the {theme} theme, "
        f"below the {CONTRAST_FLOOR}:1 floor"
    )


def test_the_line_token_is_never_asked_to_carry_text(themes):
    """`line` is a hairline divider and is deliberately below the floor.

    It is excluded from the pairs above by design, so this states the reason
    rather than leaving its absence to look like an oversight. A boundary that
    identifies a component uses `ink`, not `line`.
    """
    for theme, palette in themes.items():
        ratio = contrast(palette["line"], palette["ground"])
        assert ratio < CONTRAST_FLOOR, (
            f"`line` now clears {ratio:.2f}:1 in the {theme} theme. If it is "
            "meant to carry text, add it to TEXT_PAIRS instead of relying on "
            "this test to keep passing."
        )


def test_fonts_are_self_hosted_and_every_reference_resolves(compiled):
    """No CDN at runtime, and no @font-face pointing at a file that is absent.

    A missing file fails silently: the browser falls back and the page merely
    looks wrong, which no template assertion would catch.
    """
    references = re.findall(r"url\(([^)]+)\)", compiled)
    assert references, "the compiled sheet declares no font files"

    for reference in references:
        target = reference.strip("'\"")
        assert not target.startswith(("http:", "https:", "//")), (
            f"{target} is fetched over the network; fonts are self-hosted"
        )
        resolved = (COMPILED.parent / target).resolve()
        assert resolved.is_file(), f"{target} resolves to nothing"

    # The licence travels with the font it licenses (SIL OFL 1.1).
    assert list(FONT_DIR.glob("*.woff2"))
    for family in ("BigShouldersDisplay", "SourceSerif4"):
        assert (FONT_DIR / f"{family}-OFL.txt").is_file()


def test_allergen_chips_differ_by_more_than_colour():
    """Colour alone never carries a compliance distinction (01-DOMAIN.md).

    `contains` and `may_contain` are already separated by their headings and
    their wording. The chips add a border style to that, so the distinction
    survives a monochrome print and a colour-vision deficiency.
    """
    source = strip_comments(SOURCE.read_text())
    blocks = re.findall(r"([^{}]+)\{([^{}]*)\}", source)

    def declarations(selector: str) -> str:
        """Everything that lands on a class, grouped selectors included."""
        matched = [
            body
            for selectors, body in blocks
            if selector in [part.strip() for part in selectors.split(",")]
        ]
        assert matched, f"{selector} is not defined"
        return "\n".join(matched)

    assert "border-dashed" in declarations(".chip--may")
    assert "border-dashed" not in declarations(".chip--contains")
