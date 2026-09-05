#!/usr/bin/env python
"""Verify the deployment manifests agree with each other and with the code.

Three files start this application — `Procfile`, `railway.json` and
`render.yaml` — and nothing makes them stay in step. A start command
edited in one and forgotten in the others is invisible until a deploy
runs on the host that was missed, so it is checked here instead.

Then each platform is simulated and the detection is asserted, which
catches the other silent failure: a host renaming a variable, leaving
detection to fall through to `local` and a public service to advertise a
localhost origin.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from app.deployment import CODESPACES, RAILWAY, RENDER, detect  # noqa: E402

HEALTH_PATH = "/health"

EXPECTED = {
    CODESPACES: {
        "environ": {"CODESPACES": "true", "CODESPACE_NAME": "ci-space", "PORT": "5000"},
        "base_url": "https://ci-space-5000.app.github.dev",
        "port": 5000,
        "default_env": "development",
    },
    RAILWAY: {
        "environ": {
            "RAILWAY_ENVIRONMENT": "production",
            "RAILWAY_PUBLIC_DOMAIN": "feedme.up.railway.app",
            "PORT": "8080",
        },
        "base_url": "https://feedme.up.railway.app",
        "port": 8080,
        "default_env": "production",
    },
    RENDER: {
        "environ": {
            "RENDER": "true",
            "RENDER_EXTERNAL_URL": "https://feedme.onrender.com",
            "PORT": "10000",
        },
        "base_url": "https://feedme.onrender.com",
        "port": 10000,
        "default_env": "production",
    },
}


def _procfile_web_command() -> str:
    for line in (ROOT / "Procfile").read_text().splitlines():
        if line.startswith("web:"):
            return line.split(":", 1)[1].strip()
    raise SystemExit("Procfile declares no `web:` process")


def _check_start_commands(failures: list[str]) -> None:
    commands = {
        "Procfile": _procfile_web_command(),
        "railway.json": json.loads((ROOT / "railway.json").read_text())["deploy"][
            "startCommand"
        ],
        "render.yaml": yaml.safe_load((ROOT / "render.yaml").read_text())["services"][
            0
        ]["startCommand"],
    }
    for source, command in commands.items():
        print(f"  {source:<16} {command}")
        if "wsgi:app" not in command:
            failures.append(f"{source} does not start wsgi:app")
        # The platform assigns the port; a hardcoded one binds the wrong
        # socket and the health check never answers.
        if "$PORT" not in command and "${PORT" not in command:
            failures.append(f"{source} ignores the platform's PORT")

    if len(set(commands.values())) != 1:
        failures.append("the three start commands have drifted apart")


def _check_health_checks(failures: list[str]) -> None:
    railway = json.loads((ROOT / "railway.json").read_text())["deploy"]
    render = yaml.safe_load((ROOT / "render.yaml").read_text())["services"][0]
    for source, path in (
        ("railway.json", railway.get("healthcheckPath")),
        ("render.yaml", render.get("healthCheckPath")),
    ):
        print(f"  {source:<16} health check {path}")
        if path != HEALTH_PATH:
            failures.append(f"{source} does not gate the deploy on {HEALTH_PATH}")


def _check_devcontainer(failures: list[str]) -> None:
    raw = (ROOT / ".devcontainer" / "devcontainer.json").read_text()
    # devcontainer.json is JSON with comments.
    config = json.loads(re.sub(r"^\s*//.*$", "", raw, flags=re.MULTILINE))
    ports = config.get("forwardPorts", [])
    declared = int(config.get("remoteEnv", {}).get("PORT", 0))
    print(f"  devcontainer     forwards {ports}, binds {declared}")
    if declared not in ports:
        failures.append(
            f"devcontainer.json forwards {ports} but the app binds {declared}; "
            "the Codespaces URL is derived from the forwarded port"
        )


def _check_detection(failures: list[str]) -> None:
    for name, expected in EXPECTED.items():
        platform = detect(expected["environ"])
        print(
            f"  {name:<16} {platform.base_url}  port {platform.port}  "
            f"env {platform.default_env}"
        )
        if platform.name != name:
            failures.append(f"{name} environment detected as {platform.name}")
        for field in ("base_url", "port", "default_env"):
            actual = getattr(platform, field)
            if actual != expected[field]:
                failures.append(
                    f"{name}: {field} resolved to {actual!r}, "
                    f"expected {expected[field]!r}"
                )
        if not platform.behind_proxy:
            failures.append(f"{name} must trust its platform proxy")


def main() -> int:
    failures: list[str] = []

    print("Start commands")
    _check_start_commands(failures)
    print("\nHealth checks")
    _check_health_checks(failures)
    print("\nCodespaces")
    _check_devcontainer(failures)
    print("\nDetection")
    _check_detection(failures)

    if failures:
        print("\nFAILED", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("\nManifests agree, and every platform resolves as expected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
