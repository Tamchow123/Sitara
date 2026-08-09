"""Documentation-only schema serializers for the design API responses.

These describe the wire shapes for drf-spectacular so the generated
TypeScript is precise; the runtime views build responses from the payload
FUNCTIONS in :mod:`sitara.designs.serializers`. Nothing here ever exposes the
DesignSession id, user, version rows, generation attempts, storage keys,
image hashes, rights evidence, verifier identity or internal notes.
"""

from drf_spectacular.utils import PolymorphicProxySerializer, extend_schema_field
from rest_framework import serializers

from sitara.generation.errors import GENERATION_ERROR_CODES
from sitara.generation.refinement import REFINEMENT_CHANGE_TYPES
from sitara.questionnaire.openapi import QuestionnaireSchemaSerializer

from .annotation_schema import (
    ANNOTATION_ITEM_TYPES,
    ANNOTATION_NOTE_MAX_LENGTH,
    ANNOTATION_PALETTES,
    FREEHAND_MAX_POINTS,
    FREEHAND_MIN_POINTS,
    MAX_ANNOTATION_ITEMS,
    MIN_GEOMETRY_EXTENT,
)
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


class InspirationUploadSerializer(serializers.Serializer):
    """One image the user uploaded as inspiration for their own design.

    Only the sanitised WebP derivative exists server-side; its bytes are served
    exclusively by the ownership-checked image endpoint. No storage key, image
    hash, byte size, filename or client-declared content type is ever
    exposed."""

    id = serializers.UUIDField()
    position = serializers.IntegerField()
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    rights_acknowledged_at = serializers.DateTimeField(
        help_text="When the user affirmed they hold the rights to this image."
    )
    created_at = serializers.DateTimeField()


class InspirationUploadResponseSerializer(serializers.Serializer):
    upload = InspirationUploadSerializer()


class InspirationUploadWriteSerializer(serializers.Serializer):
    """The multipart upload body.

    The client's filename and declared content type are deliberately IGNORED —
    the decoded image is the only thing trusted (see
    ``designs.upload_processing``)."""

    image = serializers.FileField(help_text="The image file. JPEG, PNG or single-frame WebP.")
    rights_acknowledged = serializers.BooleanField(
        help_text="Must be true: the user affirms they hold the rights to this image."
    )


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
    # Since Phase 14: which pipeline branch this job runs, so the frontend can
    # render honest refinement-specific progress wording. Never the source
    # version, refinement request text/hash, seed, seed-reuse or any
    # provider/storage provenance.
    generation_kind = serializers.ChoiceField(choices=GenerationAttempt.GenerationKind.values)
    # Since Phase 15: the FROZEN historical demo/live mode this attempt ran
    # in. Never provider, model, manifest or asset details.
    is_demo = serializers.BooleanField()
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
    # Since Phase 16B: the user's OWN uploaded inspirations. They share the
    # MAX_INSPIRATION_IMAGES budget with the curated selections above.
    inspiration_uploads = InspirationUploadSerializer(many=True)
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


# --- Annotations (Phase 19) ------------------------------------------------
#
# Geometry is modelled as one explicit serializer per item type rather than an
# untyped JSON blob, so the generated TypeScript actually constrains what a
# client may build. The four shapes are mutually exclusive by their field names.


class AnnotationPointSerializer(serializers.Serializer):
    """A normalised point: a fraction of the image's own width and height, in
    the closed range [0, 1]. Never viewport pixels — those would stop meaning
    anything the moment the image were rendered at a different size."""

    x = serializers.FloatField(min_value=0, max_value=1)
    y = serializers.FloatField(min_value=0, max_value=1)


class AnnotationPinGeometrySerializer(serializers.Serializer):
    point = AnnotationPointSerializer()


class AnnotationArrowGeometrySerializer(serializers.Serializer):
    """An arrow must span at least the degenerate-geometry floor.

    OpenAPI cannot express a constraint between two fields, so the rule is stated
    here rather than left for a client to discover by rejection."""

    start = AnnotationPointSerializer()
    end = AnnotationPointSerializer(
        help_text=(
            "The straight-line distance from start must be at least "
            f"{MIN_GEOMETRY_EXTENT} — hypot(dx, dy), not either axis alone; "
            "a zero-length arrow points at nothing."
        )
    )


