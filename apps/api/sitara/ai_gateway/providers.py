"""AI provider boundary.

Two narrow protocols and their demo implementations. Demo providers are
pure functions over their inputs: deterministic fixture METADATA, no
network clients, no side effects, no cost. Real Anthropic / Replicate
implementations are deliberately absent in Phase 3A — the only way to
obtain a provider is through sitara.ai_gateway.policy, which fails closed.
"""

import hashlib
from typing import Any, Protocol


class StructuredDesignProvider(Protocol):
    """Turns validated questionnaire input into a design-spec payload."""

    def generate_design_spec(self, brief: dict[str, Any]) -> dict[str, Any]: ...


class ImageGenerationProvider(Protocol):
    """Turns a controlled prompt into generated-image metadata."""

    def generate_image(self, prompt: str, *, model: str) -> dict[str, Any]: ...


def _fixture_id(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class DemoStructuredDesignProvider:
    """Deterministic local fixture — identical input, identical output."""

    name = "demo"

    def generate_design_spec(self, brief: dict[str, Any]) -> dict[str, Any]:
        stable = "|".join(f"{key}={brief[key]}" for key in sorted(brief))
        return {
            "provider": "demo",
            "paid_call": False,
            "fixture_id": f"design-{_fixture_id(stable)}",
            "design_spec": {
                "title": "Demo concept (pre-generated fixture)",
                "source": "demo-fixture",
                "brief_echo": dict(sorted(brief.items())),
            },
        }


class DemoImageGenerationProvider:
    """Deterministic local fixture metadata — never a network call."""

    name = "demo"

    def generate_image(self, prompt: str, *, model: str) -> dict[str, Any]:
        return {
            "provider": "demo",
            "paid_call": False,
            "fixture_id": f"image-{_fixture_id(f'{model}|{prompt}')}",
            "model_requested": model,
            "image_ref": "demo-fixtures/placeholder.webp",
        }
