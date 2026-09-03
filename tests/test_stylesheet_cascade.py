"""The compiled stylesheet must let the `hidden` attribute win.

`app/static/css/app.css` is the artifact the browser reads, so it is the
artifact these tests read. A template test can assert that the theme toggle
is served with `hidden` on it and still miss the reason it paints anyway:
Tailwind's preflight resets it with `[hidden]:where(:not([hidden=until-found]))`,
whose `:where()` contributes no specificity, so any component-layer `display`
from `@apply` — `.btn`, `.chip`, `.tab` all set one — ties on specificity and
wins on order.

The guard is generalised rather than written per class. Every class in the
compiled sheet that declares `display` is discovered and checked, so a
primitive added later is covered without anyone remembering to extend this
file.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests.css_cascade import (
    Declaration,
    Element,
    classes_declaring,
    matches,
    parse,
    resolve,
    specificity,
    subject_compound,
)

STYLESHEET = Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css"

# Primitives 03-FRONTEND.md names as the sanctioned uses of @apply. Asserting
# they are found keeps the generalised test from passing over an empty set if
# the stylesheet is ever missing, empty, or parsed wrongly.
EXPECTED_PRIMITIVES = {"btn", "chip", "tab"}

# Elements this application gives a `hidden` attribute from JavaScript, so
# they never appear hidden in served markup and the test below cannot see
# them. Each entry is the full class list the element actually carries.
JS_HIDDEN_TARGETS = [
    (("btn", "btn--ghost"), "the theme toggle, hidden again by theme.js"),
    (("tab-panel",), "an inactive item panel, toggled by tabs.js"),
]


@pytest.fixture(scope="module")
def rules():
    return parse(STYLESHEET.read_text(encoding="utf-8"))


class HiddenElementCollector(HTMLParser):
    """Collects (tag, classes) for every element served with `hidden`."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str, frozenset[str]]] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "hidden" not in attributes:
            return
        self.found.append((tag, frozenset((attributes.get("class") or "").split())))


def hidden_element(classes: set[str], tag: str | None = None) -> Element:
    return Element(tag=tag, classes=frozenset(classes), attrs={"hidden": ""})


# --- the guard --------------------------------------------------------


def test_every_class_that_sets_display_still_loses_to_hidden(rules):
    """The generalisation: no component primitive may out-rank `hidden`."""
    declaring = classes_declaring(rules, "display")
    assert EXPECTED_PRIMITIVES <= set(declaring), (
        "the compiled stylesheet does not declare display on the primitives "
        "03-FRONTEND.md defines; app.css is stale, empty, or was not parsed"
    )

    for name in sorted(declaring):
        winner = resolve(rules, "display", hidden_element({name}))
        assert winner is not None, f".{name} carrying `hidden` resolves no display at all"
        assert winner.value == "none", (
            f"an element with class .{name} and the `hidden` attribute paints "
            f"as display:{winner.value}. Its display comes from "
            f"{sorted(declaring[name])} and out-ranks the `hidden` reset. "
            "Restore the `[hidden] { display: none !important }` rule in the "
            "base layer of tailwind.css and recompile."
        )


@pytest.mark.parametrize(("css_classes", "why"), JS_HIDDEN_TARGETS)
def test_elements_this_app_hides_with_javascript_are_not_painted(rules, css_classes, why):
    winner = resolve(rules, "display", hidden_element(set(css_classes)))
    assert winner is not None and winner.value == "none", (
        f"{' '.join('.' + name for name in css_classes)} — {why}"
    )


def test_markup_served_with_hidden_is_not_painted(client, rules):
    """The concrete case: a JS-less visitor is never shown a dead control."""
    collector = HiddenElementCollector()
    collector.feed(client.get("/").get_data(as_text=True))

    assert collector.found, (
        "no element is served with `hidden`; this test has lost its subject"
    )
    for tag, classes in collector.found:
        winner = resolve(rules, "display", hidden_element(classes, tag=tag))
        assert winner is not None and winner.value == "none", (
            f"<{tag} class=\"{' '.join(sorted(classes))}\" hidden> is served hidden "
            f"but resolves to display:{winner.value if winner else None}"
        )


