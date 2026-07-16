"""Fail-closed provider selection.

The ONLY sanctioned way to obtain an AI provider. Rules, in order:

1. ``DEMO_MODE=true``  -> demo providers, always. A configured API token
   never bypasses demo mode.
2. ``ALLOW_PAID_AI_CALLS=false`` -> paid providers refused with
   PaidGenerationDisabled.
3. Both gates open (DEMO_MODE=false AND ALLOW_PAID_AI_CALLS=true) -> still
   refused in Phase 3A, because no paid provider is implemented yet. The
   paid path will be added, behind these same gates, in a later phase.

Error messages never include API tokens, and this module never logs them.
"""

from dataclasses import dataclass

from django.conf import settings

from .providers import (
    DemoImageGenerationProvider,
    DemoStructuredDesignProvider,
    ImageGenerationProvider,
    StructuredDesignProvider,
)


class PaidGenerationDisabled(Exception):
    """Raised whenever a paid provider would be required but is not allowed
    (or, in Phase 3A, not implemented). Message is safe to log."""


# CODE-LEVEL capability flag: flips to True only in the future task that
# actually implements the paid Anthropic/Replicate providers. Deliberately
# NOT an environment variable — an operator must never be able to claim a
# capability the codebase does not have.
PAID_PROVIDERS_IMPLEMENTED = False


@dataclass(frozen=True)
class GenerationPolicy:
    demo_mode: bool
    allow_paid_ai_calls: bool

    @classmethod
    def from_settings(cls) -> "GenerationPolicy":
        return cls(
            demo_mode=bool(settings.DEMO_MODE),
            allow_paid_ai_calls=bool(settings.ALLOW_PAID_AI_CALLS),
        )

    @property
    def paid_calls_permitted(self) -> bool:
        """Environment AUTHORISATION only (both gates open). Whether
        generation is actually available also depends on implementation
        capability — see generation_is_available()."""
        return (not self.demo_mode) and self.allow_paid_ai_calls


def generation_is_available() -> bool:
    """The single source of truth for whether paid generation can happen:
    environment authorisation (both gates) AND implementation availability.

    The public config endpoint and the provider factory both derive from
    this state, so they can never contradict each other: while
    PAID_PROVIDERS_IMPLEMENTED is False, this returns False for EVERY
    environment combination, including DEMO_MODE=false with
    ALLOW_PAID_AI_CALLS=true."""
    policy = GenerationPolicy.from_settings()
    return policy.paid_calls_permitted and PAID_PROVIDERS_IMPLEMENTED


def _refuse_paid(policy: GenerationPolicy) -> Exception:
    if policy.demo_mode:  # pragma: no cover - callers check demo first
        reason = "demo mode is enabled (DEMO_MODE=true)"
    elif not policy.allow_paid_ai_calls:
        reason = "paid AI calls are disabled (ALLOW_PAID_AI_CALLS=false)"
    elif not PAID_PROVIDERS_IMPLEMENTED:
        reason = (
            "paid providers are not implemented in Phase 3A; "
            "demo mode is the only supported generation path"
        )
    else:  # pragma: no cover - unreachable until a paid path exists
        reason = "paid generation is unavailable"
    return PaidGenerationDisabled(f"paid generation refused: {reason}")


def get_structured_design_provider() -> StructuredDesignProvider:
    policy = GenerationPolicy.from_settings()
    if policy.demo_mode:
        return DemoStructuredDesignProvider()
    raise _refuse_paid(policy)


def get_image_generation_provider() -> ImageGenerationProvider:
    policy = GenerationPolicy.from_settings()
    if policy.demo_mode:
        return DemoImageGenerationProvider()
    raise _refuse_paid(policy)
