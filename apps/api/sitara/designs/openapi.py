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


class DesignListVersionSerializer(serializers.Serializer):
    """One version inside a gallery row (Phase 21).

    No signed URL, storage key, hash, prompt or note text — see
    ``serializers._version_row_payload`` for why a list is the wrong place for a
    bearer token. ``job_status`` is this version's own progress and may be null on
    a legacy row whose attempt was deleted."""

    id = serializers.UUIDField()
    version_number = serializers.IntegerField(min_value=1)
    is_demo = serializers.BooleanField()
    has_image = serializers.BooleanField()
    job_status = serializers.ChoiceField(choices=GenerationAttempt.Status.choices, allow_null=True)
    created_at = serializers.DateTimeField()


class DesignListItemSerializer(serializers.Serializer):
    """A gallery row — no questionnaire schema, no inspiration records, no job."""

    id = serializers.UUIDField()
    title = serializers.CharField(allow_blank=True)
    display_title = serializers.CharField(
        help_text=(
            "The name to show on a card. The design's own title when it has one, "
            "otherwise the concept's name taken from its newest generated version, "
            "otherwise a plain placeholder. The ONLY spec-derived text in this "
            "payload — see serializers._display_title for the name/description "
            "boundary that admits it."
        ),
    )
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    # Null until there is a version to report a mode for.
    is_demo = serializers.BooleanField(allow_null=True)
    version_count = serializers.IntegerField(min_value=0)
    versions = DesignListVersionSerializer(many=True)


class DesignListResponseSerializer(serializers.Serializer):
    designs = DesignListItemSerializer(many=True)
    # How many the caller owns in total, so "that is all of them" is
    # distinguishable from "there is another page" without asking for one.
    total = serializers.IntegerField(min_value=0)
    limit = serializers.IntegerField(min_value=1)
    offset = serializers.IntegerField(min_value=0)


class DesignQuestionnaireSerializer(serializers.Serializer):
    """The questionnaire a design is pinned to (null for legacy designs)."""

    id = serializers.UUIDField()
    version = serializers.IntegerField()
    schema = QuestionnaireSchemaSerializer()


class SelectedInspirationSerializer(serializers.Serializer):
    """One HISTORICAL curated-catalogue selection, made before Phase 22.

    Phase 22 (ADR 0025) retired the catalogue from the product: no design can
    gain a selection any more, and the three endpoints that once streamed an
    asset's bytes and attribution are gone. So the asset object went with them —
    documenting a payload that named dead URLs would be worse than documenting
    none — and ``available`` is permanently false, meaning "no longer usable in
    a design".

    The rows themselves are untouched, as is every ``DesignVersion``'s frozen
    ``inspiration_context`` acknowledgement, which is rendered from its own
    snapshot by the result endpoint."""

    id = serializers.UUIDField(help_text="The selected inspiration asset id.")
    position = serializers.IntegerField()
    available = serializers.BooleanField(
        help_text="Always false since the catalogue was retired (ADR 0025)."
    )


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


class DesignReferencesResponseSerializer(serializers.Serializer):
    """Just the design's own uploaded references, and nothing else.

    The whole point is what is ABSENT. The phone-handoff panel asks "has a
    photograph arrived yet?" every couple of seconds for as long as a code is
    live, and answering that with the full design detail meant re-sending the
    entire versioned questionnaire schema, the customer's saved answers and the
    latest job snapshot — none of which the question is about, and none of which
    changes between two polls — over the same shop wifi the customer's phone is
    using to push the photograph. Widening this payload puts that back."""

    inspiration_uploads = InspirationUploadSerializer(many=True)


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


class RenderSendStateSerializer(serializers.Serializer):
    """What the owner needs before pressing Send (Phase 21, ADR 0022).

    Still no recipient address, for the same reason as above. What is new is
    ``suggested_filename``, which is the owner's OWN free text read back to them:
    the name they last chose for this exact render, or their design's title if
    they have not chosen one. It is never derived from an annotation note."""

    used = serializers.IntegerField(
        min_value=0,
        help_text=(
            "How many times this render has already been emailed. A lifetime "
            "total per render, not a rate that refills."
        ),
    )
    limit = serializers.IntegerField(
        min_value=1,
        help_text=(
            "The most times this render may EVER be emailed. Reaching it is a "
            "409 send_limit_reached, not a 429 — no waiting returns an allowance "
            "that is spent for good."
        ),
    )
    suggested_filename = serializers.CharField(
        allow_blank=True,
        help_text=(
            "Pre-fill the name field with this: the name last chosen for this "
            "render, else the design's title, else blank. Your annotation notes "
            "are never used here."
        ),
    )


