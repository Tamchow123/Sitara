"""Private design domain models (Phase 4).

Ownership model (ADR 0004): a ``DesignSession`` is one private design
workspace. It starts life owned by an anonymous browser session — the
browser's Django session data holds the DesignSession UUID under
``sitara_design_session_id`` — and may later be claimed by an authenticated
user. Django preserves session DATA when it rotates the session KEY during
login, so an anonymous workspace survives login without any raw session key
ever being stored in a domain table.

Deliberately absent: raw Django session keys, custom ownership tokens or
cookies, public slugs, sharing/visibility fields, soft deletion. Designs are
private by construction; knowing a UUID never grants access (ownership
filtering happens before any UUID lookup, and failures are 404).
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

DESIGN_TITLE_MAX_LENGTH = 120

# Upper bound for an inspiration selection's position, mirroring the current
# ``settings.MAX_INSPIRATION_IMAGES`` (3). Baked into the database CHECK
# constraint below as a final backstop; the application-level limit is
# enforced in ``services.update_design_draft`` from the live setting.
MAX_INSPIRATION_POSITION = settings.MAX_INSPIRATION_IMAGES


class DesignSession(models.Model):
    """One private design workspace.

    ``user`` is null while the workspace belongs to an anonymous browser
    session; it is set exactly once when that browser logs in or registers
    and touches the design API again (lazy promotion — see services). A user
    may accumulate several DesignSessions (one per browser they started
    designing in before signing in)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        # Deleting a user deletes their private design workspaces.
        on_delete=models.CASCADE,
        related_name="design_sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        owner = self.user.email if self.user_id else "anonymous"
        return f"DesignSession {self.id} ({owner})"


class Design(models.Model):
    """A single bridalwear concept draft inside a workspace.

    ``answers`` exists so the Phase 7 questionnaire can extend the draft
    without a destructive schema change; through the Phase 4 API it is
    server-controlled, always ``{}`` and never client-writable."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        # Phase 10/11 lifecycle: a design moves draft -> generating on a
        # successful enqueue, generating -> generated only once the canonical
        # permanent image ingest (Phase 11 stage E) has stored and verified
        # the final original + thumbnail, and generating -> generation_failed
        # on terminal pipeline failure. A failed design with no DesignVersion
        # may be edited again, which returns it to draft (see
        # services.update_design_draft).
        GENERATING = "generating", "Generating"
        GENERATED = "generated", "Generated"
        GENERATION_FAILED = "generation_failed", "Generation failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    design_session = models.ForeignKey(
        DesignSession, on_delete=models.CASCADE, related_name="designs"
    )
    # The questionnaire version this design's answers are validated against.
    # Null for legacy (Phase 4) title-only designs. Assigned at most once by
    # ``services.update_design_draft`` and never changed afterwards, because
    # persisted answers reference that version's stable question/option ids
    # forever. PROTECT: a version with any linked design can never be deleted.
    questionnaire_version = models.ForeignKey(
        "questionnaire.QuestionnaireVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="designs",
    )
    title = models.CharField(max_length=DESIGN_TITLE_MAX_LENGTH, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    answers = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Newest designs first everywhere; id breaks same-instant ties.
        ordering = ["-created_at", "-id"]
        constraints = [
            # Final backstop restricting the lifecycle field to the known
            # values; the application services own the transitions.
            models.CheckConstraint(
                condition=Q(status__in=["draft", "generating", "generated", "generation_failed"]),
                name="designs_design_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.title or f"Untitled design {self.id}"

    def save(self, *args, **kwargs):
        # The serializer already trims; this backstops direct ORM writes.
        self.title = (self.title or "").strip()
        super().save(*args, **kwargs)


class DesignInspiration(models.Model):
    """One inspiration image a user selected for a design, at a position.

    A through model rather than a plain M2M so the selection carries an
    explicit 1-based ``position`` (the user's ordering) and its own audit
    timestamp. It links to the catalogue asset by ``PROTECT`` and stores
    NOTHING copied from it — no storage key, image hash, rights evidence,
    rights note, verifier detail, image bytes or attribution. The linked
    asset and its live rights record remain the single source of truth, so a
    later rights revocation is reflected immediately (the asset simply stops
    being ``publicly_eligible()`` and the selection is rendered as
    unavailable)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    design = models.ForeignKey(
        Design, on_delete=models.CASCADE, related_name="inspiration_selections"
    )
    inspiration_asset = models.ForeignKey(
        "catalogue.InspirationAsset",
        on_delete=models.PROTECT,
        related_name="design_selections",
    )
    position = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["design", "inspiration_asset"],
                name="designs_inspiration_unique_asset_per_design",
            ),
            models.UniqueConstraint(
                fields=["design", "position"],
                name="designs_inspiration_unique_position_per_design",
            ),
            models.CheckConstraint(
                condition=Q(position__gte=1) & Q(position__lte=MAX_INSPIRATION_POSITION),
                name="designs_inspiration_position_bounds",
            ),
        ]

    def __str__(self) -> str:
        return f"inspiration {self.inspiration_asset_id} at position {self.position}"


