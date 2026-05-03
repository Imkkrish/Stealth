"""Shared-secret auth for the Stealth server.

The server holds STEALTH_SHARED_SECRET in its env. The client sends it in
the Socket.IO connection auth payload. Reject the connection if missing
or wrong. That's the whole "auth" story for friends-grade v1.
"""

import hmac
import os


def expected_secret() -> str:
    return os.environ.get("STEALTH_SHARED_SECRET", "").strip()


def check(provided: str | None) -> bool:
    expected = expected_secret()
    if not expected:
        # If no secret is configured on the server, refuse all connections —
        # we never want to silently run "open" in production.
        return False
    if not provided or not isinstance(provided, str):
        return False
    return hmac.compare_digest(provided.strip(), expected)
