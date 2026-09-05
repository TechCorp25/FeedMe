#!/usr/bin/env python
"""Print what the application resolved about its environment.

The first thing to run on a host that behaves unexpectedly: it names the
platform that was detected, the origin and port derived from it, and
whether the hardened cookie and proxy settings are on — without touching
the database or the network.

Secrets are never printed. Each one is reported only as present or
missing, and whether it is still the development placeholder, which is
the failure this catches most often.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

# Read `.env` the same way `wsgi.py` does, so the report reflects what the
# application would actually resolve on this host.
load_dotenv(override=False)

from app.config import ConfigError, load_config  # noqa: E402
from app.deployment import PlatformError, detect  # noqa: E402

SECRETS = ("SECRET_KEY", "JWT_SECRET", "MONGO_URI")


def _secret_state(name: str, value: str) -> str:
    if not os.environ.get(name):
        return "not set — using a built-in default"
    if "dev-" in value or "not-for-production" in value:
        return "set to a development placeholder"
    return f"set ({len(value)} characters)"


def main() -> int:
    try:
        platform = detect()
    except PlatformError as error:
        print(f"platform detection failed: {error}", file=sys.stderr)
        return 2

    print("Detected platform")
    print(f"  name             {platform.name} ({platform.display_name})")
    origin = platform.external_base_url or "— none published —"
    print(f"  external origin  {origin}")
    print(f"  port to bind     {platform.port}")
    print(f"  behind a proxy   {platform.behind_proxy}")
    print(f"  default env      {platform.default_env}")
    for key, value in platform.details.items():
        print(f"  {key:<16} {value}")

    try:
        config = load_config()
    except ConfigError as error:
        # The common case on a first deploy: production is in force and a
        # required variable is missing. Say which, and stop.
        print(f"\nConfiguration refused to load: {error}", file=sys.stderr)
        return 1

    print("\nResolved configuration")
    print(f"  FLASK_ENV        {config.ENV}")
    print(f"  BASE_URL         {config.BASE_URL}")
    print(f"  MONGO_DB_NAME    {config.MONGO_DB_NAME}")
    print(f"  cookies secure   {config.SESSION_COOKIE_SECURE}")
    print(f"  trust proxy      {config.TRUST_PROXY_HEADERS}")
    print(f"  storage backend  {config.STORAGE_BACKEND}")

    print("\nSecrets")
    for name in SECRETS:
        print(f"  {name:<16} {_secret_state(name, getattr(config, name))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
