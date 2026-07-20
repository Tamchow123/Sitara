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

Additive (Phase 13): ``inspiration_acknowledgements`` is built ONLY from the
persisted ``DesignVersion.inspiration_context`` snapshot — never by
re-querying the live catalogue, so a later asset retirement/expiry never
rewrites a historical result. It carries position/title/attribution only;
provider cues, the asset UUID, garment type, alt text and cultural context
are never included. Legacy (pre-Phase-13) versions with no snapshot yield an
empty list — inspiration context is never a readiness requirement.
"""

from pydantic import ValidationError

from sitara.generation.design_spec import DESIGN_SPEC_SCHEMA_VERSION, DesignSpec
from sitara.generation.input_safety import GeneratedContentRejected
from sitara.generation.inspiration_context import (
    INSPIRATION_CONTEXT_SCHEMA_VERSION,
    InspirationContextSnapshot,
    inspiration_acknowledgements,
    inspiration_context_sha256,
)
from sitara.generation.refinement import (
    REFINEMENT_REQUEST_SCHEMA_VERSION,
    RefinementRequest,
    refinement_request_sha256,
)
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


def load_inspiration_acknowledgements(version: DesignVersion) -> list[dict]:
    """The audit-only acknowledgement list for one DesignVersion's persisted
    inspiration-context snapshot.

    A legacy (pre-Phase-13) version with no snapshot returns an empty list —
    this is never a result-readiness requirement. Raises
    :class:`DesignResultUnavailable` when the persisted content is corrupt,
    its schema version is unsupported, or it fails hash verification —
    never exposing the raw stored content."""
    if version.inspiration_context is None:
        return []

    if version.inspiration_context_schema_version != INSPIRATION_CONTEXT_SCHEMA_VERSION:
        raise DesignResultUnavailable(
            "persisted inspiration context schema version is not supported"
        )

    try:
        snapshot = InspirationContextSnapshot.model_validate(version.inspiration_context)
    except ValidationError as exc:
        raise DesignResultUnavailable("stored inspiration context failed validation") from exc

    if inspiration_context_sha256(snapshot) != version.inspiration_context_sha256:
        raise DesignResultUnavailable("stored inspiration context failed hash verification")

    return inspiration_acknowledgements(snapshot)


def load_lineage(version: DesignVersion) -> dict:
    """The additive ``lineage`` block (Phase 14): ``kind``, the parent
    version id, and — for a refinement — its ``change_type`` only. Never the
    raw note, the refinement-request hash, its schema version, the
    refinement template version, a seed or the source attempt.

    A legacy or initial version (no ``parent_version``) returns
    ``{"kind": "initial", "parent_version_id": None, "refinement": None}``.
    Raises :class:`DesignResultUnavailable` when the persisted refinement
    provenance is incomplete, corrupt, unsupported, or fails hash
    verification — never exposing the raw stored content."""
    if version.parent_version_id is None:
        return {"kind": "initial", "parent_version_id": None, "refinement": None}

    if version.refinement_request is None or version.refinement_request_schema_version is None:
        raise DesignResultUnavailable("persisted refinement provenance is incomplete")
    if version.refinement_request_schema_version != REFINEMENT_REQUEST_SCHEMA_VERSION:
        raise DesignResultUnavailable(
            "persisted refinement request schema version is not supported"
        )
    try:
        request = RefinementRequest.model_validate(version.refinement_request)
    except ValidationError as exc:
        raise DesignResultUnavailable("stored refinement request failed validation") from exc
    if refinement_request_sha256(request) != version.refinement_request_sha256:
        raise DesignResultUnavailable("stored refinement request failed hash verification")

    return {
        "kind": "refinement",
        "parent_version_id": str(version.parent_version_id),
        "refinement": {"change_type": request.change_type},
    }


def design_result_payload(
    version: DesignVersion, spec: DesignSpec, acknowledgements: list[dict], lineage: dict
) -> dict:
    """The curated ``{"result": {...}}`` body for one owned, ready
    DesignVersion, its revalidated DesignSpec, its inspiration
    acknowledgements (see :func:`load_inspiration_acknowledgements`) and its
    lineage (see :func:`load_lineage`)."""
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
            "inspiration_acknowledgements": list(acknowledgements),
            "lineage": lineage,
        }
    }
