"""Hardened download boundary for provider output (Phase 10 Part B).

A successful Replicate prediction yields exactly one HTTPS output URL on the
provider's delivery host. This module is the ONLY code that fetches it, and it
fetches NOTHING else: only ``https://…replicate.delivery`` hosts are allowed,
embedded credentials and non-HTTPS URLs are rejected, EVERY redirect
destination is re-validated, the stream is chunked and aborted the moment it
exceeds the byte cap, and the URL / query string is NEVER logged. No arbitrary
user-supplied URL can reach this downloader — the only caller passes a URL that
came from a provider prediction object.
"""

from urllib.parse import urlsplit

import httpx
from celery.exceptions import SoftTimeLimitExceeded

# The only hosts a provider output may live on.
_ALLOWED_HOST = "replicate.delivery"
MAX_REDIRECTS = 5


class ImageDownloadError(Exception):
    """A safe, contents-free download failure. Never carries the URL, query
    string, response body or host beyond a coarse reason token."""


def _validate_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ImageDownloadError("non_https")
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise ImageDownloadError("embedded_credentials")
    host = (parts.hostname or "").lower()
    if not host or (host != _ALLOWED_HOST and not host.endswith("." + _ALLOWED_HOST)):
        raise ImageDownloadError("host_not_allowed")


def download_replicate_output(url: str, *, max_bytes: int, timeout_seconds: int) -> bytes:
    """Stream a provider output URL into memory, enforcing every protection.

    Raises :class:`ImageDownloadError` (safe) on any scheme/host/credential/
    redirect/size violation or transport failure."""
    _validate_url(url)
    try:
        with httpx.Client(follow_redirects=False, timeout=httpx.Timeout(timeout_seconds)) as client:
            current = url
            for _ in range(MAX_REDIRECTS + 1):
                with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ImageDownloadError("redirect_without_location")
                        current = str(httpx.URL(current).join(location))
                        _validate_url(current)  # every hop re-validated
                        continue
                    if response.status_code != 200:
                        raise ImageDownloadError("bad_status")
                    buffer = bytearray()
                    for chunk in response.iter_bytes():
                        buffer.extend(chunk)
                        if len(buffer) > max_bytes:
                            # Stop immediately — never buffer an unbounded body.
                            raise ImageDownloadError("too_large")
                    if not buffer:
                        raise ImageDownloadError("empty")
                    return bytes(buffer)
            raise ImageDownloadError("too_many_redirects")
    except ImageDownloadError:
        raise
    except SoftTimeLimitExceeded:
        # A worker interruption is not a download failure — propagate so the
        # pipeline's top-level handler converts it to a bounded retry instead
        # of a terminal image_download_failed.
        raise
    except Exception:  # noqa: BLE001 - varied httpx transport errors
        # Never surface the URL or provider error body.
        raise ImageDownloadError("transport_error") from None
