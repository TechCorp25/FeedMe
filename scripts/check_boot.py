#!/usr/bin/env python
"""Boot the application and print every route with its auth marker.

`create_app` already refuses to boot when an endpoint carries no marker
(`security/decorators.assert_routes_marked`). This script is what makes
that refusal visible in CI and readable by a person: it lists the whole
URL map with the marker each endpoint carries, so a route that is public
when it should require an account is something you can see rather than
something you have to remember.

It boots against mongomock, so it needs no database and no secrets.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mongomock  # noqa: E402

from app import create_app  # noqa: E402
from app.config import TestingConfig  # noqa: E402
from app.security.decorators import (  # noqa: E402
    FRAMEWORK_ENDPOINTS,
    MARKER_CHEF_REQUIRED,
    MARKER_LOGIN_REQUIRED,
    MARKER_PUBLIC,
    UnmarkedRouteError,
    marker_for,
)

#: How each marker prints. Widened deliberately: a marker that is missing
#: says so in words rather than printing an empty column.
MARKER_LABELS = {
    MARKER_PUBLIC: "public",
    MARKER_LOGIN_REQUIRED: "login required",
    MARKER_CHEF_REQUIRED: "chef only",
    None: "NO MARKER",
}


def main() -> int:
    try:
        app = create_app(TestingConfig(), mongo_client=mongomock.MongoClient())
    except UnmarkedRouteError as error:
        # The boot check fired first; it names the endpoints, so say so and
        # stop rather than printing a map that does not exist.
        print(f"application refused to boot: {error}", file=sys.stderr)
        return 1

    rows: list[tuple[str, str, str, str]] = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint in FRAMEWORK_ENDPOINTS:
            continue
        methods = ",".join(
            sorted(rule.methods - {"HEAD", "OPTIONS"})
        )
        marker = marker_for(app.view_functions[rule.endpoint])
        rows.append(
            (str(rule), methods, rule.endpoint, MARKER_LABELS[marker])
        )

    widths = [max(len(row[column]) for row in rows) for column in range(3)]
    for path, methods, endpoint, label in sorted(rows):
        print(
            f"{path:<{widths[0]}}  {methods:<{widths[1]}}  "
            f"{endpoint:<{widths[2]}}  {label}"
        )

    unmarked = [row for row in rows if row[3] == MARKER_LABELS[None]]
    if unmarked:
        print(
            f"\n{len(unmarked)} endpoint(s) carry no auth marker",
            file=sys.stderr,
        )
        return 1
    print(f"\n{len(rows)} endpoints, every one marked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