class RenderSendStateResponseSerializer(serializers.Serializer):
    send = RenderSendStateSerializer()


class ReferenceUploadGrantSerializer(serializers.Serializer):
    """A freshly minted handoff grant, returned ONCE to the design's owner.

    ``token`` is the plaintext secret and this is the only response in the whole
    API that contains it: the row stores a digest, and no later request can read
    it back. It is a BEARER credential — whoever can see it can add a reference
    to this design — and the exposure is accepted and bounded (ADR 0026), not
    removed. It carries no read capability of any kind."""

    id = serializers.UUIDField(help_text="The grant row, for revoking it later.")
    token = serializers.CharField(
        help_text=(
            "The plaintext secret, returned exactly once. Put it in the QR code "
            "and nowhere else: never a log, never storage, never another response."
        )
    )
    expires_at = serializers.DateTimeField(
        help_text="When the grant stops working, whatever else happens."
    )
    slots_remaining = serializers.IntegerField(
        min_value=0,
        help_text=(
            "How many reference slots the design has left. A grant is spent when "
            "this reaches zero, and the phone is told no more than any other "
            "unusable grant would be told."
        ),
    )


class ReferenceUploadGrantResponseSerializer(serializers.Serializer):
    grant = ReferenceUploadGrantSerializer()


class ReferenceUploadGrantRevokedSerializer(serializers.Serializer):
    revoked = serializers.IntegerField(
        min_value=0, help_text="How many live grants this call revoked. Zero is a success."
    )


class GrantUploadWriteSerializer(serializers.Serializer):
    """The phone-side multipart body.

    ``grant_token`` is the secret, sent in the BODY rather than the URL so it
    cannot land in a web-server access log, a Referer header or a browser
    history entry. The client's filename and declared content type are ignored
    exactly as on the owner's own upload endpoint — only the decoded image is
    trusted, and the storage key is server-generated."""

    grant_token = serializers.CharField(
        write_only=True,
        trim_whitespace=True,
        max_length=512,
        help_text="The handoff secret, from the scanned code. Never logged.",
    )
    image = serializers.FileField(help_text="The image file. JPEG, PNG or single-frame WebP.")
    rights_acknowledged = serializers.BooleanField(
        help_text=(
            "Must be true, and must be given ON THIS DEVICE by the person "
            "choosing the image. A tick on the iPad does not carry across."
        )
    )


class GrantUploadAcceptedSerializer(serializers.Serializer):
    """What the phone learns after a successful upload — and nothing more.

    One constant field, which is a decision rather than an oversight. Not the
    design's id, title, answers, versions or images; and — the part that is easy
    to get wrong — not how many reference slots are left either.

    A remaining-slots count looks harmless and is not. The cap is on the DESIGN
    and ``MAX_INSPIRATION_IMAGES`` is public, so a caller who knows how many
    photographs they sent through this code can subtract and recover how many
    references the design already had before their code existed. In the ordinary
    shop flow — a stylist adds one photograph on the iPad, then mints a code for
    the customer — the very first upload would tell the phone that something
    else was already attached to a design it is not allowed to read. That is a
    read capability, arrived at by arithmetic, and ADR 0026's non-goal is
    permanent.

    So the phone learns capacity only by trying: an upload succeeds, or the code
    answers with the one indistinguishable "no longer usable". That is one bit
    at a time and each bit is the direct consequence of the caller's own
    action — which is the least that can be disclosed while the customer is
    still able to send a photograph at all."""

    accepted = serializers.BooleanField(
        help_text=(
            "Always true. A constant, so that the success body carries no "
            "information beyond the 201 itself — see the class docstring for "
            "why a remaining-slots count was removed."
        )
    )


class GrantUploadResponseSerializer(serializers.Serializer):
    upload = GrantUploadAcceptedSerializer()


class WalkInSessionEndedSerializer(serializers.Serializer):
    """What ending a walk-in session reports back (Phase 22, ADR 0027).

    One boolean, and deliberately nothing else — not the workspace id, not how
    many designs it held, not how many handoff codes were revoked. The next
    person to pick up this iPad may be reading the screen, and a count is a
    fact about the customer who just left."""

    ended = serializers.BooleanField(
        help_text=(
            "True when there was a walk-in workspace to hand back, false when "
            "there was not. Ending twice is not an error."
        )
    )