class AnnotationRectangleGeometrySerializer(serializers.Serializer):
    """Origin plus extent, so one shape has exactly one representation.

    Two rules OpenAPI cannot express, both enforced server-side: each side must
    be at least the degenerate-geometry floor, and the rectangle must stay inside
    the image (``x + width`` and ``y + height`` at most 1)."""

    x = serializers.FloatField(min_value=0, max_value=1)
    y = serializers.FloatField(min_value=0, max_value=1)
    width = serializers.FloatField(
        min_value=0,
        max_value=1,
        help_text=f"At least {MIN_GEOMETRY_EXTENT}, and x + width must not exceed 1.",
    )
    height = serializers.FloatField(
        min_value=0,
        max_value=1,
        help_text=f"At least {MIN_GEOMETRY_EXTENT}, and y + height must not exceed 1.",
    )


class AnnotationFreehandGeometrySerializer(serializers.Serializer):
    # The ceilings live in the description rather than as minItems/maxItems:
    # drf-spectacular derives those from validators, and neither a validator nor
    # ListSerializer's own min_length/max_length survives a nested many=True
    # field, so the keywords never reach the published contract. Prose does, and
    # discoverability is the point — a client should learn the ceiling from the
    # contract rather than from being rejected. Enforcement is, as ever,
    # annotation_schema's.
    points = AnnotationPointSerializer(
        many=True,
        help_text=(
            f"Between {FREEHAND_MIN_POINTS} and {FREEHAND_MAX_POINTS} points, "
            "already simplified by the client."
        ),
    )


@extend_schema_field(
    PolymorphicProxySerializer(
        component_name="AnnotationGeometry",
        serializers=[
            AnnotationPinGeometrySerializer,
            AnnotationArrowGeometrySerializer,
            AnnotationRectangleGeometrySerializer,
            AnnotationFreehandGeometrySerializer,
        ],
        # No discriminator: see the class docstring.
        resource_type_field_name=None,
    )
)
class AnnotationGeometryField(serializers.Field):
    """The geometry for whichever ``type`` the item declares.

    A ``oneOf`` rather than a discriminated union: the discriminator (``type``)
    lives on the ITEM, not inside the geometry object, and OpenAPI's
    ``discriminator`` requires the property to be on each variant. The shapes are
    unambiguous without one, so adding a redundant nested ``type`` purely to
    satisfy the keyword would change the wire contract for no gain.

    A ``PolymorphicProxySerializer`` rather than a hand-written ``oneOf`` of
    ``$ref`` strings: the variant serializers are reachable only from here, so
    drf-spectacular would never register them as components and every ``$ref``
    would dangle. ``openapi-typescript`` rejects the schema outright in that
    state, which is how the first attempt was caught."""

    def to_representation(self, value):
        return value

    def to_internal_value(self, data):
        return data


class AnnotationItemSerializer(serializers.Serializer):
    id = serializers.UUIDField(
        help_text=(
            "A client-generated UUID, stable for this mark's life and unique "
            "within the document."
        )
    )
    type = serializers.ChoiceField(
        choices=list(ANNOTATION_ITEM_TYPES),
        help_text="Must agree with the geometry shape sent alongside it.",
    )
    geometry = AnnotationGeometryField()
    note = serializers.CharField(
        allow_blank=True,
        max_length=ANNOTATION_NOTE_MAX_LENGTH,
        help_text=(
            "Short editorial note, at most "
            f"{ANNOTATION_NOTE_MAX_LENGTH} characters. May be blank for a purely "
            "visual mark. Stored and returned as inert text: markup and URLs are "
            "rejected, and invisible/bidirectional characters are stripped."
        ),
    )
    palette = serializers.ChoiceField(
        choices=list(ANNOTATION_PALETTES),
        help_text="A fixed allowlisted mark colour, never a free colour value.",
    )
    created_order = serializers.IntegerField(
        min_value=1, help_text="The mark's 1-based placement order; unique within a document."
    )


class AnnotationDocumentSerializer(serializers.Serializer):
    schema_version = serializers.IntegerField()
    image_width = serializers.IntegerField(
        help_text="The canonical image width the marks were placed against."
    )
    image_height = serializers.IntegerField()
    items = AnnotationItemSerializer(
        many=True, help_text=f"At most {MAX_ANNOTATION_ITEMS} marks. May be empty."
    )
    revision = serializers.IntegerField(
        help_text=(
            "Counts WRITES to this document. 0 means nothing has ever been saved "
            "for this version; a stored document is always 1 or more. Send the "
            "revision you hold as expected_revision when replacing."
        )
    )
    updated_at = serializers.DateTimeField(
        allow_null=True, help_text="Null when nothing has been saved yet."
    )


class AnnotationDocumentResponseSerializer(serializers.Serializer):
    annotations = AnnotationDocumentSerializer()


