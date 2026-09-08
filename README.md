# FeedMe

A private meal-preparation service application: a chef-authored catalogue
of components and complete dishes, full allergen declarations on every
item, customer ordering, and a chef order queue.

The specification lives in [`docs/`](docs/) and is authoritative:

| Doc | Covers |
|---|---|
| [`00-SYSTEM.md`](docs/00-SYSTEM.md) | Role, locked stack, non-negotiables, quality gates |
| [`01-DOMAIN.md`](docs/01-DOMAIN.md) | Collections, item shape, allergen model, orders, ledger |
| [`02-ARCHITECTURE.md`](docs/02-ARCHITECTURE.md) | Flask layout, layering, repository contract, auth, config |
| [`03-FRONTEND.md`](docs/03-FRONTEND.md) | Tailwind, responsive and orientation rules, tabbed item view |
| [`04-WORKFLOWS.md`](docs/04-WORKFLOWS.md) | Ordering flows, order state machine, chef-admin flows |
| [`05-DEPLOYMENT.md`](docs/05-DEPLOYMENT.md) | Platform detection, required variables, Codespaces / Railway / Render |

## Stack

Python 3.11+ · Flask (app factory + blueprints) · Jinja2 · MongoDB via
PyMongo · Pydantic v2 · Tailwind CSS compiled with the Tailwind CLI ·
vanilla ES modules, no bundler.

## Local setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env      # then fill in SECRET_KEY and JWT_SECRET
```

A MongoDB instance must be reachable at `MONGO_URI` — a local server, or
a MongoDB Atlas cluster over `mongodb+srv://`. The application applies its
index bootstrap at startup and refuses to boot if the database is
unreachable.

```bash
flask --app wsgi run
```

## Deployment

The same code runs on a workstation, in a GitHub Codespace, on Railway
and on Render. The platform is **detected**, not configured: the external
origin, the port to bind, whether TLS is terminated upstream and which
`FLASK_ENV` to assume are all read from the variables the host itself
sets. Anything detected is overridden by stating that variable
explicitly.

| Host | Recognised by | Manifest |
|---|---|---|
| local | nothing else matched | — |
| GitHub Codespaces | `CODESPACES`, `CODESPACE_NAME` | `.devcontainer/devcontainer.json` |
| Railway | `RAILWAY_ENVIRONMENT` | `railway.json`, `Procfile` |
| Render | `RENDER`, `RENDER_SERVICE_ID` | `render.yaml` |

`SECRET_KEY`, `JWT_SECRET`, `MONGO_URI` and `MONGO_DB_NAME` are the values
no platform can derive; production refuses to start without them. Set
them in the platform's own variable store — never in this repository.

```bash
python scripts/env_report.py
```

Prints the detected platform, the resolved configuration and whether each
secret is real or still a placeholder. It prints no secret values and
touches no network. `GET /health` reports the same detection alongside a
live database round-trip, and both Railway and Render gate a deploy on it.

Per-platform setup, the Atlas network-allowlist requirement and the
reasoning behind the forwarded-header handling are in
[`docs/05-DEPLOYMENT.md`](docs/05-DEPLOYMENT.md).

## Design system

