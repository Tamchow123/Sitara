"""Fail-closed provider selection.

The ONLY sanctioned way to obtain an AI provider. Rules, in order:

1. ``DEMO_MODE=true``  -> demo providers, always. A configured API token
   never bypasses demo mode and never instantiates a network client.
2. ``ALLOW_PAID_AI_CALLS=false`` -> paid providers refused with
   PaidGenerationDisabled.
3. Both gates open (DEMO_MODE=false AND ALLOW_PAID_AI_CALLS=true) -> a paid
   provider is handed out ONLY for a capability the codebase actually
   implements (see the code-level capability flags below).

Capabilities are explicit CODE-LEVEL flags, never environment variables, so an
operator can never claim a capability the codebase does not have. Phase 8
implements structured-TEXT generation only; image generation and the full
end-to-end pipeline remain unimplemented, so the PUBLIC
``generation_is_available()`` stays False.

Error messages never include API tokens or model names, and this module never
logs them.
"""

from dataclasses import dataclass

from django.conf import settings

from .providers import (
    DemoImageGenerationProvider,
    DemoStructuredDesignProvider,
    ImageGenerationProvider,
    StructuredDesignProvider,
)

# ---------------------------------------------------------------------------
# CODE-LEVEL capability flags — deliberately NOT environment variables.
# ---------------------------------------------------------------------------

# Phase 8: the gated Anthropic structured-TEXT (DesignSpec) provider exists.
STRUCTURED_DESIGN_PROVIDER_IMPLEMENTED = True

# The paid IMAGE-generation provider does not exist yet (a later phase).
IMAGE_PROVIDER_IMPLEMENTED = False


class PaidGenerationDisabled(Exception):
    """Raised whenever a paid provider would be required but is not allowed
    (or not implemented). Message is safe to log."""


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
        """Environment AUTHORISATION only (both gates open). Whether a given
        capability is actually available also depends on implementation — see
        the *_is_available() helpers."""
        return (not self.demo_mode) and self.allow_paid_ai_calls


def generation_is_available() -> bool:
    """The single source of truth for whether END-TO-END (image) generation
    can happen: environment authorisation AND the image provider AND the full
    pipeline. While IMAGE_PROVIDER_IMPLEMENTED is False this returns False for
    EVERY environment combination, so the public config endpoint never claims
    concept generation is available in Phase 8."""
    policy = GenerationPolicy.from_settings()
    return policy.paid_calls_permitted and IMAGE_PROVIDER_IMPLEMENTED


def structured_design_generation_is_available() -> bool:
    """INTERNAL: whether the gated Anthropic structured-TEXT (DesignSpec)
    generation may run — environment authorisation AND the code-level
    structured-design capability. Not surfaced to the public config."""
    policy = GenerationPolicy.from_settings()
    return policy.paid_calls_permitted and STRUCTURED_DESIGN_PROVIDER_IMPLEMENTED


def _refuse(policy: GenerationPolicy, implemented: bool, capability_label: str) -> Exception:
    if policy.demo_mode:  # pragma: no cover - callers check demo first
        reason = "demo mode is enabled (DEMO_MODE=true)"
    elif not policy.allow_paid_ai_calls:
        reason = "paid AI calls are disabled (ALLOW_PAID_AI_CALLS=false)"
    elif not implemented:
        reason = f"the {capability_label} provider is not implemented yet"
    else:  # pragma: no cover - unreachable when the capability is implemented
        reason = "paid generation is unavailable"
    return PaidGenerationDisabled(f"paid generation refused: {reason}")


def get_structured_design_provider() -> StructuredDesignProvider:
    """Legacy Phase 3A demo scaffolding (brief -> dict). Demo mode only; the
    gated Phase 8 structured-text path is
    ``get_structured_design_generation_provider``."""
    policy = GenerationPolicy.from_settings()
    if policy.demo_mode:
        return DemoStructuredDesignProvider()
    raise _refuse(policy, implemented=False, capability_label="demo structured-design")


def get_image_generation_provider() -> ImageGenerationProvider:
    policy = GenerationPolicy.from_settings()
    if policy.demo_mode:
        return DemoImageGenerationProvider()
    raise _refuse(policy, IMAGE_PROVIDER_IMPLEMENTED, "image generation")


def get_structured_design_generation_provider():
    """The Phase 8 gated Anthropic structured-text generation provider.

    NEVER returned in demo mode or when paid calls are disabled; a configured
    key alone is never enough. The provider creates its network client lazily,
    only when ``generate`` is invoked (i.e. after every gate has passed)."""
    policy = GenerationPolicy.from_settings()
    if not structured_design_generation_is_available():
        raise _refuse(
            policy, STRUCTURED_DESIGN_PROVIDER_IMPLEMENTED, "structured design generation"
        )
    from .anthropic_provider import AnthropicStructuredDesignProvider

    return AnthropicStructuredDesignProvider()
