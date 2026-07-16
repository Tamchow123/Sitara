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
        return (not self.demo_mode) and self.allow_paid_ai_calls


def _refuse_paid(policy: GenerationPolicy) -> Exception:
    if policy.demo_mode:  # pragma: no cover - callers check demo first
        reason = "demo mode is enabled (DEMO_MODE=true)"
    elif not policy.allow_paid_ai_calls:
        reason = "paid AI calls are disabled (ALLOW_PAID_AI_CALLS=false)"
    else:
        reason = (
            "paid providers are not implemented in Phase 3A; "
            "demo mode is the only supported generation path"
        )
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