class AnnotationDocumentWriteSerializer(serializers.Serializer):
    """A complete replacement. There is no partial update: the client owns the
    whole overlay and sends all of it, so a dropped item is unambiguous rather
    than indistinguishable from an omitted field."""

    schema_version = serializers.IntegerField()
    image_width = serializers.IntegerField(
        help_text=(
            "Must equal the version's own canonical width. Validated against the "
            "server's value and rejected on mismatch — never trusted."
        )
    )
    image_height = serializers.IntegerField()
    items = AnnotationItemSerializer(
        many=True, help_text=f"At most {MAX_ANNOTATION_ITEMS} marks. May be empty."
    )
    expected_revision = serializers.IntegerField(
        min_value=0,
        help_text=(
            "The revision the client believes it holds. Use 0 to create the "
            "document for the first time. A stale value is refused with "
            "annotation_conflict and the stored document is left unchanged."
        ),
    )


class AnnotationConflictErrorSerializer(serializers.Serializer):
    """The 409 body. Deliberately carries the server's current revision and
    nothing else — a conflict must never echo the stored private document."""

    error = serializers.DictField(
        help_text="code=annotation_conflict plus a safe message.",
    )
    revision = serializers.IntegerField(help_text="The server's current revision; reload to it.")


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


class InspirationAcknowledgementResultSerializer(serializers.Serializer):
    """One private audit acknowledgement from the persisted, historical
    inspiration-context snapshot (Phase 13). Deliberately excludes the asset
    UUID, provider cues (garment type, visual description, cultural
    context), storage data and every URL — never reconstructed from the
    live catalogue, so a later asset retirement never rewrites it."""

    position = serializers.IntegerField()
    title = serializers.CharField()
    attribution = serializers.CharField()


class RefinementLineageSerializer(serializers.Serializer):
    """The refinement-specific lineage detail (Phase 14) for a version whose
    ``lineage.kind`` is ``"refinement"``. Deliberately excludes the raw
    optional note, the refinement-request hash, its schema version, the
    refinement template version, a seed and the source attempt."""

    change_type = serializers.ChoiceField(choices=sorted(REFINEMENT_CHANGE_TYPES))


class DesignVersionLineageSerializer(serializers.Serializer):
    """Since Phase 14: additive parent-child lineage for one result. A
    version with no parent (initial, or legacy) reports
    ``kind="initial"``/``parent_version_id=None``/``refinement=None``."""

    # Reuses GenerationAttempt.GenerationKind's exact value set — a version's
    # lineage kind and the attempt that produced it share the same "initial"
    # or "refinement" vocabulary (see ENUM_NAME_OVERRIDES in settings.py).
    kind = serializers.ChoiceField(choices=GenerationAttempt.GenerationKind.values)
    parent_version_id = serializers.UUIDField(allow_null=True)
    refinement = RefinementLineageSerializer(allow_null=True)


class DesignResultSerializer(serializers.Serializer):
    """The purpose-built, curated concept result (Phase 12).

    Deliberately excludes ``source_selections``, questionnaire answers,
    inspiration selections, the image prompt, prompt-builder version,
    DesignSpec provider/model, token counts, provider prediction id,
    provider/model name, seed, image parameters, staged metadata, storage
    keys, hashes, internal byte sizes, the user id, DesignSession id, the
    questionnaire version id, every signed URL and (Phase 14) the raw
    refinement note, refinement-request hash/schema version, seed reuse or
    source attempt."""

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
    # Since Phase 13: audit-only; empty for a legacy pre-Phase-13 version.
    inspiration_acknowledgements = InspirationAcknowledgementResultSerializer(many=True)
    # Since Phase 14: additive parent-child lineage.
    lineage = DesignVersionLineageSerializer()
    # Since Phase 15: this version's own frozen historical demo/live mode.
    is_demo = serializers.BooleanField()


class DesignResultResponseSerializer(serializers.Serializer):
    result = DesignResultSerializer()


# ---------------------------------------------------------------------------
# Phase 19: account render email delivery (documentation-only)
# ---------------------------------------------------------------------------


class RenderSendStatusSerializer(serializers.Serializer):
    """Deliberately only a status.

    No recipient address: the client already knows the account's own address
    from ``/auth/me`` and uses that for its confirmation copy, so echoing it
    here would put an address into a response body for no benefit. No job id
    either — a send is fire-and-forget, with no progress to poll, unlike
    generation."""

    status = serializers.ChoiceField(
        choices=["queued"],
        help_text=(
            "Always 'queued'. A 202 means the delivery task was enqueued, NOT "
            "that a message was sent — rendering and SMTP happen afterwards in "
            "a worker."
        ),
    )


class RenderSendResponseSerializer(serializers.Serializer):
    send = RenderSendStatusSerializer()
