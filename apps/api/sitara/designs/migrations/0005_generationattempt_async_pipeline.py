"""Phase 10 — Design lifecycle statuses and the reshaped GenerationAttempt.

Non-destructive: the required ``design`` FK is added nullable, backfilled from
each existing attempt's ``design_version.design_id``, then made non-null; the
``design_version`` link becomes nullable; global idempotency-key uniqueness is
replaced by per-Design uniqueness. No existing row is dropped or rewritten.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


def backfill_attempt_design(apps, schema_editor):
    """Populate the new ``design`` FK from the existing ``design_version``.

    Every legacy attempt had a required ``design_version``; its
    ``design_id`` is the owning design. Frozen logic (uses the historical
    model), so it stays correct regardless of later runtime changes."""
    GenerationAttempt = apps.get_model("designs", "GenerationAttempt")
    # select_related avoids an N+1 fetch of each related DesignVersion during
    # the backfill (the table is near-empty at this phase, but the join keeps
    # the migration cheap if it is ever re-run against existing rows).
    for attempt in GenerationAttempt.objects.select_related("design_version").iterator():
        attempt.design_id = attempt.design_version.design_id
        attempt.save(update_fields=["design"])


def normalise_legacy_attempts(apps, schema_editor):
    """Make every valid pre-Phase-10 row satisfy the NEW constraints so adding
    them can never abort a deployment (PostgreSQL validates existing rows).

    Three legacy shapes were legal under the old schema but violate the new
    invariants, and all are normalised into constraint-compatible TERMINAL
    audit states — rows are preserved, never deleted:

    - a ``succeeded`` attempt without staged-image metadata (no pre-Phase-10
      code ever staged an image, so such a row cannot represent a real staged
      output) becomes ``failed`` with the stable ``internal_generation_error``
      code and a completion timestamp;
    - a ``failed`` attempt with a blank ``error_code`` or a missing
      ``completed_at`` (both optional pre-Phase-10) gains the stable
      ``internal_generation_error`` code and/or a completion timestamp so the
      new failed-requirements constraint can never reject it;
    - multiple in-progress attempts for one design (the old schema had no
      single-in-progress rule) keep ONLY the newest in-progress row; older
      ones become ``failed``/``internal_generation_error`` (superseded).

    Frozen logic: only historical models, stable literal codes, and
    timezone-aware timestamps."""
    from django.utils import timezone

    GenerationAttempt = apps.get_model("designs", "GenerationAttempt")
    now = timezone.now()

    # Legacy "succeeded" without staged metadata -> terminal failed audit row.
    for attempt in GenerationAttempt.objects.filter(
        status="succeeded", staged_image_storage_key=""
    ).iterator():
        attempt.status = "failed"
        attempt.error_code = "internal_generation_error"
        attempt.completed_at = attempt.completed_at or attempt.updated_at or now
        attempt.save(update_fields=["status", "error_code", "completed_at"])

    # Legacy "failed" rows missing the now-required code and/or timestamp.
    from django.db.models import Q

    for attempt in GenerationAttempt.objects.filter(
        Q(status="failed") & (Q(error_code="") | Q(completed_at__isnull=True))
    ).iterator():
        attempt.error_code = attempt.error_code or "internal_generation_error"
        attempt.completed_at = attempt.completed_at or attempt.updated_at or now
        attempt.save(update_fields=["error_code", "completed_at"])

    # Duplicate in-progress attempts -> keep the newest per design.
    in_progress = ("queued", "running_text", "running_image")
    seen_designs: set = set()
    for attempt in GenerationAttempt.objects.filter(status__in=in_progress).order_by(
        "design_id", "-created_at"
    ):
        if attempt.design_id in seen_designs:
            attempt.status = "failed"
            attempt.error_code = "internal_generation_error"
            attempt.completed_at = attempt.completed_at or now
            attempt.save(update_fields=["status", "error_code", "completed_at"])
        else:
            seen_designs.add(attempt.design_id)


def noop_reverse(apps, schema_editor):
    # Reversing only drops the column again (handled by the schema ops); the
    # backfill itself has nothing to undo.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("designs", "0004_designversion_image_prompt_and_more"),
    ]

    operations = [
        # --- Design lifecycle -------------------------------------------------
        migrations.AlterField(
            model_name="design",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("generating", "Generating"),
                    ("generated", "Generated"),
                    ("generation_failed", "Generation failed"),
                ],
                default="draft",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="design",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    status__in=["draft", "generating", "generated", "generation_failed"]
                ),
                name="designs_design_status_valid",
            ),
        ),
        # --- GenerationAttempt: required design FK (nullable -> backfill -> not null)
        migrations.AddField(
            model_name="generationattempt",
            name="design",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="generation_attempts",
                to="designs.design",
            ),
        ),
        migrations.RunPython(backfill_attempt_design, noop_reverse),
        # Flush the deferred FK checks queued by the backfill NOW — a later
        # ALTER TABLE in this same transaction would otherwise abort with
        # "cannot ALTER TABLE ... because it has pending trigger events".
        migrations.RunSQL(sql="SET CONSTRAINTS ALL IMMEDIATE", reverse_sql=migrations.RunSQL.noop),
        migrations.AlterField(
            model_name="generationattempt",
            name="design",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="generation_attempts",
                to="designs.design",
            ),
        ),
        # design_version becomes nullable, SET_NULL.
        migrations.AlterField(
            model_name="generationattempt",
            name="design_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="generation_attempts",
                to="designs.designversion",
            ),
        ),
        # Drop global idempotency-key uniqueness (replaced by a per-design
        # unique constraint below).
        migrations.AlterField(
            model_name="generationattempt",
            name="idempotency_key",
            field=models.UUIDField(default=uuid.uuid4, editable=False),
        ),
        # --- New attempt columns ---------------------------------------------
        migrations.AddField(
            model_name="generationattempt",
            name="celery_task_id",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="generationattempt",
            name="image_provider",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="generationattempt",
            name="image_model",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="generationattempt",
            name="image_prediction_id",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="generationattempt",
            name="image_seed",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="generationattempt",
            name="image_submission_in_flight",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="generationattempt",
            name="image_parameters",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="generationattempt",
            name="staged_image_storage_key",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="generationattempt",
            name="staged_image_sha256",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="generationattempt",
            name="staged_image_size_bytes",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="generationattempt",
            name="staged_image_width",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="generationattempt",
            name="staged_image_height",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        # --- Legacy-row normalisation (BEFORE the new constraints, which
        # PostgreSQL validates against existing rows) ---------------------------
        migrations.RunPython(normalise_legacy_attempts, noop_reverse),
        # Flush deferred checks again before the constraint ALTERs below.
        migrations.RunSQL(sql="SET CONSTRAINTS ALL IMMEDIATE", reverse_sql=migrations.RunSQL.noop),
        # --- Constraints ------------------------------------------------------
        migrations.AddConstraint(
            model_name="generationattempt",
            constraint=models.UniqueConstraint(
                fields=("design", "idempotency_key"),
                name="designs_attempt_idempotency_unique_per_design",
            ),
        ),
        migrations.AddConstraint(
            model_name="generationattempt",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    status__in=["queued", "running_text", "running_image"]
                ),
                fields=("design",),
                name="designs_attempt_single_in_progress_per_design",
            ),
        ),
        migrations.AddConstraint(
            model_name="generationattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(
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
        ),
        migrations.AddConstraint(
            model_name="generationattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(image_seed__isnull=True) | models.Q(image_seed__gte=0),
                name="designs_attempt_seed_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="generationattempt",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(staged_image_size_bytes__isnull=True)
                    | models.Q(staged_image_size_bytes__gt=0)
                )
                & (
                    models.Q(staged_image_width__isnull=True)
                    | models.Q(staged_image_width__gt=0)
                )
                & (
                    models.Q(staged_image_height__isnull=True)
                    | models.Q(staged_image_height__gt=0)
                ),
                name="designs_attempt_staged_dimensions_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="generationattempt",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(staged_image_storage_key="")
                    & models.Q(staged_image_sha256="")
                    & models.Q(staged_image_size_bytes__isnull=True)
                    & models.Q(staged_image_width__isnull=True)
                    & models.Q(staged_image_height__isnull=True)
                )
                | (
                    ~models.Q(staged_image_storage_key="")
                    & ~models.Q(staged_image_sha256="")
                    & models.Q(staged_image_size_bytes__isnull=False)
                    & models.Q(staged_image_width__isnull=False)
                    & models.Q(staged_image_height__isnull=False)
                ),
                name="designs_attempt_staged_all_or_none",
            ),
        ),
        migrations.AddConstraint(
            model_name="generationattempt",
            constraint=models.CheckConstraint(
                condition=~models.Q(status="succeeded")
                | (
                    models.Q(design_version__isnull=False)
                    & ~models.Q(staged_image_storage_key="")
                    & models.Q(error_code="")
                    & models.Q(completed_at__isnull=False)
                ),
                name="designs_attempt_succeeded_requirements",
            ),
        ),
        migrations.AddConstraint(
            model_name="generationattempt",
            constraint=models.CheckConstraint(
                condition=~models.Q(status="failed")
                | (~models.Q(error_code="") & models.Q(completed_at__isnull=False)),
                name="designs_attempt_failed_requirements",
            ),
        ),
    ]
