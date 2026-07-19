"""Documentation-only schema serializers for the design API responses.

These describe the wire shapes for drf-spectacular so the generated
TypeScript is precise; the runtime views build responses from the payload
FUNCTIONS in :mod:`sitara.designs.serializers`. Nothing here ever exposes the
DesignSession id, user, version rows, generation attempts, storage keys,
image hashes, rights evidence, verifier identity or internal notes.
"""

from rest_framework import serializers

from sitara.generation.errors import GENERATION_ERROR_CODES
from sitara.questionnaire.openapi import QuestionnaireSchemaSerializer

from .models import GenerationAttempt


class DesignListItemSerializer(serializers.Serializer):
    """A compact list row — no questionnaire schema, no inspiration records."""

    id = serializers.UUIDField()
    title = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class DesignListResponseSerializer(serializers.Serializer):
    designs = DesignListItemSerializer(many=True)


class DesignQuestionnaireSerializer(serializers.Serializer):
    """The questionnaire a design is pinned to (null for legacy designs)."""

    id = serializers.UUIDField()
    version = serializers.IntegerField()
    schema = QuestionnaireSchemaSerializer()


class SelectedInspirationAssetSerializer(serializers.Serializer):
    """The public catalogue fields for an inspiration that is still eligible."""

    id = serializers.UUIDField()
    title = serializers.CharField()
    alt_text = serializers.CharField()
    garment_type = serializers.CharField()
    cultural_context = serializers.CharField()
    attribution = serializers.CharField()
    image_url = serializers.CharField()
    thumbnail_url = serializers.CharField()


class SelectedInspirationSerializer(serializers.Serializer):
    """One inspiration selection with its live availability.

    ``available: false`` with ``asset: null`` means the previously-selected
    asset is no longer publicly eligible (retired, expired or revoked). The
    reason is deliberately not disclosed."""

    id = serializers.UUIDField(help_text="The selected inspiration asset id.")
    position = serializers.IntegerField()
    available = serializers.BooleanField()
    asset = SelectedInspirationAssetSerializer(allow_null=True)


class DesignValidationSuccessSerializer(serializers.Serializer):
    valid = serializers.BooleanField()


class GenerationJobSerializer(serializers.Serializer):
    """The stable public shape of one generation job (Phase 10).

    Deliberately excludes every provider/storage provenance field (provider,
    model, prediction id, seed, parameters, storage key, image hash/size and
    the Celery task id) — only the lifecycle is public."""

    id = serializers.UUIDField()
    design_id = serializers.UUIDField()
    design_version_id = serializers.UUIDField(allow_null=True)
    # Derived from the model so the documented enum can never drift from the
    # actual lifecycle values (and their DB constraints).
    status = serializers.ChoiceField(choices=GenerationAttempt.Status.values)
    # Derived from the backend allowlist so the documented enum can never
    # drift from the actual set of codes a job may carry.
    error_code = serializers.ChoiceField(choices=sorted(GENERATION_ERROR_CODES), allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)


class GenerationJobResponseSerializer(serializers.Serializer):
    job = GenerationJobSerializer()


class DesignDetailResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    status = serializers.CharField()
    questionnaire = DesignQuestionnaireSerializer(allow_null=True)
    answers = serializers.JSONField(help_text="Answers keyed by stable question id.")
    selected_inspirations = SelectedInspirationSerializer(many=True)
    # Since Phase 12: one sanitised public snapshot of the design's most
    # recent generation attempt, or null if it has never attempted
    # generation. Supports durable resume navigation. Still no private
    # attempt provenance and never present on the list payload.
    latest_job = GenerationJobSerializer(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class DesignImageSerializer(serializers.Serializer):
    """One deliverable image: a short-lived signed URL plus its dimensions.

    The URL is a TEMPORARY BEARER URL (usable by anyone holding it until
    expiry) — never persisted, cached or logged. No storage key, hash,
    provider/prediction id, seed or staging metadata is ever exposed."""

    url = serializers.CharField(help_text="Short-lived signed GET URL for the private WebP object.")
    width = serializers.IntegerField()
    height = serializers.IntegerField()


class DesignOriginalImageSerializer(DesignImageSerializer):
    """The original image additionally carries a separately signed
    attachment URL (Phase 12) for a reliable download, sharing the same
    declared expiry as every other URL in the response."""

    download_url = serializers.CharField(
        help_text=(
            "Short-lived signed GET URL that downloads the private WebP " "object as an attachment."
        )
    )


class DesignImagesSerializer(serializers.Serializer):
    original = DesignOriginalImageSerializer()
    thumbnail = DesignImageSerializer()
    expires_at = serializers.DateTimeField(
        help_text="The single instant ALL THREE URLs stop working (ISO-8601)."
    )


class DesignVersionImagesResponseSerializer(serializers.Serializer):
    images = DesignImagesSerializer()


class GarmentBreakdownResultSerializer(serializers.Serializer):
    overall_form = serializers.CharField()
    garment_components = serializers.ListField(child=serializers.CharField())
    silhouette = serializers.CharField()
    drape_or_layering = serializers.CharField()
    key_proportions = serializers.CharField()


class ColourStoryResultSerializer(serializers.Serializer):
    palette_summary = serializers.CharField()
    placement = serializers.CharField()
    rationale = serializers.CharField()


class FabricEntryResultSerializer(serializers.Serializer):
    fabric = serializers.CharField()
    placement = serializers.CharField()
    finish_and_movement = serializers.CharField()


class EmbellishmentPlanResultSerializer(serializers.Serializer):
    techniques = serializers.ListField(child=serializers.CharField())
    density = serializers.CharField()
    placement = serializers.ListField(child=serializers.CharField())
    motifs = serializers.ListField(child=serializers.CharField())
    restraint_notes = serializers.CharField()


class CoverageAndDrapeResultSerializer(serializers.Serializer):
    sleeves = serializers.CharField()
    neckline = serializers.CharField()
    back_and_midriff = serializers.CharField()
    head_covering = serializers.CharField()
    dupatta_or_saree_drape = serializers.CharField()


class CulturalContextResultSerializer(serializers.Serializer):
    regional_direction = serializers.CharField(allow_null=True)
    interpretation_notes = serializers.ListField(child=serializers.CharField())
    safeguards = serializers.ListField(child=serializers.CharField())


class DesignResultSerializer(serializers.Serializer):
    """The purpose-built, curated concept result (Phase 12).

    Deliberately excludes ``source_selections``, questionnaire answers,
    inspiration selections, the image prompt, prompt-builder version,
    DesignSpec provider/model, token counts, provider prediction id,
    provider/model name, seed, image parameters, staged metadata, storage
    keys, hashes, internal byte sizes, the user id, DesignSession id, the
    questionnaire version id and every signed URL."""

    design_id = serializers.UUIDField()
    design_version_id = serializers.UUIDField()
    version_number = serializers.IntegerField()
    title = serializers.CharField()
    concept_summary = serializers.CharField()
    garment_breakdown = GarmentBreakdownResultSerializer()
    colour_story = ColourStoryResultSerializer()
    fabrics_and_texture = FabricEntryResultSerializer(many=True)
    embellishment_plan = EmbellishmentPlanResultSerializer()
    coverage_and_drape = CoverageAndDrapeResultSerializer()
    cultural_context = CulturalContextResultSerializer()
    styling_notes = serializers.ListField(child=serializers.CharField())
    construction_caveats = serializers.ListField(child=serializers.CharField())
    image_alt_text = serializers.CharField()
    created_at = serializers.DateTimeField()


class DesignResultResponseSerializer(serializers.Serializer):
    result = DesignResultSerializer()