class DesignVersion(models.Model):
    """One generated concept iteration (initial concept + one refinement).

    ``MAX_DESIGN_VERSIONS`` is an application-level rule enforced by
    ``services.create_next_design_version``; the database constraints below
    are the final backstop against duplicate or non-positive numbering, not
    against the maximum (future multi-round refinement must not need a
    migration)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    design = models.ForeignKey(Design, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    # The validated DesignSpec payload (Phase 8). NULL until a spec is
    # generated. Never stores prompts, raw provider responses or credentials.
    design_spec = models.JSONField(null=True, blank=True)
    # Narrow provenance for a generated DesignSpec (Phase 8). Present exactly
    # when design_spec is present (all-or-none constraint below). NEVER stores
    # API keys, raw prompts/responses, provider error bodies, headers, hidden
    # reasoning, inspiration storage keys or image bytes.
    design_spec_schema_version = models.PositiveSmallIntegerField(null=True, blank=True)
    design_spec_template_version = models.CharField(max_length=32, blank=True)
    design_spec_provider = models.CharField(max_length=32, blank=True)
    design_spec_model = models.CharField(max_length=100, blank=True)
    design_spec_input_tokens = models.PositiveIntegerField(null=True, blank=True)
    design_spec_output_tokens = models.PositiveIntegerField(null=True, blank=True)
    design_spec_generated_at = models.DateTimeField(null=True, blank=True)
    # The deterministic image prompt built from the DesignSpec (Phase 9) and the
    # builder version that produced it. Present exactly together (all-or-none
    # constraint below) and only when a DesignSpec exists; both empty for legacy
    # rows and for Phase 8 rows that predate prompt building. Never stores a
    # provider call, model id, seed, negative prompt or reference-image data.
    image_prompt = models.TextField(blank=True)
    prompt_builder_version = models.CharField(max_length=32, blank=True)
    # Versioned inspiration-context provenance (Phase 13). Present exactly
    # when a snapshot was built for this version — including an EMPTY item
    # list when no inspiration was selected (all-or-none constraint below).
    # NEVER stores image bytes, storage keys, image hashes, dimensions,
    # rights UUIDs/basis, source/licence URLs, evidence references or
    # verifier/staff identity; audit-only title/attribution are the only
    # caller-visible identity of a selected inspiration.
    inspiration_context = models.JSONField(null=True, blank=True)
    inspiration_context_schema_version = models.PositiveSmallIntegerField(null=True, blank=True)
    inspiration_context_sha256 = models.CharField(max_length=64, blank=True)
    # --- Permanent private image provenance (Phase 11) --------------------
    # Written EXACTLY ONCE by the canonical ingest service and immutable
    # afterwards (all-or-none constraint below; a future processor version
    # creates a NEW DesignVersion). Object-storage keys only — never a URL,
    # signed URL, credential, staging byte, provider output URL, MIME header,
    # EXIF, prompt or answer.
    image_storage_key = models.CharField(max_length=255, blank=True)
    image_sha256 = models.CharField(max_length=64, blank=True)
    image_size_bytes = models.BigIntegerField(null=True, blank=True)
    image_width = models.PositiveIntegerField(null=True, blank=True)
    image_height = models.PositiveIntegerField(null=True, blank=True)
    thumbnail_storage_key = models.CharField(max_length=255, blank=True)
    thumbnail_sha256 = models.CharField(max_length=64, blank=True)
    thumbnail_size_bytes = models.BigIntegerField(null=True, blank=True)
    thumbnail_width = models.PositiveIntegerField(null=True, blank=True)
    thumbnail_height = models.PositiveIntegerField(null=True, blank=True)
    # The exact image-processing behaviour version that produced the stored
    # derivatives (sitara.media.image_processing.DESIGN_IMAGE_PROCESSOR_VERSION).
    image_processor_version = models.CharField(max_length=32, blank=True)
    image_ingested_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Every permanent-image field, used by the all-or-none constraint below
    # and by the ingest service's completeness checks — one list so they can
    # never drift apart.
    PERMANENT_IMAGE_CHAR_FIELDS = (
        "image_storage_key",
        "image_sha256",
        "thumbnail_storage_key",
        "thumbnail_sha256",
        "image_processor_version",
    )
    PERMANENT_IMAGE_NULLABLE_FIELDS = (
        "image_size_bytes",
        "image_width",
        "image_height",
        "thumbnail_size_bytes",
        "thumbnail_width",
        "thumbnail_height",
        "image_ingested_at",
    )

    class Meta:
        ordering = ["version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["design", "version_number"],
                name="designs_version_number_unique_per_design",
            ),
            models.CheckConstraint(
                condition=Q(version_number__gt=0),
                name="designs_version_number_positive",
            ),
            # All-or-none provenance: either there is no spec and every
            # provenance field is absent, or there is a spec and schema
            # version, template, provider, model and generated timestamp are
            # all present. Token counts are independently optional.
            models.CheckConstraint(
                condition=(
                    Q(design_spec__isnull=True)
                    & Q(design_spec_schema_version__isnull=True)
                    & Q(design_spec_template_version="")
                    & Q(design_spec_provider="")
                    & Q(design_spec_model="")
                    & Q(design_spec_generated_at__isnull=True)
                )
                | (
                    Q(design_spec__isnull=False)
                    & Q(design_spec_schema_version__isnull=False)
                    & ~Q(design_spec_template_version="")
                    & ~Q(design_spec_provider="")
                    & ~Q(design_spec_model="")
                    & Q(design_spec_generated_at__isnull=False)
                ),
                name="designs_designversion_spec_provenance_all_or_none",
            ),
            # Token counts, when present, are strictly positive.
            models.CheckConstraint(
                condition=(
                    Q(design_spec_input_tokens__isnull=True) | Q(design_spec_input_tokens__gt=0)
                )
                & (Q(design_spec_output_tokens__isnull=True) | Q(design_spec_output_tokens__gt=0)),
                name="designs_designversion_spec_tokens_positive",
            ),
            # Image-prompt provenance is all-or-none: the prompt and the builder
            # version are both empty or both populated.
            models.CheckConstraint(
                condition=(Q(image_prompt="") & Q(prompt_builder_version=""))
                | (~Q(image_prompt="") & ~Q(prompt_builder_version="")),
                name="designs_designversion_image_prompt_all_or_none",
            ),
            # An image prompt can only exist when a DesignSpec exists; legacy
            # rows without a spec never carry a prompt.
            models.CheckConstraint(
                condition=Q(image_prompt="") | Q(design_spec__isnull=False),
                name="designs_designversion_image_prompt_requires_spec",
            ),
            # Inspiration-context provenance (Phase 13) is all-or-none: either
            # no snapshot was built (legacy/pre-Phase-13 rows) or every field
            # is present — including an empty item list, which is itself a
            # valid, hashed snapshot recording "no inspiration selected".
            models.CheckConstraint(
                condition=(
                    Q(inspiration_context__isnull=True)
                    & Q(inspiration_context_schema_version__isnull=True)
                    & Q(inspiration_context_sha256="")
                )
                | (
                    Q(inspiration_context__isnull=False)
                    & Q(inspiration_context_schema_version__isnull=False)
                    & ~Q(inspiration_context_sha256="")
                ),
                name="designs_designversion_inspiration_context_all_or_none",
            ),
            models.CheckConstraint(
                condition=Q(inspiration_context__isnull=True)
                | Q(inspiration_context_schema_version=1),
                name="designs_designversion_inspiration_context_schema_version_valid",
            ),
            models.CheckConstraint(
                condition=Q(inspiration_context_sha256="")
                | Q(inspiration_context_sha256__regex=r"^[0-9a-f]{64}$"),
                name="designs_designversion_inspiration_context_sha256_shape",
            ),
            # An inspiration-context snapshot can only exist when a DesignSpec
            # exists; legacy rows without a spec never carry one.
            models.CheckConstraint(
                condition=Q(inspiration_context__isnull=True) | Q(design_spec__isnull=False),
                name="designs_designversion_inspiration_context_requires_spec",
            ),
            # Permanent-image provenance (Phase 11) is all-or-none: EVERY
            # field absent (legacy and Phase 10 rows), or EVERY field present
            # (a completed canonical ingest). Partial combinations can never
            # commit.
            models.CheckConstraint(
                condition=(
                    Q(image_storage_key="")
                    & Q(image_sha256="")
                    & Q(image_size_bytes__isnull=True)
                    & Q(image_width__isnull=True)
                    & Q(image_height__isnull=True)
                    & Q(thumbnail_storage_key="")
                    & Q(thumbnail_sha256="")
                    & Q(thumbnail_size_bytes__isnull=True)
                    & Q(thumbnail_width__isnull=True)
                    & Q(thumbnail_height__isnull=True)
                    & Q(image_processor_version="")
                    & Q(image_ingested_at__isnull=True)
                )
                | (
                    ~Q(image_storage_key="")
                    & ~Q(image_sha256="")
                    & Q(image_size_bytes__isnull=False)
                    & Q(image_width__isnull=False)
                    & Q(image_height__isnull=False)
                    & ~Q(thumbnail_storage_key="")
                    & ~Q(thumbnail_sha256="")
                    & Q(thumbnail_size_bytes__isnull=False)
                    & Q(thumbnail_width__isnull=False)
                    & Q(thumbnail_height__isnull=False)
                    & ~Q(image_processor_version="")
                    & Q(image_ingested_at__isnull=False)
                ),
                name="designs_designversion_permanent_image_all_or_none",
            ),
            # A supplied permanent-image hash must be a real SHA-256: exactly
            # 64 lowercase hex characters. Blank means not ingested.
            models.CheckConstraint(
                condition=Q(image_sha256="") | Q(image_sha256__regex=r"^[0-9a-f]{64}$"),
                name="designs_designversion_image_sha256_shape",
            ),
            models.CheckConstraint(
                condition=Q(thumbnail_sha256="") | Q(thumbnail_sha256__regex=r"^[0-9a-f]{64}$"),
                name="designs_designversion_thumbnail_sha256_shape",
            ),
            # Byte sizes and dimensions, when present, are strictly positive.
            models.CheckConstraint(
                condition=(Q(image_size_bytes__isnull=True) | Q(image_size_bytes__gt=0))
                & (Q(image_width__isnull=True) | Q(image_width__gt=0))
                & (Q(image_height__isnull=True) | Q(image_height__gt=0))
                & (Q(thumbnail_size_bytes__isnull=True) | Q(thumbnail_size_bytes__gt=0))
                & (Q(thumbnail_width__isnull=True) | Q(thumbnail_width__gt=0))
                & (Q(thumbnail_height__isnull=True) | Q(thumbnail_height__gt=0)),
                name="designs_designversion_permanent_image_positive",
            ),
            # The original and thumbnail can never share one object key.
            models.CheckConstraint(
                condition=Q(image_storage_key="")
                | Q(thumbnail_storage_key="")
                | ~Q(image_storage_key=models.F("thumbnail_storage_key")),
                name="designs_designversion_image_keys_differ",
            ),
            # Permanent image metadata requires a DesignSpec, an image prompt
            # and a prompt-builder version — an ingested image can never exist
            # on a version whose generation provenance is missing.
            models.CheckConstraint(
                condition=Q(image_storage_key="")
                | (
                    Q(design_spec__isnull=False)
                    & ~Q(image_prompt="")
                    & ~Q(prompt_builder_version="")
                ),
                name="designs_designversion_permanent_image_requires_spec_prompt",
            ),
        ]

    def __str__(self) -> str:
        return f"v{self.version_number} of design {self.design_id}"

    @property
    def has_permanent_image(self) -> bool:
        """True when EVERY permanent-image provenance field is populated.

        The all-or-none database constraint makes partial states impossible
        for committed rows; this property is the single in-Python spelling of
        "ingest completed" used by the pipeline and delivery services."""
        return all(getattr(self, name) != "" for name in self.PERMANENT_IMAGE_CHAR_FIELDS) and all(
            getattr(self, name) is not None for name in self.PERMANENT_IMAGE_NULLABLE_FIELDS
        )


class GenerationAttempt(models.Model):
    """One durable asynchronous generation job for a Design (Phase 10).

    The attempt exists BEFORE the DesignSpec/DesignVersion do — it is created
    ``queued`` at enqueue time and the pipeline links a ``design_version``
    later. It therefore belongs to its ``design`` (required) and carries a
    nullable ``design_version`` link.

    Everything image-related here is PRIVATE provenance (provider, model,
    prediction id, seed, server-authored reproducibility parameters) and the
    raw provider output is staged into private storage — none of it is ever
    exposed through the job API. ``error_code`` is limited to the stable
    machine codes in :mod:`sitara.generation.errors`; no prompt, answer,
    output URL, provider error body or credential is ever stored here."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING_TEXT = "running_text", "Running text"
        RUNNING_IMAGE = "running_image", "Running image"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    # The statuses that count as an in-progress job (at most one per Design).
    IN_PROGRESS_STATUSES = (Status.QUEUED, Status.RUNNING_TEXT, Status.RUNNING_IMAGE)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Required owner: the attempt inherits the Design's private ownership.
    design = models.ForeignKey(Design, on_delete=models.CASCADE, related_name="generation_attempts")
    # Linked once the pipeline creates (or resumes) the DesignVersion. SET_NULL
    # so deleting a version never destroys the attempt's audit row.
    design_version = models.ForeignKey(
        DesignVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generation_attempts",
    )
    # Unique PER DESIGN (see the constraint below), not globally: the same
    # client-supplied key may legitimately recur for a different design.
    idempotency_key = models.UUIDField(default=uuid.uuid4, editable=False)
    # The deterministic Celery task id (the attempt UUID as a string). Blank
    # until the task is submitted; never a broker URL or credential.
    celery_task_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    error_code = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # --- Private provider-submission provenance (image + text; never exposed) ---
    image_provider = models.CharField(max_length=32, blank=True)
    image_model = models.CharField(max_length=100, blank=True)
    image_prediction_id = models.CharField(max_length=128, blank=True)
    # A cryptographically-generated non-negative seed, persisted once before
    # provider submission and reused for every retry. Zero is allowed.
    image_seed = models.BigIntegerField(null=True, blank=True)
    # Set True in the same transaction that persists the seed/parameters, just
    # BEFORE the provider create-prediction call, and cleared only once the
    # outcome is known (prediction id persisted, or a definitely-pre-acceptance
    # failure). If a crash leaves this True with no prediction id, a resume must
    # treat the submission as ambiguous and never blindly resubmit — conservative
    # spend semantics across the best-effort create boundary.
    image_submission_in_flight = models.BooleanField(default=False)
    # Durable TEXT-submission marker (review hardening): set in its own
    # transaction BEFORE the paid Anthropic request and cleared only when the
    # outcome is known (version linked, or a definitively-answered provider
    # outcome). If a crash leaves this True with no linked version, a resumed
    # delivery must treat the text submission as ambiguous and never repeat
    # the paid request or resend the prompt content automatically.
    text_submission_in_flight = models.BooleanField(default=False)
    # Server-authored reproducibility parameters ONLY (aspect ratio, output
    # format/quality, safety tolerance, prompt upsampling). Never the prompt,
    # a token, an output URL, provider error body, answers or image bytes.
    image_parameters = models.JSONField(null=True, blank=True)

    # --- Raw staged provider output (private) ------------------------------
    # Provider output is temporary, so a successful raw image is copied into
    # private storage. These five fields are all-or-none. The FINAL design
    # image provenance lives on DesignVersion and is populated only by the
    # Phase 11 canonical ingest — never here. Staged objects and metadata are
    # retained after ingest (they are part of crash recovery, and permanent
    # storage + database commits are not atomic); purging them is Phase 16.
    staged_image_storage_key = models.CharField(max_length=255, blank=True)
    staged_image_sha256 = models.CharField(max_length=64, blank=True)
    staged_image_size_bytes = models.BigIntegerField(null=True, blank=True)
    staged_image_width = models.PositiveIntegerField(null=True, blank=True)
    staged_image_height = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Idempotency is scoped to the Design: a repeated key for the SAME
            # design replays the same attempt; the same key for a DIFFERENT
            # design is unrelated.
            models.UniqueConstraint(
                fields=["design", "idempotency_key"],
                name="designs_attempt_idempotency_unique_per_design",
            ),
            # At most one in-progress attempt per Design. A partial unique
            # index over queued/running_text/running_image.
            models.UniqueConstraint(
                fields=["design"],
                condition=Q(status__in=["queued", "running_text", "running_image"]),
                name="designs_attempt_single_in_progress_per_design",
            ),
            models.CheckConstraint(
                condition=Q(
                    status__in=[
                        "queued",
                        "running_text",
                        "running_image",
                        "succeeded",
                        "failed",
                    ]
                ),
                name="designs_attempt_status_valid",
            ),
            # Seed, when present, is non-negative (zero allowed).
            models.CheckConstraint(
                condition=Q(image_seed__isnull=True) | Q(image_seed__gte=0),
                name="designs_attempt_seed_non_negative",
            ),
            # A supplied staged hash must be a real SHA-256: exactly 64
            # lowercase hex characters (spec: "SHA-256: exactly 64 when
            # supplied"). Blank means not staged.
            models.CheckConstraint(
                condition=Q(staged_image_sha256="")
                | Q(staged_image_sha256__regex=r"^[0-9a-f]{64}$"),
                name="designs_attempt_sha256_shape",
            ),
            # Staged size/dimensions, when present, are strictly positive.
            models.CheckConstraint(
                condition=(
                    Q(staged_image_size_bytes__isnull=True) | Q(staged_image_size_bytes__gt=0)
                )
                & (Q(staged_image_width__isnull=True) | Q(staged_image_width__gt=0))
                & (Q(staged_image_height__isnull=True) | Q(staged_image_height__gt=0)),
                name="designs_attempt_staged_dimensions_positive",
            ),
            # Staged metadata is all-or-none: key, hash, size, width and height
            # are all populated together or all absent.
            models.CheckConstraint(
                condition=(
                    Q(staged_image_storage_key="")
                    & Q(staged_image_sha256="")
                    & Q(staged_image_size_bytes__isnull=True)
                    & Q(staged_image_width__isnull=True)
                    & Q(staged_image_height__isnull=True)
                )
                | (
                    ~Q(staged_image_storage_key="")
                    & ~Q(staged_image_sha256="")
                    & Q(staged_image_size_bytes__isnull=False)
                    & Q(staged_image_width__isnull=False)
                    & Q(staged_image_height__isnull=False)
                ),
                name="designs_attempt_staged_all_or_none",
            ),
            # A succeeded attempt must carry a DesignVersion, staged image
            # metadata, a blank error code and a completion timestamp.
            models.CheckConstraint(
                condition=~Q(status="succeeded")
                | (
                    Q(design_version__isnull=False)
                    & ~Q(staged_image_storage_key="")
                    & Q(error_code="")
                    & Q(completed_at__isnull=False)
                ),
                name="designs_attempt_succeeded_requirements",
            ),
            # A failed attempt must carry a non-empty stable error code and a
            # completion timestamp.
            models.CheckConstraint(
                condition=~Q(status="failed") | (~Q(error_code="") & Q(completed_at__isnull=False)),
                name="designs_attempt_failed_requirements",
            ),
        ]

    def __str__(self) -> str:
        return f"GenerationAttempt {self.id} ({self.status})"
