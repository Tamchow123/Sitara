"""Private design result readiness, revalidation and curated payload (Phase 12).

A DesignVersion is result-READY only once every user-facing prerequisite is
structurally present: a persisted DesignSpec, its schema version, the built
image prompt and its builder version, and complete permanent image
provenance (original + thumbnail + processor version + ingest timestamp).
Readiness is checked directly from these fields — never inferred from
``Design.status`` alone, which is a coarser lifecycle flag that can diverge
from per-version state (e.g. across a refinement).

Before a result is ever returned the persisted DesignSpec JSON is
revalidated through the authoritative Pydantic ``DesignSpec`` model, its
schema version is confirmed supported, and it is rerun through the
generated-content safety scan. Any failure means the stored content is
corrupt, unsupported or unsafe — reported as the controlled
:class:`DesignResultUnavailable` (503), never a raw exception and never the
offending content.

The curated payload this module builds is a purpose-built, user-facing shape
— never the raw DesignVersion model or DesignSpec dictionary. It omits
``source_selections``, the image prompt, prompt-builder version, provider/
model/token provenance, storage keys, hashes, staged metadata and every
signed URL; Phase 11's image endpoint remains the only signed-image URL
issuer.
"""

from pydantic import ValidationError

from sitara.generation.design_spec import DESIGN_SPEC_SCHEMA_VERSION, DesignSpec
from sitara.generation.input_safety import GeneratedContentRejected
from sitara.generation.services import scan_design_spec_or_raise

from .jobs import _iso
from .models import DesignVersion


class DesignResultError(Exception):
    """Base class for design-result failures."""


class DesignResultNotReady(DesignResultError):
    """The DesignVersion has not yet reached every result prerequisite."""


class DesignResultUnavailable(DesignResultError):
    """Persisted content is corrupt, unsupported or unsafe."""


def _has_result_prerequisites(version: DesignVersion) -> bool:
    return (
        version.design_spec is not None
        and version.design_spec_schema_version is not None
        and version.image_prompt != ""
        and version.prompt_builder_version != ""
        and version.has_permanent_image
    )


def load_validated_design_spec(version: DesignVersion) -> DesignSpec:
    """Revalidate one DesignVersion's persisted DesignSpec end to end.

    Raises :class:`DesignResultNotReady` when a structural prerequisite is
    missing (this also asserts permanent-image provenance is complete, via
    :func:`_has_result_prerequisites`), and :class:`DesignResultUnavailable`
    when the persisted content fails revalidation, its schema version is
    unsupported, or it fails the generated-content safety scan."""
    if not _has_result_prerequisites(version):
        raise DesignResultNotReady("this design version has no complete result yet")

    if version.design_spec_schema_version != DESIGN_SPEC_SCHEMA_VERSION:
        raise DesignResultUnavailable("persisted design spec schema version is not supported")

    try:
        spec = DesignSpec.model_validate(version.design_spec)
    except ValidationError as exc:
        raise DesignResultUnavailable("stored design spec failed validation") from exc

    try:
        scan_design_spec_or_raise(spec)
    except GeneratedContentRejected as exc:
        raise DesignResultUnavailable("stored design spec failed the safety scan") from exc

    return spec


def design_result_payload(version: DesignVersion, spec: DesignSpec) -> dict:
    """The curated ``{"result": {...}}`` body for one owned, ready
    DesignVersion and its revalidated DesignSpec."""
    return {
        "result": {
            "design_id": str(version.design_id),
            "design_version_id": str(version.id),
            "version_number": version.version_number,
            "title": spec.title,
            "concept_summary": spec.concept_summary,
            "garment_breakdown": {
                "overall_form": spec.garment_breakdown.overall_form,
                "garment_components": list(spec.garment_breakdown.garment_components),
                "silhouette": spec.garment_breakdown.silhouette,
                "drape_or_layering": spec.garment_breakdown.drape_or_layering,
                "key_proportions": spec.garment_breakdown.key_proportions,
            },
            "colour_story": {
                "palette_summary": spec.colour_story.palette_summary,
                "placement": spec.colour_story.placement,
                "rationale": spec.colour_story.rationale,
            },
            "fabrics_and_texture": [
                {
                    "fabric": entry.fabric,
                    "placement": entry.placement,
                    "finish_and_movement": entry.finish_and_movement,
                }
                for entry in spec.fabrics_and_texture
            ],
            "embellishment_plan": {
                "techniques": list(spec.embellishment_plan.techniques),
                "density": spec.embellishment_plan.density,
                "placement": list(spec.embellishment_plan.placement),
                "motifs": list(spec.embellishment_plan.motifs),
                "restraint_notes": spec.embellishment_plan.restraint_notes,
            },
            "coverage_and_drape": {
                "sleeves": spec.coverage_and_drape.sleeves,
                "neckline": spec.coverage_and_drape.neckline,
                "back_and_midriff": spec.coverage_and_drape.back_and_midriff,
                "head_covering": spec.coverage_and_drape.head_covering,
                "dupatta_or_saree_drape": spec.coverage_and_drape.dupatta_or_saree_drape,
            },
            "cultural_context": {
                "regional_direction": spec.cultural_context.regional_direction,
                "interpretation_notes": list(spec.cultural_context.interpretation_notes),
                "safeguards": list(spec.cultural_context.safeguards),
            },
            "styling_notes": list(spec.styling_notes),
            "construction_caveats": list(spec.construction_caveats),
            "image_alt_text": spec.image_alt_text,
            "created_at": _iso(version.design_spec_generated_at),
        }
    }