def test_the_override_is_the_only_thing_holding_this_up(rules):
    """The winning declaration is `!important`, not an accident of ordering.

    Specificity alone cannot carry it: `[hidden]` and `.btn` both score
    (0, 1, 0), and a descendant selector such as `.filters__option label`
    scores higher than either.
    """
    winner = resolve(rules, "display", hidden_element({"btn"}))
    assert winner == Declaration("display", "none", important=True)


# --- proof the guard has teeth ----------------------------------------


def test_the_resolver_reproduces_the_collision_it_guards_against():
    """Without the override the resolver reports the bug, not a pass.

    A test that cannot fail on the broken input is not a test. This pins the
    pre-fix stylesheet shape and shows the assertion above flipping.
    """
    preflight = "[hidden]:where(:not([hidden=until-found])){display:none}"
    component = ".btn{display:inline-flex;min-height:44px}"
    toggle = Element(tag="button", classes=frozenset({"btn"}), attrs={"hidden": ""})

    without_override = resolve(parse(preflight + component), "display", toggle)
    assert without_override is not None and without_override.value == "inline-flex"

    override = "[hidden]{display:none!important}"
    with_override = resolve(parse(preflight + component + override), "display", toggle)
    assert with_override is not None and with_override.value == "none"


def test_the_override_survives_a_higher_specificity_component():
    """`!important` is required: source order and specificity both lose."""
    css = (
        "[hidden]{display:none!important}"
        ".filters__option label{display:flex}"
        ".chip.chip--contains{display:inline-flex}"
    )
    element = hidden_element({"chip", "chip--contains"}, tag="label")
    winner = resolve(parse(css), "display", element)
    assert winner is not None and winner.value == "none"


# --- the resolver itself ----------------------------------------------


def test_specificity_ignores_where_and_takes_the_worst_case_of_not():
    assert specificity("[hidden]") == (0, 1, 0)
    assert specificity(".btn") == (0, 1, 0)
    assert specificity("[hidden]:where(:not([hidden=until-found]))") == (0, 1, 0)
    assert specificity(".filters__option label") == (0, 1, 1)
    assert specificity("button") == (0, 0, 1)
    assert specificity(":not(.a, .b.c)") == (0, 2, 0)
    assert specificity(".tab[aria-selected='true']") == (0, 2, 0)
    assert specificity(".ingredients__note::before") == (0, 1, 1)


def test_parsing_keeps_source_order_across_media_blocks():
    css = ".a{display:block}@media (min-width:640px){.b{display:flex}}.c{display:grid}"
    assert [rule.selectors for rule in parse(css)] == [(".a",), (".b",), (".c",)]
    assert [rule.order for rule in parse(css)] == [0, 1, 2]


def test_parsing_reads_important_and_leaves_values_intact():
    rules = parse('.a{display:none !important;content:" · ";color:rgb(var(--x)/1)}')
    declarations = {declaration.prop: declaration for declaration in rules[0].declarations}
    assert declarations["display"] == Declaration("display", "none", important=True)
    assert declarations["content"].value == '" · "'
    assert declarations["color"].value == "rgb(var(--x)/1)"


def test_parsing_skips_comments_and_descriptor_at_rules():
    css = "/*! banner */@font-face{font-family:X;src:url(x.woff2)}.a{display:block}"
    rules = parse(css)
    assert [rule.selectors for rule in rules] == [(".a",)]


def test_matching_is_conservative_about_what_it_cannot_know():
    element = hidden_element({"btn"})
    # An unknown tag, an unknown state, and an unknown ancestor all match.
    assert matches("button", element)
    assert matches(".btn:focus-visible", element)
    assert matches(".filters__option label", element)
    # What the model does know, it enforces.
    assert not matches(".chip", element)
    assert not matches("[hidden=until-found]", element)
    assert not matches(".btn::before", element)
    assert not matches("button.btn", Element(tag="a", classes=frozenset({"btn"})))


def test_the_subject_of_a_selector_is_its_rightmost_compound():
    assert subject_compound(".filters__option label") == "label"
    assert subject_compound(".tab-strip > .tab") == ".tab"
    assert subject_compound("[hidden]:where(:not([a=b]))") == "[hidden]:where(:not([a=b]))"
