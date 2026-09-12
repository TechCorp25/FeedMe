# 03-FRONTEND.md — Frontend Conventions

## Principles

1. **HTML first.** The server sends a complete page. JavaScript adds convenience, never content.
2. **No framework.** Vanilla ES2020+, ES modules, no bundler, no transpiler.
3. **Tailwind compiled, not CDN.** `npx tailwindcss -i static/css/tailwind.css -o static/css/app.css --minify`. Output is committed so deployment needs no Node runtime.
4. **Progressive enhancement.** Every JS interaction has a working non-JS fallback (form POST + redirect).

## Visual language — "Butcher's Label"

The delivered design system. Every object in the interface — a dish, a
component, an account — is presented as something specifically made and
labelled, not picked off a shelf: a condensed stamped label over a calm serif
description, on a kraft-toned ground.

**Colour.** Twenty-one tokens, each defined for both themes in `tailwind.css`
as RGB channels so Tailwind's alpha modifiers keep working. This is the only
place a raw colour appears.

Six neutrals carry the kraft ground the system is built on. Above them sit
four *label hues*, each with exactly one job, so a colour is never decoration
and never has to be learnt twice; and three *signal hues* that only ever
report an outcome. Every hue has a `-soft` companion — the same colour at a
background weight — so a coloured surface is a token rather than an opacity
modifier invented at the call site.

| Token | Light | Dark | Used as |
|---|---|---|---|
| `ground` | `#F5EFE3` | `#14110D` | page background |
| `surface` | `#FFFBF2` | `#1E1913` | card, panel and header background |
| `sunken` | `#EAE1CE` | `#272019` | recessed panel: filter strip, table head, allergen snapshot |
| `ink` | `#1F1913` | `#F1E8D7` | primary text, component boundaries |
| `ink-muted` | `#554734` | `#B7A88F` | secondary text, `may_contain` |
| `line` | `#D4C8B0` | `#3C3128` | hairline dividers — decorative only |
| `accent` / `-soft` | `#1C6A4A` / `#DCEDE2` | `#74C49C` / `#14301F` | what you can do: links, primary buttons, active tab, focus ring |
| `accent-ink` | `#FFFFFF` | `#0A1A12` | button label on `accent` |
| `saffron` / `-soft` | `#8A5300` / `#FAEBCC` | `#EBB552` / `#33260E` | what it costs: every price, the cart badge, an order total |
| `berry` / `-soft` | `#7A2B50` / `#F4E4EC` | `#EE9DBE` / `#331624` | the way in: meal-type menus, taste chips, the dish catalogue's rule |
| `stamp` / `-soft` | `#8C2F1E` / `#FAE3DD` | `#F19070` / `#331A12` | the `contains` allergen declaration, and nothing else |
| `success` / `-soft` | `#1B6B3A` / `#DEF0E3` | `#7FD4A1` / `#10291A` | it worked: a confirmation, a fulfilled order |
| `danger` / `-soft` | `#A3201A` / `#FBE2E0` | `#FF9A8D` / `#341613` | it failed, or it undoes something: errors, cancellation |
| `info` / `-soft` | `#1A4F8B` / `#E0EBF8` | `#93BFF2` / `#11223A` | not yet known: a pending declaration, a placed order |

Two rules keep the set honest:

- **`stamp` is the allergen declaration and nothing else.** It used to double
  as the error colour, which meant a mistyped password was marked in the same
  ink as "contains peanut". Errors now take `danger`, so the stamped red on a
  page always means the same thing.
- **A colour never carries a distinction on its own.** Every status stamp
  names its status, every flash reads as a sentence, and the two allergen
  chips differ by border style and wording as well as hue — so the interface
  survives a monochrome print and a colour-vision deficiency. Where a hue
  does identify something — the rule along the top of a catalogue card — the
  page heading and the card's own link say the same thing first.

Every text pair clears 4.5:1 in both themes against `ground`, `surface`,
`sunken` and its own `-soft`; the narrowest is `saffron` on `sunken` in light
at 4.87:1. `line` is 1.45:1 and therefore never carries text or meaning — a
boundary that identifies a component uses `ink` at 2px, not `line`.
`test_design_system.py` checks the whole product of foregrounds against
backgrounds rather than a hand-picked list, so retuning one token is verified
everywhere it can land.

**Type.** Two self-hosted families, both SIL OFL 1.1, in `app/static/fonts/`
with their licences beside them. Google publishes both as variable fonts, so
one file per family covers every weight rather than one file per instance.

- **Big Shoulders Display** (`font-display`) — names, headings, labels,
  buttons, field labels. Uppercase and tracked, applied as a CSS transform so
  the accessible name and the text in the markup stay as authored.
- **Source Serif 4** (`font-serif`) — every run of reading text.

Scale is a perfect fourth (1.333) from a 16px base; headings `1.05`, body `1.6`.

**Shape.** Radius is near-zero throughout (2px default, 1px small) because a
stamped label is boxy. The one exception is the taste-preference chip, which is
a pill so it can never be mistaken for the square allergen stamp beside it.

**Two deliberate deviations from the delivered system**, both in service of
rules this document already sets:

1. *Allergen chips keep the serif face and their authored casing.* The system
   sets every stamped label in condensed uppercase. An allergen name here
   carries qualifiers — "Cereals containing gluten (wheat, rye)" — and
   condensed capitals slow that down exactly where reading accuracy matters
   most. The chips still take the stamped box: squared off, 2px border,
   `stamp` for `contains` and a dashed `ink-muted` for `may_contain`, so the
   two are distinguished by border style and wording as well as by colour.
