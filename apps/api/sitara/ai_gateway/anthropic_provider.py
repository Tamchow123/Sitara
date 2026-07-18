"""The gated Anthropic structured-design provider (Phase 8).

A narrow wrapper over the Anthropic SDK's first-class structured-output
parsing (``beta.messages.parse`` with ``output_format=DesignSpec``). It is
reached ONLY through ``policy.get_structured_design_generation_provider`` after
every gate passes. The network client is created LAZILY inside ``generate``
(never in ``__init__``), tests inject a fake client, and CI never instantiates
a real one.

No streaming, no extended thinking, no tools, no images, no inspiration
content, and SDK automatic retries are disabled (``max_retries=0``) so the
Sitara service controls the exact provider-call count. Request and response
bodies are never logged.
"""

import anthropic
from django.conf import settings
from pydantic import ValidationError

from sitara.generation.design_spec import DesignSpec

from .structured_design import (
    StructuredDesignProviderError,
    StructuredDesignRequest,
    StructuredDesignResult,
)

_ANTHROPIC_ERROR_CATEGORIES = (
    (anthropic.APITimeoutError, "timeout"),
    (anthropic.AuthenticationError, "authentication"),
    (anthropic.PermissionDeniedError, "permission"),
    (anthropic.RateLimitError, "rate_limit"),
    (anthropic.APIConnectionError, "connection"),
    (anthropic.APIStatusError, "server"),
    (anthropic.APIError, "unknown"),
)


class AnthropicStructuredDesignProvider:
    """Structured-text DesignSpec generation via Anthropic."""

    name = "anthropic"

    def __init__(self, client=None):
        # An injected client (fakes in tests) bypasses lazy real-client
        # creation entirely.
        self._injected_client = client
        # One lazily-created SDK client is cached per provider instance so the
        # single allowed retry reuses it rather than constructing a new client.
        self._cached_client = None

    def _client(self):
        if self._injected_client is not None:
            return self._injected_client
        if self._cached_client is None:
            # Lazy: only reached after all policy gates passed. Client
            # construction is inside a safe error boundary so a configuration
            # or SDK-initialisation failure becomes a generic domain error —
            # never a traceback that could carry the key or model value.
            try:
                self._cached_client = anthropic.Anthropic(
                    api_key=settings.ANTHROPIC_API_KEY,
                    max_retries=0,
                    timeout=settings.ANTHROPIC_TIMEOUT_SECONDS,
                )
            except Exception:
                raise StructuredDesignProviderError("client_initialisation") from None
        return self._cached_client

    def generate(self, request: StructuredDesignRequest) -> StructuredDesignResult:
        client = self._client()
        try:
            message = client.beta.messages.parse(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=request.max_output_tokens,
                system=request.system_prompt,
                messages=[{"role": "user", "content": request.user_message}],
                output_format=DesignSpec,
                timeout=settings.ANTHROPIC_TIMEOUT_SECONDS,
            )
        except ValidationError:
            # The model returned something that did not parse into DesignSpec:
            # a structurally invalid output, not a transport failure. Treated
            # as retryable (payload=None); no body is captured.
            return self._result(None, None, None, "parse_error", refused=False)
        except tuple(cls for cls, _ in _ANTHROPIC_ERROR_CATEGORIES) as exc:
            raise StructuredDesignProviderError(self._categorise(exc)) from None

        stop_reason = getattr(message, "stop_reason", None)
        usage = getattr(message, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)

        if stop_reason == "refusal":
            return self._result(None, input_tokens, output_tokens, stop_reason, refused=True)

        parsed = message.parsed_output
        payload = parsed.model_dump(mode="json") if parsed is not None else None
        return self._result(payload, input_tokens, output_tokens, stop_reason, refused=False)

    @staticmethod
    def _categorise(exc: Exception) -> str:
        for cls, category in _ANTHROPIC_ERROR_CATEGORIES:
            if isinstance(exc, cls):
                return category
        return "unknown"

    def _result(self, payload, input_tokens, output_tokens, stop_reason, *, refused):
        return StructuredDesignResult(
            payload=payload,
            provider=self.name,
            model=settings.ANTHROPIC_MODEL,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
            refused=refused,
        )
