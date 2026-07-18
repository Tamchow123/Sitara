"""Offline fixture provider (Phase 8).

A structured-design provider that makes ZERO network calls and returns a
deterministic, valid DesignSpec built around the design's canonical
source_selections (so it always matches and passes verification). Used by the
management command's ``--fixture`` mode and by tests. It labels itself
``fixture`` so a persisted DesignVersion can never be mistaken for a live
Anthropic result."""

from sitara.ai_gateway.structured_design import (
    StructuredDesignRequest,
    StructuredDesignResult,
)
from sitara.generation.design_spec import NO_REGIONAL_DIRECTION


def build_fixture_spec(source_selections: dict) -> dict:
    """A valid DesignSpec payload echoing ``source_selections`` exactly.

    All narrative text is generic, safe (no designer/brand references; caveats
    are negated) and clearly labelled as an offline placeholder."""
    garment = source_selections.get("garment_type") or "outfit"
    ceremony = source_selections.get("ceremony") or "ceremony"
    regional_style = source_selections.get("regional_style")
    has_regional_direction = bool(regional_style) and regional_style != NO_REGIONAL_DIRECTION
    return {
        "schema_version": 1,
        "source_selections": source_selections,
        "title": f"Offline placeholder concept for a {garment}",
        "concept_summary": (
            "This is an offline placeholder concept specification produced from the "
            f"validated selections for a {garment} for a {ceremony}. It is generated "
            "locally without any live model call and exists only to verify the "
            "generation pipeline end to end."
        ),
        "garment_breakdown": {
            "overall_form": "A placeholder overall form derived from the validated garment choice.",
            "garment_components": ["Placeholder primary component", "Placeholder secondary layer"],
            "silhouette": "A placeholder silhouette description faithful to the selected shape.",
            "drape_or_layering": "Placeholder notes on the drape or layering as selected.",
            "key_proportions": "Placeholder notes on the key visual proportions.",
        },
        "colour_story": {
            "palette_summary": "A placeholder summary of the selected colour palette.",
            "placement": "Placeholder notes on primary, secondary and accent placement.",
            "rationale": "Placeholder visual rationale for the palette.",
        },
        "fabrics_and_texture": [
            {
                "fabric": "Placeholder fabric",
                "placement": "Placeholder placement across the outfit.",
                "finish_and_movement": "Placeholder notes on finish and movement.",
            }
        ],
        "embellishment_plan": {
            "techniques": ["Placeholder embellishment technique"],
            "density": "Placeholder density description reflecting the selection.",
            "placement": ["Placeholder placement"],
            "motifs": ["Placeholder motif language"],
            "restraint_notes": "Placeholder notes on restraint and balance.",
        },
        "coverage_and_drape": {
            "sleeves": "Placeholder sleeve notes honouring the coverage preferences.",
            "neckline": "Placeholder neckline notes.",
            "back_and_midriff": "Placeholder back and midriff coverage notes.",
            "head_covering": "Placeholder head-covering notes reflecting the preference.",
            "dupatta_or_saree_drape": "Placeholder drape notes faithful to the selection.",
        },
        "cultural_context": {
            "regional_direction": (
                "Placeholder note on the broad regional direction where one was supplied."
                if has_regional_direction
                else None
            ),
            "interpretation_notes": ["Placeholder interpretation notes, kept broad and flexible."],
            "safeguards": ["Placeholder safeguard against conflating distinct traditions."],
        },
        "styling_notes": ["Placeholder styling suggestion for local review."],
        "construction_caveats": [
            "This is a concept visualisation only and is not a sewing pattern.",
            "It does not guarantee that the garment can be constructed exactly as shown.",
        ],
        "image_alt_text": (
            f"An offline placeholder concept visualisation of a {garment} for a {ceremony}, "
            "generated locally for pipeline testing."
        ),
    }


class FixtureStructuredDesignProvider:
    """A zero-network provider returning a deterministic valid result.

    ``fail_first`` returns one invalid result before a valid one (to exercise
    the single retry offline)."""

    name = "fixture"

    def __init__(self, *, fixture_name: str = "valid", fail_first: bool = False):
        self.fixture_name = fixture_name
        self.fail_first = fail_first
        self._calls = 0

    def generate(self, request: StructuredDesignRequest) -> StructuredDesignResult:
        self._calls += 1
        if self.fail_first and self._calls == 1:
            return StructuredDesignResult(
                payload=None,
                provider=self.name,
                model=f"fixture:{self.fixture_name}",
                input_tokens=None,
                output_tokens=None,
                stop_reason="invalid",
            )
        return StructuredDesignResult(
            payload=build_fixture_spec(request.source_selections),
            provider=self.name,
            model=f"fixture:{self.fixture_name}",
            input_tokens=None,
            output_tokens=None,
            stop_reason="end_turn",
        )
