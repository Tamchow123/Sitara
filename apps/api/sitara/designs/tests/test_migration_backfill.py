"""Migration 0005 data-migration tests (Phase 10).

Proves, via the real migration graph (MigrationExecutor against historical
model state, not the current ORM), that migration 0005:

- backfills the new required ``design`` FK from ``design_version.design_id``
  for every legacy GenerationAttempt row; and
- normalises legacy shapes that would violate the NEW constraints (a
  ``succeeded`` attempt without staged metadata; a ``failed`` attempt with a
  blank error code or missing completion timestamp; duplicate in-progress
  attempts per design) into preserved, constraint-compatible terminal audit
  rows — so applying the constraints can never abort a deployment; and
- sanitises every legacy ``error_code`` against the frozen stable allowlist
  so no unvetted legacy text can surface through the public job API.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)

_APP = "designs"
_FROM = [(_APP, "0004_designversion_image_prompt_and_more")]
_TO = [(_APP, "0005_generationattempt_async_pipeline")]
_LATEST = [(_APP, "0007_generationattempt_text_submission_in_flight")]


class TestBackfill:
    def test_legacy_attempt_backfills_design_from_design_version(self):
        # Roll the schema back to the pre-Phase-10 state.
        executor = MigrationExecutor(connection)
        executor.migrate(_FROM)
        old_apps = executor.loader.project_state(_FROM).apps
        DesignSession = old_apps.get_model(_APP, "DesignSession")
        Design = old_apps.get_model(_APP, "Design")
        DesignVersion = old_apps.get_model(_APP, "DesignVersion")
        GenerationAttempt = old_apps.get_model(_APP, "GenerationAttempt")

        session = DesignSession.objects.create()
        design = Design.objects.create(design_session=session)
        version = DesignVersion.objects.create(design=design, version_number=1)
        attempt = GenerationAttempt.objects.create(design_version=version)
        attempt_id = attempt.pk

        try:
            # Apply the migration under test (runs the RunPython backfill).
            executor = MigrationExecutor(connection)
            executor.loader.build_graph()
            executor.migrate(_TO)
            new_apps = executor.loader.project_state(_TO).apps
            NewAttempt = new_apps.get_model(_APP, "GenerationAttempt")
            migrated = NewAttempt.objects.get(pk=attempt_id)
            assert migrated.design_id == design.pk
            # The legacy design_version link is preserved (now nullable).
            assert migrated.design_version_id == version.pk
        finally:
            # Leave the schema at the latest migration for subsequent tests.
            final_executor = MigrationExecutor(connection)
            final_executor.loader.build_graph()
            final_executor.migrate(_LATEST)

    def test_legacy_rows_violating_new_constraints_are_normalised_not_lost(self):
        """Legacy shapes the OLD schema permitted must never abort the
        migration: a 'succeeded' attempt without staged metadata and duplicate
        in-progress attempts per design are preserved as terminal audit rows."""
        executor = MigrationExecutor(connection)
        executor.migrate(_FROM)
        old_apps = executor.loader.project_state(_FROM).apps
        DesignSession = old_apps.get_model(_APP, "DesignSession")
        Design = old_apps.get_model(_APP, "Design")
        DesignVersion = old_apps.get_model(_APP, "DesignVersion")
        GenerationAttempt = old_apps.get_model(_APP, "GenerationAttempt")

        session = DesignSession.objects.create()
        design = Design.objects.create(design_session=session)
        version = DesignVersion.objects.create(design=design, version_number=1)
        # Legacy succeeded row (old schema had no staged fields at all).
        legacy_succeeded = GenerationAttempt.objects.create(
            design_version=version, status="succeeded"
        )
        # Legacy failed row with the old optional defaults: blank error_code
        # and no completed_at (both legal pre-Phase-10, both violating the new
        # failed-requirements constraint).
        legacy_failed = GenerationAttempt.objects.create(design_version=version, status="failed")
        assert legacy_failed.error_code == ""
        assert legacy_failed.completed_at is None
        # Legacy failed row with an UNVETTED code (the old schema imposed no
        # allowlist) — must never survive into the public job API.
        legacy_garbage = GenerationAttempt.objects.create(
            design_version=version,
            status="failed",
            error_code="Raw legacy provider text!",
            completed_at=timezone.now(),
        )
        # Two in-progress attempts for ONE design (legal pre-Phase-10). The
        # surviving newest one carries a stray code that must be cleared.
        older = GenerationAttempt.objects.create(design_version=version, status="queued")
        newer = GenerationAttempt.objects.create(
            design_version=version, status="running_text", error_code="stray-code"
        )

        try:
            executor = MigrationExecutor(connection)
            executor.loader.build_graph()
            executor.migrate(_TO)  # must NOT abort on the new constraints
            new_apps = executor.loader.project_state(_TO).apps
            NewAttempt = new_apps.get_model(_APP, "GenerationAttempt")
            # All five rows preserved.
            assert (
                NewAttempt.objects.filter(
                    pk__in=[
                        legacy_succeeded.pk,
                        legacy_failed.pk,
                        legacy_garbage.pk,
                        older.pk,
                        newer.pk,
                    ]
                ).count()
                == 5
            )
            # The impossible legacy 'succeeded' became a terminal audit row.
            normalised = NewAttempt.objects.get(pk=legacy_succeeded.pk)
            assert normalised.status == "failed"
            assert normalised.error_code == "internal_generation_error"
            assert normalised.completed_at is not None
            # The legacy failed row gained the required code and timestamp.
            backfilled_failed = NewAttempt.objects.get(pk=legacy_failed.pk)
            assert backfilled_failed.status == "failed"
            assert backfilled_failed.error_code == "internal_generation_error"
            assert backfilled_failed.completed_at is not None
            # The unvetted legacy code was sanitised to the generic code.
            sanitised = NewAttempt.objects.get(pk=legacy_garbage.pk)
            assert sanitised.error_code == "internal_generation_error"
            # The surviving in-progress row's stray code was cleared.
            survivor = NewAttempt.objects.get(pk=newer.pk)
            assert survivor.error_code == ""
            # Exactly one in-progress attempt survives (the newest).
            in_progress = NewAttempt.objects.filter(
                design=design.pk, status__in=["queued", "running_text", "running_image"]
            )
            assert in_progress.count() == 1
            assert in_progress.first().pk == newer.pk
            superseded = NewAttempt.objects.get(pk=older.pk)
            assert superseded.status == "failed"
            assert superseded.error_code == "internal_generation_error"
        finally:
            final_executor = MigrationExecutor(connection)
            final_executor.loader.build_graph()
            final_executor.migrate(_LATEST)
