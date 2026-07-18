"""The gated Replicate image provider (Phase 10 Part B).

A narrow adapter over the pinned Replicate SDK's PUBLIC asynchronous
prediction API (``client.predictions.create/get/cancel``). Reached ONLY through
``policy.get_image_generation_provider_async`` after every gate passes. The
network client is created LAZILY (never in ``__init__``) and cached per
instance; tests inject a fake client and CI never instantiates a real one.

Deliberately NOT used: ``replicate.run()`` (blocking), streaming, webhooks, a
hard-coded model version/digest, reference images, negative prompts, or implicit
prompt upsampling. Only safe structured metadata crosses back out as an
:class:`ImagePrediction`; the raw SDK object, tokens, request headers, provider
error bodies and dashboard URLs never leave this module.
"""

import httpx
from django.conf import settings

from .image_generation import (
    ImageGenerationRequest,
    ImagePrediction,
    ImageProviderError,
)

# httpx transport failures that mean the create request may or may not have been
# accepted by Replicate — conservative spend semantics require NOT resubmitting.
_AMBIGUOUS_CREATE_ERRORS = (
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
)
# httpx failures that happen BEFORE the request could have been accepted — safe
# to retry a submission.
_PRE_ACCEPTANCE_CREATE_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
)


class ReplicateImageProvider:
    """Create, poll and cancel a Replicate image prediction."""

    name = "replicate"

    def __init__(self, client=None):
        self._injected_client = client
        self._cached_client = None

    def _client(self):
        if self._injected_client is not None:
            return self._injected_client
        if self._cached_client is None:
            # Lazy: only reached after all policy gates passed. Construction is
            # inside a safe boundary so an SDK/init failure becomes a generic
            # transient error — never a traceback carrying the token.
            from replicate.client import Client

            try:
                self._cached_client = Client(
                    api_token=settings.REPLICATE_API_TOKEN,
                    timeout=httpx.Timeout(settings.REPLICATE_TIMEOUT_SECONDS),
                )
            except Exception:
                raise ImageProviderError("client_initialisation") from None
        return self._cached_client

    def create_prediction(self, request: ImageGenerationRequest) -> ImagePrediction:
        # Re-check the paid-image gate immediately before every new submission
        # (a previously accepted prediction may still be polled if the flag is
        # later disabled, but a NEW submission must satisfy the gate now).
        from .policy import image_generation_is_available

        if not image_generation_is_available():
            raise ImageProviderError("gate_closed")
        # Defence in depth: reference images are rejected at request construction
        # (ImageGenerationRequest.__post_init__), so a request that reached here
        # already carries none.
        client = self._client()
        model_input = {
            "prompt": request.prompt,
            "seed": request.seed,
            "aspect_ratio": request.aspect_ratio,
            "output_format": request.output_format,
            "output_quality": request.output_quality,
            "safety_tolerance": request.safety_tolerance,
            "prompt_upsampling": request.prompt_upsampling,
        }
        try:
            prediction = client.predictions.create(model=request.model, input=model_input)
        except _AMBIGUOUS_CREATE_ERRORS:
            # The request may already have been accepted — never resubmit.
            raise ImageProviderError("create_ambiguous", ambiguous_acceptance=True) from None
        except _PRE_ACCEPTANCE_CREATE_ERRORS:
            raise ImageProviderError("create_pre_acceptance") from None
        except Exception as exc:
            # Any other create failure (SDK/API error): conservatively treat a
            # bare timeout as ambiguous, otherwise pre-acceptance. Never capture
            # the provider error body.
            if _looks_like_timeout(exc):
                raise ImageProviderError("create_ambiguous", ambiguous_acceptance=True) from None
            raise ImageProviderError("create_failed") from None
        return self._to_prediction(prediction, request.model)

    def get_prediction(self, prediction_id: str) -> ImagePrediction:
        client = self._client()
        try:
            prediction = client.predictions.get(prediction_id)
        except Exception:
            # Transient transport failure while polling — bounded retry, same id.
            raise ImageProviderError("poll_failed") from None
        return self._to_prediction(prediction, getattr(prediction, "model", "") or "")

    def cancel_prediction(self, prediction_id: str) -> None:
        client = self._client()
        try:
            client.predictions.cancel(prediction_id)
        except Exception:
            # Best-effort cancellation; never raises out of a timeout path.
            return

    def _to_prediction(self, prediction, model: str) -> ImagePrediction:
        status = getattr(prediction, "status", "") or ""
        output_url = _extract_output_url(getattr(prediction, "output", None))
        return ImagePrediction(
            prediction_id=getattr(prediction, "id", "") or "",
            provider=self.name,
            model=model or "",
            status=status,
            output_url=output_url,
        )


def _looks_like_timeout(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name


def _extract_output_url(output):
    """Extract a single output URL from the SDK output (str / FileOutput / list).
    Never returns the raw SDK object."""
    if output is None:
        return None
    if isinstance(output, list | tuple):
        output = output[0] if output else None
    if output is None:
        return None
    url = getattr(output, "url", None)
    if url:
        return str(url)
    return str(output)
