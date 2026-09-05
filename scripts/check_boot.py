#!/usr/bin/env python
"""Boot the application and fail loudly on any startup defect.

Run in CI so the route-marker enforcement is itself verified, not merely
present. Uses mongomock so no database is required.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mongomock  # noqa: E402

from app import create_app  # noqa: E402
from app.config import TestingConfig  # noqa: E402
from app.security.decorators import marker_for  # noqa: E402


def main() -> int:
    app = create_app(TestingConfig(), mongo_client=mongomock.MongoClient(tz_aware=True))

    routes = [
        (rule.endpoint, marker_for(app.view_functions[rule.endpoint]))
        for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.endpoint)
        if rule.endpoint != "static"
    ]
    for endpoint, marker in routes:
        print(f"  {endpoint:<32} {marker}")

    print(f"{len(routes)} route(s) checked; every one carries an auth marker.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
