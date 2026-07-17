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
    design_spec = models.JSONField(null=True, blank=True)
    # Object-storage key only — never a URL; signed delivery is a later phase.
    image_storage_key = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
        ]

    def __str__(self) -> str:
        return f"v{self.version_number} of design {self.design_id}"


class GenerationAttempt(models.Model):
    """Durable state reserved for the later asynchronous generation work.

    Phase 4 stores no prompts, no credentials and no raw provider error
    bodies — ``error_code`` is limited to stable machine-readable codes."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING_TEXT = "running_text", "Running text"
        RUNNING_IMAGE = "running_image", "Running image"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    design_version = models.ForeignKey(
        DesignVersion, on_delete=models.CASCADE, related_name="generation_attempts"
    )
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"GenerationAttempt {self.id} ({self.status})"