The interface follows the **"Butcher's Label"** system: a condensed stamped
label over a calm serif description, on a kraft-toned ground. Tokens, type
scale, the two deliberate deviations and the reasoning are recorded in
[`docs/03-FRONTEND.md`](docs/03-FRONTEND.md#visual-language--butchers-label).

Both type families are self-hosted from `app/static/fonts/` under the SIL Open
Font License 1.1, with each licence beside the font it covers. There is no CDN
at runtime and no network access needed at build time.

`tests/test_design_system.py` guards the parts that fail silently: a colour
token defined for one theme only, a text pair dropping under 4.5:1, a font
reference that resolves to nothing, and the allergen chips being told apart by
colour alone.

## Stylesheet

`app/static/css/app.css` is compiled output and is committed, so
deployment needs no Node runtime. Rebuild it after any template or
`tailwind.css` change:

```bash
npm install
npm run css        # or: npm run css:watch
```

CI fails if the committed stylesheet is out of date with its source.

`tests/test_stylesheet_cascade.py` reads the compiled file and resolves the
cascade over it. It exists because a template test cannot catch a stylesheet
rule that out-ranks the user-agent sheet: Tailwind's preflight resets the
`hidden` attribute through `:where()`, which contributes no specificity, so
any `@apply` that sets `display` on a component primitive — `.btn`, `.chip`
and `.tab` all do — would otherwise leave a `hidden` control painted on the
page. Every class in the compiled sheet that declares `display` is discovered
and checked, so a primitive added later is covered automatically.

## Tests

```bash
python -m pytest
```

Tests run against `mongomock`, never a live database. Coverage centres on
the areas the specification names: allergen validators, tenancy
isolation, order state transitions, and integer-cent price arithmetic.
`tests/test_checkout.py` adds the two the checkout slice owes: a placed
order that a later catalogue edit cannot reach, and one customer asking for
another's order and getting 404 rather than 403.

```bash
python scripts/check_boot.py
```

Boots the application and prints every route with its auth marker. It
exits non-zero if any endpoint lacks one — the same check that runs
inside `create_app`.

`tests/test_deployment.py` covers platform detection: each host is
recognised from the variables it really sets, an explicit value always
beats a detected one, and `X-Forwarded-*` is honoured on a platform proxy
and ignored everywhere else.

`tests/test_account.py` covers the account area: another customer's order
answering 404 on every surface that takes a reference, a cancellation that
credits exactly what it charged, the same cancellation submitted twice
writing one credit, a profile form that cannot reach `role` or
`password_hash`, saved filters narrowing a browse page and saying so, and a
`filtered=1` URL being taken literally.

## Continuous integration

`.github/workflows/ci.yml` runs three jobs on every pull request:

| Check name | Fails when |
|---|---|
| `pytest` | any test fails |
| `route-marker check` | an endpoint lacks an explicit auth marker |
| `deployment manifests` | a platform manifest is malformed, or detection resolves wrongly |
| `compiled stylesheet is current` | `app.css` is out of date with `tailwind.css` |

These are advisory until they are named as required status checks in the
branch protection rule for `main`. Until then a red build does not stop a
merge, and the checks report rather than enforce.

## What is built so far

The application skeleton, the Pydantic schema of record, the repository
layer with its tenancy contract, the order state machine, the public
catalogue and its three ordering entry points, the cart they feed, and the
accounts and checkout that turn a cart into an order:

| Route | |
|---|---|
| `GET /` | landing page, server-rendered |
| `GET /health` | liveness, a database round-trip, and the detected platform |
| `GET /components` | components browse, filtered by a plain GET form |
| `GET /components/<slug>` | one component, all four tab panels in the HTML |
| `GET /dishes` | dishes browse, filtered by meal type and preference |
| `GET /dishes/<slug>` | one dish, all four tab panels plus provenance links |
| `GET /menu/<meal_type_slug>` | one meal type's dishes — the third ordering entry point |
| `GET /cart` | the cart, resolved against the catalogue as it stands now |
| `POST /cart/add`, `/cart/update`, `/cart/remove` | cart mutation as plain form posts |
| `POST /api/cart` | the same three intents as JSON, for `cart.js` |
| `GET`/`POST /register` | create a customer account, signed straight in |
| `GET`/`POST /login` | sign in, and continue to wherever you were going |
| `POST /logout` | sign out, and take the cart with it |
| `GET`/`POST /checkout` | review, choose a date and fulfilment, confirm |
| `GET /orders/<reference>` | permanent redirect to the same order under `/account` |
| `GET`/`POST /account` | name, phone, address, dietary notes, default filters |
| `GET /account/orders` | order history, newest first |
| `GET /account/orders/<reference>` | one placed order, as it was shown when placed |
| `POST /account/orders/<reference>/cancel` | cancel from `placed` or `confirmed` |
| `GET /account/balance` | ledger entries and the running balance |
| `GET /api/orders/<reference>/status` | live status for a non-terminal order |

All three browse surfaces offer the **allergen exclusion filter**. It hides an
item that *declares* an allergen, and marks rather than hides an item that
carries it as a cross-contact risk — hiding that would let the filter read as
the safety guarantee it says, at the control itself, that it is not. The strip
offers the whole FSANZ vocabulary every time: a list built from the catalogue
would be shorter, and its shortness would itself be a claim.

The **cart** is a guest cart in the signed session cookie. 01-DOMAIN.md names
six collections and none of them is a cart, so there is no seventh; the cookie
holds item ids and quantities only, and every price shown is read from the
catalogue on the server, so a tampered cookie cannot change one. A line whose
item has since been withdrawn is never dropped — it renders struck through and
blocks checkout until the customer removes it. `cart.merge_into_user_cart` is
the guest-cart merge `04-WORKFLOWS.md` asks for, and it now runs at sign-in.

The add control sits on the item page rather than on a catalogue card. A card
carries no allergen declaration and points at the page that does, so ordering
from the page that shows what is in an item is the order this service
encourages — at the cost of one click the customer was going to make anyway.

**Accounts** are sessions, not tokens: Flask-Login over a signed cookie,
Argon2id password hashing, and one message for every sign-in failure so the
form cannot be used to find out which addresses are registered. Length is the
whole password rule — composition rules buy nothing against a hash nobody can
read. There is deliberately no password reset: a reset is delivered by email,
`04-WORKFLOWS.md` keeps notifications out of v1 entirely, and a reset form
with nothing to send would be a control that appears to work and does not. The
sign-in page says so, and the chef resets a password out of band.

The cart is now stamped with the customer it belongs to. `01-DOMAIN.md` names
six collections and none of them is a cart, so "keyed to `user_id`" is an
owner on the stored cart rather than a seventh collection: signing out clears
it, a cart stamped for somebody else is never read, and signing in folds the
guest cart into the customer's — with any line that will not fit reported
rather than dropped, which is the same rule the cart keeps everywhere else.

**Checkout** is where the catalogue stops being live. Confirming snapshots the
name, the unit price and the *whole* allergen block onto every line, totals in
integer minor units, draws the next `MP-YYMM-NNNN` reference for the month,
writes the order at `placed` / `unpaid`, appends the `charge` to the ledger and
clears the cart. A later catalogue edit cannot reach a placed order, and
`tests/test_checkout.py` holds it to that by editing the item afterwards.

"Atomically" is honoured where a single-node MongoDB allows it: the order is
one document and one atomic write, and the ledger entry is a second write
carrying `order_id`. A two-collection transaction needs a replica set, which a
workstation, a single-node deploy and mongomock do not have — so the order is
written first, and the failure that remains is a charge missing from a ledger,
which is detectable, repairable and logged at ERROR. It is never a customer
charged for an order that does not exist.

Delivery needs an address, and `01-DOMAIN.md` puts the address on the customer
rather than on the order. What is typed at checkout is saved to
`users.delivery_address`, so an order carries no address of its own and the
chef reads the current one.

The confirmation form carries a single-use token and a digest of the lines and
prices it displayed. A double-clicked Place order shows the order it already
placed rather than writing a second one and a second ledger charge, and a cart
that changed in another tab re-renders the page instead of being confirmed
unseen. Two requests racing before either replies share one cookie and are not
covered — that needs a durable idempotency key on the order document, which is
a change to the schema `01-DOMAIN.md` owns.

Dates are validated against the kitchen's own clock, `BUSINESS_TIMEZONE`
(default `Australia/Melbourne`). Every stored timestamp stays UTC; a date a
customer picks is a local one, and for the ten hours between Melbourne midnight
and UTC midnight the two disagree — long enough to offer, and accept, a date
that has already passed.

`scripts/check_boot.py` now prints the whole URL map with the marker each
endpoint carries, and exits non-zero on an unmarked one. It was an empty file
passing a CI gate in silence; the gate now has something behind it.

The **account area** is where a customer reads their own record: their
details, their orders, one order in full, and the balance those orders
built. `/orders/<reference>` — where checkout used to send people —
permanently redirects into it, because two pages rendering one order drift
the moment either gains a control the other lacks, and the cancel button is
that control.

**Cancellation** is a compare-and-set, not a status write.
`services/order_state.py` decides the transition and
`orders.apply_transition` writes it filtered on the status it was decided
against, so a confirmation clicked twice — or a chef moving the same order
on at that moment — cancels once and credits once. The transition is written
first and the offsetting `credit` second: a credit written first would stand
alone if the transition then lost its race, crediting a customer for an
order still being prepared. `tests/test_account.py` holds it to that by
submitting the same cancellation twice and counting the ledger.

**Live status** is `GET /api/orders/<reference>/status`, the second of the
two JSON surfaces `00-SYSTEM.md` allows. Every status is rendered by the
server on first request, so `order-status.js` only saves a refresh; it stops
polling an order that has reached a terminal status.

The customer's **saved preference filters** pre-apply to a browse page
arrived at with nothing stated. A GET form submitted with every box cleared
sends no `preference` at all, which the server cannot tell from a fresh
arrival, so the filter form carries a hidden `filtered=1` and every "clear"
link sets it: a URL that states its filters is taken literally. A page
narrowed by the defaults says so and links to the unfiltered catalogue,
because a shortened list that does not explain itself reads as the whole
catalogue.

**Sign-out** now empties the session rather than only the login. A message
flashed but never rendered — the order confirmation, which carries the
reference — survived `logout_user` and was shown to whoever signed in next
on that browser, as did the open checkout's `last_order_reference`.

The **use-by date is deliberately not computed.** `04-WORKFLOWS.md` defines
it as `prepared_at + shelf_life_days`, shortest across lines, but
`OrderLine` snapshots the name, the price and the allergen block and not the
storage block — so the shelf life is only readable from the catalogue as it
stands now, which the chef may have edited since. A use-by *lengthened*
under a customer is the one direction this must never fail in, so the order
page points at the item's current guidance instead. Closing it means a
storage snapshot on `OrderLine`, which is a change to the order document
`01-DOMAIN.md` owns; the decision is written up there.

Not built: every chef-admin screen — the catalogue editors, the allergen
editor, the order queue, the prep sheet and the chef's view of a customer
ledger.

Next slice: **the chef order queue.** `/chef/orders` needs
`chef_list_order_queue`, which already exists, and the chef half of the
transition writer this slice added — the same compare-and-set, called with
`actor_is_chef=True` so the `chef_note` that `prepping → cancelled` and
`ready → cancelled` require is enforced where `order_state.py` already
demands it.
