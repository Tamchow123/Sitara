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
