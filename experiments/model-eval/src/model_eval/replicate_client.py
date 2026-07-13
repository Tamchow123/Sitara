"""Replicate HTTP adapter and the live-run gate.

This module is the ONLY place that talks to the provider. The adapter is
never even constructed unless every live gate passes:

    ALLOW_PROVIDER_CALLS=true      (environment, exactly "true")
    REPLICATE_API_TOKEN=<token>    (environment, non-empty)
    --budget-usd <positive amount> (CLI)
    --confirm-live                 (CLI)

``--dry-run`` never reaches this module's network paths at all.

The API token is held privately and redacted from every exception message
and log line this module produces. Never print it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import httpx

REPLICATE_API_BASE = "https://api.replicate.com/v1"
ENV_ALLOW = "ALLOW_PROVIDER_CALLS"
ENV_TOKEN = "REPLICATE_API_TOKEN"

TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}


class ProviderGateError(Exception):
    """A live run was attempted without every required gate."""


class ProviderError(Exception):
    """A provider interaction failed (message already token-redacted)."""

    def __init__(self, message: str, *, before_acceptance: bool):
        super().__init__(message)
        # True only when we are CERTAIN the provider never accepted the
        # request (e.g. local error before the HTTP request was sent, or an
        # HTTP 4xx validation rejection). Anything ambiguous must be False so
        # the budget is conservatively treated as spent.
        self.before_acceptance = before_acceptance


def live_gate_failures(
    env: Mapping[str, str],
    *,
    confirm_live: bool,
    budget_usd: float | None,
) -> list[str]:
    """Return the list of unmet live-run requirements (empty = all gates met)."""
    failures: list[str] = []
    if env.get(ENV_ALLOW) != "true":
        failures.append(f"{ENV_ALLOW} is not set to 'true'")
    if not env.get(ENV_TOKEN):
        failures.append(f"{ENV_TOKEN} is not set")
    if budget_usd is None or budget_usd <= 0:
        failures.append("--budget-usd must be a positive amount")
    if not confirm_live:
        failures.append("--confirm-live was not passed")
    return failures


@dataclass(frozen=True)
class Prediction:
    id: str
    status: str
    output: Any
    error: str | None
    model_version: str | None
    raw: dict[str, Any]


class ReplicateAdapter:
    """Minimal typed wrapper over the Replicate predictions API.

    Two strictly separated HTTP clients:

    - ``_client`` carries the bearer token and talks ONLY to the Replicate
      API endpoints.
    - ``_download_client`` fetches output files. It is constructed with NO
      authentication, NO cookies and NO Replicate headers, so the API token
      can never leak to output-hosting/CDN domains. Redirects are followed
      MANUALLY (follow_redirects=False): each Location is resolved and must
      be HTTPS before it is requested, and at most three hops are allowed.
    """

    MAX_DOWNLOAD_REDIRECTS = 3
    _REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

    def __init__(
        self,
        token: str,
        client: httpx.Client | None = None,
        download_client: httpx.Client | None = None,
        timeout_s: float = 120.0,
    ):
        if not token:
            raise ProviderGateError("adapter constructed without an API token")
        self._token = token
        self._client = client or httpx.Client(
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {token}"},
        )
        self._download_client = download_client or httpx.Client(
            timeout=timeout_s,
            follow_redirects=False,
            # Deliberately: no auth header, no cookies, no API headers.
        )

    # -- helpers -------------------------------------------------------------

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "***REDACTED***") if self._token else text

    def _raise_provider_error(self, exc: Exception, *, before_acceptance: bool) -> None:
        raise ProviderError(self._redact(str(exc)), before_acceptance=before_acceptance) from None

    @staticmethod
    def _parse(payload: dict[str, Any]) -> Prediction:
        return Prediction(
            id=str(payload.get("id", "")),
            status=str(payload.get("status", "unknown")),
            output=payload.get("output"),
            error=payload.get("error"),
            model_version=payload.get("version"),
            raw=payload,
        )

    # -- API calls -----------------------------------------------------------

    def create_prediction(
        self,
        replicate_id: str,
        version: str | None,
        input_params: dict[str, Any],
    ) -> Prediction:
        """Create a prediction. Official models (no pinned version) use the
        models/{owner}/{name}/predictions endpoint; pinned versions use the
        generic predictions endpoint."""
        try:
            if version:
                resp = self._client.post(
                    f"{REPLICATE_API_BASE}/predictions",
                    json={"version": version, "input": input_params},
                )
            else:
                resp = self._client.post(
                    f"{REPLICATE_API_BASE}/models/{replicate_id}/predictions",
                    json={"input": input_params},
                )
        except httpx.RequestError as exc:
            # The request may or may not have reached the provider: ambiguous.
            self._raise_provider_error(exc, before_acceptance=False)
        if resp.status_code in (400, 401, 402, 403, 404, 422):
            # Rejected before acceptance: safe to release the reservation.
            raise ProviderError(
                self._redact(f"provider rejected request ({resp.status_code}): {resp.text[:500]}"),
                before_acceptance=True,
            )
        if resp.status_code >= 300:
            raise ProviderError(
                self._redact(f"unexpected provider response {resp.status_code}: {resp.text[:500]}"),
                before_acceptance=False,
            )
        return self._parse(resp.json())

    def get_prediction(self, prediction_id: str) -> Prediction:
        try:
            resp = self._client.get(f"{REPLICATE_API_BASE}/predictions/{prediction_id}")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._raise_provider_error(exc, before_acceptance=False)
        return self._parse(resp.json())

    def download(
        self,
        url: str,
        dest: Path,
        *,
        max_bytes: int,
        allowed_mime_prefixes: tuple[str, ...] = ("image/",),
    ) -> tuple[str, int]:
        """Stream an output file to dest after validating MIME type and size.

        Uses the UNAUTHENTICATED download client — never the API client — so
        the bearer token cannot reach output-hosting domains. Redirects are
        followed manually: at most MAX_DOWNLOAD_REDIRECTS hops, and every
        Location is resolved and required to be HTTPS BEFORE it is requested.
        Refuses to overwrite an existing file. Returns (mime_type, size)."""
        if not url.startswith("https://"):
            raise ProviderError(
                f"refusing non-HTTPS output URL {url!r}", before_acceptance=False
            )
        if dest.exists():
            raise ProviderError(
                f"refusing to overwrite existing output file {dest}", before_acceptance=False
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        current_url = url
        try:
            for _hop in range(self.MAX_DOWNLOAD_REDIRECTS + 1):
                with self._download_client.stream("GET", current_url) as resp:
                    if resp.status_code in self._REDIRECT_STATUSES:
                        current_url = self._resolve_redirect(current_url, resp)
                        continue
                    resp.raise_for_status()
                    mime = resp.headers.get("content-type", "").split(";")[0].strip()
                    if not any(mime.startswith(p) for p in allowed_mime_prefixes):
                        raise ProviderError(
                            f"unexpected output MIME type {mime!r} from provider",
                            before_acceptance=False,
                        )
                    size = 0
                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                raise ProviderError(
                                    f"output exceeded size limit ({max_bytes} bytes)",
                                    before_acceptance=False,
                                )
                            fh.write(chunk)
                os.replace(tmp, dest)
                return mime, size
            raise ProviderError(
                f"output download exceeded the redirect limit "
                f"({self.MAX_DOWNLOAD_REDIRECTS})",
                before_acceptance=False,
            )
        except httpx.HTTPError as exc:
            self._raise_provider_error(exc, before_acceptance=False)
            raise AssertionError("unreachable")  # pragma: no cover
        finally:
            tmp.unlink(missing_ok=True)

    def _resolve_redirect(self, current_url: str, resp: httpx.Response) -> str:
        """Resolve a redirect Location and require an HTTPS destination
        BEFORE it is ever requested."""
        location = resp.headers.get("location")
        if not location:
            raise ProviderError(
                "output download redirect carried no Location header",
                before_acceptance=False,
            )
        try:
            resolved = httpx.URL(current_url).join(location)
        except httpx.InvalidURL:
            raise ProviderError(
                f"output download redirect Location is invalid: {location[:200]!r}",
                before_acceptance=False,
            ) from None
        if resolved.scheme != "https":
            raise ProviderError(
                f"refusing redirect to non-HTTPS URL {str(resolved)[:200]!r}",
                before_acceptance=False,
            )
        return str(resolved)

    def close(self) -> None:
        self._client.close()
        self._download_client.close()


def default_adapter_factory(env: Mapping[str, str]) -> ReplicateAdapter:
    """Construct the real adapter. Callers must have checked the gates; this
    re-checks the token so the adapter can never exist without one."""
    token = env.get(ENV_TOKEN, "")
    return ReplicateAdapter(token)
