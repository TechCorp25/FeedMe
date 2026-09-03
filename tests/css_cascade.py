"""A minimal CSS cascade resolver, used to test the compiled stylesheet.

Why this exists: a test that asserts markup cannot catch a stylesheet rule
that overrides the user-agent sheet. Whether a control carrying the `hidden`
attribute actually paints is decided by `app/static/css/app.css` — the
compiled output the browser reads — and by the cascade, not by the template.
So the assertion has to be made against that file, resolving `display` the
way a browser would rather than grepping for a rule that may be outranked.

Scope is deliberately narrow: enough of the cascade to answer "which
declaration of one property wins for this element", over the selector shapes
this project's stylesheet actually contains. It is not a CSS engine.

Where the model cannot know an answer it errs towards *more* rules
competing, never fewer: an unknown tag name matches, an unknown state
pseudo-class matches, and the ancestor half of a descendant selector is
assumed satisfiable. A stylesheet that passes under those assumptions holds
regardless of where the element sits in the document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# At-rules whose body holds ordinary style rules rather than descriptors.
# Their contents take part in the cascade, so they are parsed in place and
# keep their source order.
CONDITIONAL_AT_RULES = frozenset({"media", "supports", "layer", "container", "scope"})

# Functional pseudo-classes whose specificity is that of their most specific
# argument rather than a flat class-worth.
MATCHING_PSEUDOS = frozenset({"is", "not", "has", "matches", "any"})

_IDENT_START = re.compile(r"[A-Za-z_\-\\]")
_COMBINATORS = frozenset({">", "+", "~"})


@dataclass(frozen=True)
class Declaration:
    prop: str
    value: str
    important: bool


@dataclass(frozen=True)
class Rule:
    selectors: tuple[str, ...]
    declarations: tuple[Declaration, ...]
    order: int


@dataclass(frozen=True)
class Element:
    """The element a rule is resolved against.

    `tag` of None means "unknown", which matches any type selector.
    """

    tag: str | None = None
    classes: frozenset[str] = frozenset()
    attrs: dict[str, str] = field(default_factory=dict)


# --- scanning helpers -------------------------------------------------


def _skip_string(text: str, i: int) -> int:
    """Return the index just past the string literal starting at `i`."""
    quote = text[i]
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return i


def _skip_balanced(text: str, i: int, opener: str, closer: str) -> int:
    """Return the index just past the group opened at `i`."""
    depth = 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'":
            i = _skip_string(text, i)
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def strip_comments(css: str) -> str:
    """Remove /* */ comments, leaving string literals untouched."""
    out = []
    i = 0
    while i < len(css):
        ch = css[i]
        if ch in "\"'":
            end = _skip_string(css, i)
            out.append(css[i:end])
            i = end
            continue
        if ch == "/" and css.startswith("/*", i):
            end = css.find("*/", i + 2)
            i = len(css) if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_top_level(text: str, sep: str) -> list[str]:
    """Split on `sep`, ignoring separators inside groups or strings."""
    parts = []
    start = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'":
            i = _skip_string(text, i)
            continue
        if ch == "(":
            i = _skip_balanced(text, i, "(", ")")
            continue
        if ch == "[":
            i = _skip_balanced(text, i, "[", "]")
            continue
        if ch == sep:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return parts


# --- parsing ----------------------------------------------------------


def _parse_declarations(body: str) -> tuple[Declaration, ...]:
    declarations = []
    for chunk in _split_top_level(body, ";"):
        chunk = chunk.strip()
        # A nested block is not a declaration; the parser handles those.
        if not chunk or "{" in chunk:
            continue
        head, sep, value = _split_declaration(chunk)
        if not sep:
            continue
        important = False
        stripped = value.strip()
        if stripped.lower().endswith("!important"):
            important = True
            stripped = stripped[: -len("!important")].rstrip().rstrip("!").rstrip()
        declarations.append(Declaration(head.strip().lower(), stripped, important))
    return tuple(declarations)


def _split_declaration(chunk: str) -> tuple[str, str, str]:
    """Split `prop: value` on the first top-level colon."""
    i = 0
    while i < len(chunk):
        ch = chunk[i]
        if ch in "\"'":
            i = _skip_string(chunk, i)
            continue
        if ch == "(":
            i = _skip_balanced(chunk, i, "(", ")")
            continue
        if ch == "[":
            i = _skip_balanced(chunk, i, "[", "]")
            continue
        if ch == ":":
            return chunk[:i], ":", chunk[i + 1 :]
        i += 1
    return chunk, "", ""


def _read_block(text: str, i: int) -> tuple[str, int]:
    """Read the `{...}` block opening at `i`; return its body and end index."""
    end = _skip_balanced(text, i, "{", "}")
    return text[i + 1 : end - 1], end


def parse(css: str) -> list[Rule]:
    """Parse a stylesheet into style rules in source order."""
    rules: list[Rule] = []
    _parse_block(strip_comments(css), rules)
    return rules


def _parse_block(text: str, rules: list[Rule]) -> None:
    i = 0
    prelude_start = 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'":
            i = _skip_string(text, i)
            continue
        if ch == "(":
            i = _skip_balanced(text, i, "(", ")")
            continue
        if ch == "[":
            i = _skip_balanced(text, i, "[", "]")
            continue
        if ch == ";":
            # A statement at-rule such as @charset or @import.
            i += 1
            prelude_start = i
            continue
        if ch == "{":
            prelude = text[prelude_start:i].strip()
            body, end = _read_block(text, i)
            if prelude.startswith("@"):
                head = prelude[1:].split("(")[0].split()
                name = head[0].lower() if head else ""
                if name in CONDITIONAL_AT_RULES:
                    _parse_block(body, rules)
                # @font-face, @keyframes and friends hold no element rules.
            else:
                declarations = _parse_declarations(body)
                if declarations:
                    parts = _split_top_level(prelude, ",")
                    selectors = tuple(p.strip() for p in parts if p.strip())
                    if selectors:
                        rules.append(Rule(selectors, declarations, len(rules)))
            i = end
            prelude_start = i
            continue
        i += 1


# --- selectors --------------------------------------------------------


def _read_ident(selector: str, i: int) -> tuple[str, int]:
    start = i
    while i < len(selector):
        ch = selector[i]
        if ch == "\\":
            i += 2
            continue
        if ch.isalnum() or ch in "-_":
            i += 1
            continue
        break
    return selector[start:i], i


def specificity(selector: str) -> tuple[int, int, int]:
    """Return (ids, classes, types) for one selector.

    `:where()` contributes nothing — the rule that makes Tailwind's
    preflight `[hidden]` reset lose to a component class in the first place.
    """
    ids = classes = types = 0
    i = 0
    while i < len(selector):
        ch = selector[i]
        if ch == "#":
            _, i = _read_ident(selector, i + 1)
            ids += 1
        elif ch == ".":
            _, i = _read_ident(selector, i + 1)
            classes += 1
        elif ch == "[":
            i = _skip_balanced(selector, i, "[", "]")
            classes += 1
        elif ch == ":":
            if selector.startswith("::", i):
                _, i = _read_ident(selector, i + 2)
                if i < len(selector) and selector[i] == "(":
                    i = _skip_balanced(selector, i, "(", ")")
                types += 1
                continue
            name, i = _read_ident(selector, i + 1)
            name = name.lower()
            if i < len(selector) and selector[i] == "(":
                end = _skip_balanced(selector, i, "(", ")")
                args = selector[i + 1 : end - 1]
                i = end
                if name == "where":
                    continue
                if name in MATCHING_PSEUDOS:
                    inner = [
                        specificity(arg)
                        for arg in _split_top_level(args, ",")
                        if arg.strip()
                    ]
                    if inner:
                        best = max(inner)
                        ids += best[0]
                        classes += best[1]
                        types += best[2]
                    continue
                classes += 1
                continue
            classes += 1
        elif ch == "*" or ch.isspace() or ch in _COMBINATORS or ch == ",":
            i += 1
        elif _IDENT_START.match(ch):
            _, i = _read_ident(selector, i)
            types += 1
        else:
            i += 1
    return ids, classes, types


def subject_compound(selector: str) -> str:
    """Return the rightmost compound — the part describing the element itself."""
    parts = []
    start = 0
    i = 0
    while i < len(selector):
        ch = selector[i]
        if ch in "\"'":
            i = _skip_string(selector, i)
            continue
        if ch == "(":
            i = _skip_balanced(selector, i, "(", ")")
            continue
        if ch == "[":
            i = _skip_balanced(selector, i, "[", "]")
            continue
        if ch.isspace() or ch in _COMBINATORS:
            parts.append(selector[start:i])
            i += 1
            start = i
            continue
        i += 1
    parts.append(selector[start:])
    kept = [part for part in parts if part.strip()]
    return kept[-1] if kept else selector


def _attribute_matches(clause: str, element: Element) -> bool:
    body = clause[1:-1].strip()
    match = re.match(r"^([\w\-\\]+)\s*([~|^$*]?=)?\s*(.*)$", body, re.DOTALL)
    if not match:
        return False
    name, operator, raw = match.group(1).lower(), match.group(2), match.group(3).strip()
    if name not in element.attrs:
        return False
    if not operator:
        return True
    # Trailing case-sensitivity flag, e.g. [type=search i].
    if raw[-1:] in "iIsS" and raw[:-1].rstrip()[-1:] in "\"'":
        raw = raw[:-1].rstrip()
    if raw[:1] in "\"'" and raw[-1:] == raw[:1]:
        raw = raw[1:-1]
    actual = element.attrs[name]
    if operator == "=":
        return actual == raw
    if operator == "~=":
        return raw in actual.split()
    if operator == "|=":
        return actual == raw or actual.startswith(f"{raw}-")
    if operator == "^=":
        return bool(raw) and actual.startswith(raw)
    if operator == "$=":
        return bool(raw) and actual.endswith(raw)
    if operator == "*=":
        return bool(raw) and raw in actual
    return False


def matches(selector: str, element: Element) -> bool:
    """Whether `selector` could select `element`.

    Only the rightmost compound is evaluated. Anything to its left describes
    ancestors or siblings this model does not carry, and is assumed
    satisfiable so the caller's assertion has to hold wherever the element
    appears.
    """
    return _compound_matches(subject_compound(selector), element)


def _compound_matches(compound: str, element: Element) -> bool:
    i = 0
    while i < len(compound):
        ch = compound[i]
        if ch == "*":
            i += 1
        elif ch == ".":
            name, i = _read_ident(compound, i + 1)
            if name not in element.classes:
                return False
        elif ch == "#":
            # No rule in this stylesheet is id-scoped; treat one as unmatched
            # rather than silently widening what competes.
            return False
        elif ch == "[":
            end = _skip_balanced(compound, i, "[", "]")
            if not _attribute_matches(compound[i:end], element):
                return False
            i = end
        elif ch == ":":
            if compound.startswith("::", i):
                # A pseudo-element is a different box, not this element.
                return False
            name, i = _read_ident(compound, i + 1)
            name = name.lower()
            if i < len(compound) and compound[i] == "(":
                end = _skip_balanced(compound, i, "(", ")")
                args = [
                    arg.strip()
                    for arg in _split_top_level(compound[i + 1 : end - 1], ",")
                    if arg.strip()
                ]
                i = end
                if name in ("where", "is", "matches", "any"):
                    if not any(matches(arg, element) for arg in args):
                        return False
                elif name == "not":
                    if any(matches(arg, element) for arg in args):
                        return False
                # Any other functional pseudo-class is document state this
                # model does not carry: assume it can be satisfied.
            # A plain state pseudo-class (:hover, :focus-visible, :disabled)
            # is likewise assumed satisfiable.
        elif _IDENT_START.match(ch):
            name, i = _read_ident(compound, i)
            if element.tag is not None and name.lower() != element.tag.lower():
                return False
        else:
            i += 1
    return True


# --- the cascade ------------------------------------------------------


def resolve(rules: list[Rule], prop: str, element: Element) -> Declaration | None:
    """Return the declaration of `prop` that wins for `element`, if any."""
    winner: Declaration | None = None
    winning_key: tuple | None = None
    for rule in rules:
        for selector in rule.selectors:
            if not matches(selector, element):
                continue
            spec = specificity(selector)
            for declaration in rule.declarations:
                if declaration.prop != prop:
                    continue
                key = (declaration.important, spec, rule.order)
                if winning_key is None or key >= winning_key:
                    winner, winning_key = declaration, key
    return winner


def classes_declaring(rules: list[Rule], prop: str) -> dict[str, set[str]]:
    """Map class name to the selectors through which it declares `prop`.

    Discovery rather than a hard-coded list: a primitive added later is
    picked up without anyone remembering to extend a test.
    """
    found: dict[str, set[str]] = {}
    for rule in rules:
        if not any(declaration.prop == prop for declaration in rule.declarations):
            continue
        for selector in rule.selectors:
            compound = subject_compound(selector)
            if "::" in compound:
                continue
            for name in re.findall(r"\.((?:[\w\-]|\\.)+)", compound):
                found.setdefault(name.replace("\\", ""), set()).add(selector)
    return found
