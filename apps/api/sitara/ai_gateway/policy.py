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

# Upper bound for a configured model identifier — it must fit the persisted
# ``DesignVersion.design_spec_model`` column (max_length=100), or a successful
# generation could not be recorded. Kept as a literal so this policy module
# stays free of a domain-model import.
_ANTHROPIC_MODEL_MAX_LENGTH = 100


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


def _anthropic_config_ready() -> bool:
    """The live Anthropic configuration is complete: a non-empty API key (after
    stripping) and a non-empty model that fits the persisted model-field bound.
    Never logs or returns the key or model value."""
    key = (settings.ANTHROPIC_API_KEY or "").strip()
    model = (settings.ANTHROPIC_MODEL or "").strip()
    return bool(key) and bool(model) and len(model) <= _ANTHROPIC_MODEL_MAX_LENGTH


def structured_design_generation_is_available() -> bool:
    """INTERNAL, and the SINGLE definition of the live structured-generation
    gate: environment authorisation (both gates open) AND the code-level
    structured-design capability AND a complete Anthropic configuration (a
    non-empty key and a valid model). A configured key alone is never enough,
    and this stays False in demo mode or when paid calls are disabled. Not
    surfaced to the public config. The management command's ``--confirm-live``
    is an ADDITIONAL explicit opt-in, never a substitute for this gate."""
    policy = GenerationPolicy.from_settings()
    return (
        policy.paid_calls_permitted
        and STRUCTURED_DESIGN_PROVIDER_IMPLEMENTED
        and _anthropic_config_ready()
    )


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
        if (
            policy.paid_calls_permitted
            and STRUCTURED_DESIGN_PROVIDER_IMPLEMENTED
            and not _anthropic_config_ready()
        ):
            # Gates open and the capability exists, but the key/model are not
            # configured. Fail closed BEFORE constructing any network client;
            # the message names the reason but never the key or model value.
            raise PaidGenerationDisabled(
                "paid generation refused: the Anthropic configuration is incomplete "
                "(a non-empty API key and a valid model are required)"
            )
        raise _refuse(
            policy, STRUCTURED_DESIGN_PROVIDER_IMPLEMENTED, "structured design generation"
        )
    from .anthropic_provider import AnthropicStructuredDesignProvider

    return AnthropicStructuredDesignProvider()
