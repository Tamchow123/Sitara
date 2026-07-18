"""Migration 0005 data-backfill test (Phase 10).

Proves that a legacy GenerationAttempt row (created before Phase 10, carrying
only a required ``design_version``) has its new required ``design`` FK correctly
backfilled from ``design_version.design_id`` when migration 0005 runs. Uses the
real migration graph via MigrationExecutor so the RunPython step is exercised
against the historical model state, not the current ORM.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

_APP = "designs"
_FROM = [(_APP, "0004_designversion_image_prompt_and_more")]
_TO = [(_APP, "0005_generationattempt_async_pipeline")]


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
            final_executor.migrate(_TO)
