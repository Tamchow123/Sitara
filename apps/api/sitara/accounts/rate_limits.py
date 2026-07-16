"""Fixed-window authentication rate limiting on Django's Redis cache.

- Identifiers (IP addresses, canonical emails) are HMAC-SHA256 hashed with
  the Django secret before entering cache keys: no raw email or IP is ever
  stored in Redis.
- Only REMOTE_ADDR is trusted in Phase 3B; X-Forwarded-For handling is a
  deliberate later decision.
- If the cache is unavailable the caller must FAIL CLOSED (HTTP 503) rather
  than authenticate without protection — RateLimitUnavailable signals that.
"""

import hashlib
import hmac

from django.conf import settings
from django.core.cache import cache

KEY_PREFIX = "authrl"


class RateLimitUnavailable(Exception):
    """The rate-limit cache cannot be reached; authentication must not
    proceed unprotected."""


def hash_identifier(value: str) -> str:
    """HMAC so cache keys cannot be reversed or precomputed without the
    server secret."""
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:40]


def build_key(scope: str, identifier: str) -> str:
    return f"{KEY_PREFIX}:{scope}:{hash_identifier(identifier)}"


def check_and_count(scope: str, identifier: str, limit: int, window_seconds: int) -> int | None:
    """Record one attempt. Returns None when allowed, or a Retry-After value
    in seconds when the limit is exceeded."""
    key = build_key(scope, identifier)
    try:
        cache.add(key, 0, timeout=window_seconds)
        current = cache.incr(key)
    except Exception as exc:  # noqa: BLE001 - any cache fault fails closed
        raise RateLimitUnavailable("rate-limit cache unavailable") from exc
    if current > limit:
        # Fixed window: the full window is a safe conservative Retry-After.
        return window_seconds
    return None


def clear(scope: str, identifier: str) -> None:
    """Best-effort reset (e.g. successful login clears the email counter)."""
    try:
        cache.delete(build_key(scope, identifier))
    except Exception:  # noqa: BLE001 - clearing is best-effort only
        pass


def client_ip(request) -> str:
    """REMOTE_ADDR only — X-Forwarded-For is not trusted in Phase 3B."""
    return request.META.get("REMOTE_ADDR", "unknown")