2. *The tab strip stays four anchors, and no panel is hidden in the served
   HTML.* The system's item detail ships `role="tab"` buttons with panels
   pre-hidden server-side. That breaks Principle 1 above: `tabs.js` is what
   turns the strip into a tablist and hides panels, and only once it has run.

## Tailwind conventions

- Utilities in markup. No `@apply` except for genuinely repeated primitives (`.btn`, `.chip`, `.tab`) defined in `tailwind.css`.
- **Any class name a template composes at render time must be in the config's
  `safelist`.** Tailwind's extractor only sees whole class names in scanned
  files, so `flash--{{ category }}`, `status-stamp--{{ order.status.value }}`
  and `card--{{ kind }}` produce nothing for it to keep. `.flash--error` was
  dropped from the build exactly this way, and shipped for several releases
  styled as an ordinary flash.
- Design tokens in `tailwind.config.js` — colours, type scale, spacing, radius. Values live in
  `tailwind.css` as CSS custom properties. No arbitrary hex values inline.
- Fonts are self-hosted from `app/static/fonts/`. No CDN at runtime, no network at build time.
- Jinja macros for repeated structures: item card, allergen chip, tab strip, quantity stepper, price display.
- Dark mode: `class` strategy, toggle persisted in `localStorage`, honours `prefers-color-scheme` on first visit.

## Responsive and orientation

Viewport and orientation are treated as first-class inputs, not an afterthought.

**Breakpoints** (Tailwind defaults, plus a small-phone tier):

| Token | Min width | Target |
|---|---|---|
| `xs` | 360px | small phone |
| `sm` | 640px | large phone |
| `md` | 768px | tablet portrait |
| `lg` | 1024px | tablet landscape / small laptop |
| `xl` | 1280px | desktop |

**Mandatory viewport tag:**

```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
```

**Rules:**

- Fluid type via `clamp()` for headings; fixed Tailwind scale for body text.
- Catalogue grid: 1 column `xs`, 2 at `sm`, 3 at `lg`, 4 at `xl`.
- Touch targets minimum 44×44px. Primary actions reachable in the lower third on phones.
- Safe-area insets respected (`env(safe-area-inset-*)`) for notched devices.
- Images: `srcset` + `sizes`, `loading="lazy"` below the fold, explicit `width`/`height` to prevent layout shift.
- Honour `prefers-reduced-motion` — disable transitions, never disable function.

**Landscape handling.** Short-viewport landscape (phone rotated) is a real target, not an edge case:

```css
@media (orientation: landscape) and (max-height: 480px) { ... }
```

In that mode: collapse the header to a single compact bar, move the item tab strip to a horizontal scroller, never lock scrolling, and never trap the user in a full-height modal. Test at 640×360 and 844×390.

**Orientation change** must not lose state. Cart contents, active tab and scroll anchor persist across rotation. If JS observes `orientationchange`, it re-measures — it does not re-render from scratch.

## The tabbed item view

Every component and every dish detail page renders the same four tabs, in this order:

1. **Ingredients**
2. **Allergens**
3. **Storage & Shelf Life**
4. **Preparation**

**All four tab panels are present in the served HTML.** No lazy fetch, no JS-gated content. JavaScript toggles visibility only.

Behaviour:

- Without JS: panels render stacked with visible headings, anchored by `#ingredients`, `#allergens`, `#storage`, `#preparation`.
- With JS: tab strip becomes an ARIA tablist. `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls`, `role="tabpanel"`, arrow-key navigation, Home/End support.
- Active tab reflected in the URL hash so a tab is linkable and survives reload and rotation.
- On `xs`–`sm`, the tab strip scrolls horizontally with momentum; it does not wrap into two rows and does not become a `<select>`.

**Allergen tab presentation:**

- `contains` entries render as high-contrast chips, always first.
- `may_contain` renders in a visually distinct, clearly labelled block below.
- Gluten cereals and tree nut species are named inline within their chip.
- Preference flags (chilli, garlic, coriander) and `spice_level` **never** appear in this tab. They belong on the summary header and the ingredients tab.
- If the item is unreviewed, the tab shows a neutral "allergen information pending" state — never "no allergens".
- Never abbreviate an allergen name. Never use an icon without its text label.

## JavaScript

- ES modules, one concern per file: `tabs.js`, `cart.js`, `filters.js`, `orderStatus.js`, `theme.js`.
- No global namespace pollution; no inline `onclick`.
- Progressive: each module checks its target element exists and exits quietly if not.
- `fetch()` with explicit error handling. A failed cart request restores previous UI state and surfaces a visible message — never silent failure.
- Order status polling: 15s interval, backs off to 60s after 5 minutes, stops entirely when the tab is hidden (`visibilitychange`) and on terminal order states.
- CSRF token read from a meta tag and sent on every mutating request.

## Accessibility floor

WCAG 2.1 AA. Non-negotiable given the compliance content:

- Contrast ≥ 4.5:1 for body text, ≥ 3:1 for large text and UI boundaries.
- Every interactive element keyboard reachable with a visible focus ring.
- Forms use real `<label>` elements. Errors announced via `aria-live="polite"`.
- Skip-to-content link. Logical heading hierarchy, one `<h1>` per page.
- Allergen information must be readable by a screen reader in the same order it appears visually.
