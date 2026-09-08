"""Where a redirect is allowed to send someone.

A path that arrives in a form field or a query string decides where the
customer lands next, so it is treated as untrusted everywhere it is
read: only a path on this site is honoured, and anything carrying a
scheme, a host or a control character is discarded. Without this, every
`next` and `return_to` field is an open redirect with a friendly name.
"""

from __future__ import annotations

from urllib.parse import urlsplit


def safe_path(raw: str | None) -> str | None:
    """A same-site path, or None when the value cannot be trusted."""
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return None
    if "\\" in candidate or any(character in candidate for character in "\r\n\t"):
        return None
    parts = urlsplit(candidate)
    if parts.scheme or parts.netloc:
        return None
    return candidate
