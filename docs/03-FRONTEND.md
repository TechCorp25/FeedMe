# 03-FRONTEND.md — Frontend Conventions

## Principles

1. **HTML first.** The server sends a complete page. JavaScript adds convenience, never content.
2. **No framework.** Vanilla ES2020+, ES modules, no bundler, no transpiler.
3. **Tailwind compiled, not CDN.** `npx tailwindcss -i static/css/tailwind.css -o static/css/app.css --minify`. Output is committed so deployment needs no Node runtime.
4. **Progressive enhancement.** Every JS interaction has a working non-JS fallback (form POST + redirect).

## Tailwind conventions

- Utilities in markup. No `@apply` except for genuinely repeated primitives (`.btn`, `.chip`, `.tab`) defined in `tailwind.css`.
- Design tokens in `tailwind.config.js` — colours, spacing scale, font stack. No arbitrary hex values inline.
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
