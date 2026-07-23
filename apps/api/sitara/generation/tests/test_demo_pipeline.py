"""Deterministic demo-pipeline execution tests (Phase 15 Part C, spec §32).

Exercises the pipeline's NEW demo branch points directly against the real
local adapters (:mod:`sitara.generation.demo`) — never the live Anthropic or
Replicate SDKs, never a real network call — proving the demo journey reuses
the exact same resumable state machine, storage staging, canonical ingest
and terminal-status guarantees as the live path, differing only in provider/
downloader resolution."""

import hashlib
import threading
from unittest import mock

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command

from sitara.designs.models import Design, DesignVersion, GenerationAttempt
from sitara.generation import errors
from sitara.generation.demo.config import ACTIVE_MANIFEST_KEY
from sitara.generation.demo.selector import DemoAssetSelection
from sitara.generation.demo.storage import demo_asset_storage
from sitara.generation.fixture_provider import FixtureStructuredDesignProvider
from sitara.generation.pipeline import (
    PipelineConfig,
    _attempt_lock_keys,
    _demo_seed_factory,
    _select_demo_asset_for_attempt,
    run_generation_attempt,
)

from .factory import make_complete_design

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("inmemory_storage")]

_Status = GenerationAttempt.Status
_FAST = PipelineConfig(poll_interval_seconds=0.0, poll_max_attempts=10)


def _install_synthetic_pack():
    call_command("install_demo_asset_pack", "--dev-synthetic")


def _queued_demo_attempt(design) -> GenerationAttempt:
    design.status = Design.Status.GENERATING
    design.save(update_fields=["status"])
    return GenerationAttempt.objects.create(design=design, status=_Status.QUEUED, is_demo=True)


class TestFullDemoPipeline:
    def test_full_demo_pipeline_reaches_succeeded_via_local_adapters(self):
        # No structured/image provider, downloader, storage or seed factory is
        # injected: every resolution happens inside the pipeline's own demo
        # branches (attempt.is_demo == True), proving the real local adapters
        # work end-to-end — the strongest possible proof of zero network use
        # given the module-level ``no_network`` socket guard is active for
        # every generation test.
        _install_synthetic_pack()
        design = make_complete_design()
        attempt = _queued_demo_attempt(design)
        result = run_generation_attempt(attempt.id, config=_FAST)
        assert result.status == _Status.SUCCEEDED
        assert result.error_code == ""
        assert result.is_demo is True
        design.refresh_from_db()
        assert design.status == Design.Status.GENERATED

        version = DesignVersion.objects.get(pk=result.design_version_id)
        assert version.is_demo is True
        assert version.design_spec_provider == "demo"
        assert version.design_spec_model == "demo-spec-2.0.0"
        assert version.has_permanent_image
        # The final image lives under the normal design_images key, never the
        # private demo-source namespace.
        assert version.image_storage_key == (
            f"design-images/{design.id}/{version.id}/original.webp"
        )
        assert not version.image_storage_key.startswith("demo-assets/")

        # The demo source asset flowed through the normal raw-staging key.
        assert result.staged_image_storage_key.startswith(f"generation-staging/{attempt.id}/raw.")
        # Selection provenance was persisted privately on the attempt only.
        assert result.demo_selection is not None
        assert result.demo_selection["asset_id"]
        assert result.demo_selection["manifest_hash"]
        assert result.demo_selection["selector_version"]

    def test_demo_pipeline_never_constructs_a_live_sdk_client(self):
        _install_synthetic_pack()
        design = make_complete_design()
        attempt = _queued_demo_attempt(design)

        def _boom(*args, **kwargs):
            raise AssertionError("must never construct a live provider SDK client in demo mode")

        with (
            mock.patch("anthropic.Anthropic", side_effect=_boom),
            mock.patch("replicate.client.Client", side_effect=_boom),
        ):
            result = run_generation_attempt(attempt.id, config=_FAST)
        assert result.status == _Status.SUCCEEDED

    def test_demo_pipeline_never_resolves_a_live_provider_factory(self):
        _install_synthetic_pack()
        design = make_complete_design()
        attempt = _queued_demo_attempt(design)

        def _boom(*args, **kwargs):
            raise AssertionError("must never resolve a live provider factory in demo mode")

        with (
            mock.patch(
                "sitara.ai_gateway.policy.get_structured_design_generation_provider",
                side_effect=_boom,
            ),
            mock.patch("sitara.generation.pipeline._live_image_provider", side_effect=_boom),
            mock.patch("sitara.generation.pipeline._live_image_downloader", side_effect=_boom),
        ):
            result = run_generation_attempt(attempt.id, config=_FAST)
        assert result.status == _Status.SUCCEEDED


class TestDemoSeedDeterminism:
    def test_seed_depends_only_on_prompt_manifest_hash_and_selector_version(self):
        class _Version:
            def __init__(self, prompt):
                self.image_prompt = prompt

        selection = DemoAssetSelection(
            asset_id="lehenga-baraat-dev-001",
            manifest_hash="a" * 64,
            manifest_schema_version=1,
            selector_version="1.0.0",
        )
        seed_a = _demo_seed_factory(_Version("A deterministic prompt."), selection)()
        seed_b = _demo_seed_factory(_Version("A deterministic prompt."), selection)()
        assert seed_a == seed_b
        assert seed_a >= 0

        # A different asset_id with the SAME prompt/manifest_hash/selector
        # version yields the SAME seed — asset_id is deliberately excluded
        # from the payload (spec §25).
        same_hash_different_asset = DemoAssetSelection(
            asset_id="saree-nikah-dev-002",
            manifest_hash="a" * 64,
            manifest_schema_version=1,
            selector_version="1.0.0",
        )
        assert (
            _demo_seed_factory(_Version("A deterministic prompt."), same_hash_different_asset)()
            == seed_a
        )

        # A different prompt or manifest hash changes the seed.
        assert _demo_seed_factory(_Version("A different prompt."), selection)() != seed_a
        different_hash = DemoAssetSelection(
            asset_id="lehenga-baraat-dev-001",
            manifest_hash="b" * 64,
            manifest_schema_version=1,
            selector_version="1.0.0",
        )
        assert _demo_seed_factory(_Version("A deterministic prompt."), different_hash)() != seed_a

    def test_seed_matches_the_documented_recipe(self):
        class _Version:
            image_prompt = "A deterministic prompt."

        selection = DemoAssetSelection(
            asset_id="ignored-for-the-seed",
            manifest_hash="c" * 64,
            manifest_schema_version=1,
            selector_version="1.0.0",
        )
        expected_payload = (
            f"{_Version.image_prompt}:{selection.manifest_hash}:{selection.selector_version}"
        )
        expected = int(hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()[:8], 16)
        assert _demo_seed_factory(_Version(), selection)() == expected


class TestDemoSelectionPersistenceAndReuse:
    def test_selection_is_persisted_and_reused_without_reselecting(self):
        _install_synthetic_pack()
        design = make_complete_design()
        attempt = _queued_demo_attempt(design)
        result = run_generation_attempt(attempt.id, config=_FAST)
        assert result.status == _Status.SUCCEEDED
        first_selection = result.demo_selection

        version = DesignVersion.objects.get(pk=result.design_version_id)
        attempt.refresh_from_db()
        with mock.patch("sitara.generation.pipeline.select_demo_asset") as spy:
            selection, _manifest = _select_demo_asset_for_attempt(attempt, version)
        spy.assert_not_called()
        assert selection.asset_id == first_selection["asset_id"]
        assert selection.manifest_hash == first_selection["manifest_hash"]
        assert selection.selector_version == first_selection["selector_version"]


class TestDemoAssetsUnavailableDuringExecution:
    def test_no_active_pack_fails_the_image_stage_with_the_controlled_code(self):
        # No pack installed at all: the text/prompt stages succeed (they need
        # no manifest); the image stage's selection step is the first thing
        # that requires an active manifest.
        design = make_complete_design()
        attempt = _queued_demo_attempt(design)
        result = run_generation_attempt(attempt.id, config=_FAST)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.DEMO_ASSETS_UNAVAILABLE
        design.refresh_from_db()
        assert design.status == Design.Status.GENERATION_FAILED

    def test_corrupt_active_manifest_fails_the_image_stage_with_the_controlled_code(self):
        storage = demo_asset_storage()
        storage.save(ACTIVE_MANIFEST_KEY, ContentFile(b"not valid json"))
        design = make_complete_design()
        attempt = _queued_demo_attempt(design)
        result = run_generation_attempt(attempt.id, config=_FAST)
        assert result.status == _Status.FAILED
        assert result.error_code == errors.DEMO_ASSETS_UNAVAILABLE

    def test_unavailable_error_code_is_in_the_allowlist(self):
        assert errors.is_valid_error_code(errors.DEMO_ASSETS_UNAVAILABLE)


class TestDemoSourceKeyPrivacy:
    def test_private_demo_storage_key_never_appears_on_the_attempt_or_version(self):
        _install_synthetic_pack()
        design = make_complete_design()
        attempt = _queued_demo_attempt(design)
        result = run_generation_attempt(attempt.id, config=_FAST)
        assert result.status == _Status.SUCCEEDED
        version = DesignVersion.objects.get(pk=result.design_version_id)

        # The private demo-assets/ storage namespace must never leak into any
        # persisted, externally-reachable field.
        for value in (
            result.staged_image_storage_key,
            version.image_storage_key,
            version.thumbnail_storage_key,
            version.image_prompt,
        ):
            assert "demo-assets/" not in value

        from sitara.designs.jobs import public_job_payload
        from sitara.designs.result import (
            design_result_payload,
            load_inspiration_acknowledgements,
            load_lineage,
            load_validated_design_spec,
        )

        job_payload = public_job_payload(result)["job"]
        assert "demo-assets/" not in str(job_payload)
        assert "demo-asset://" not in str(job_payload)

        spec = load_validated_design_spec(version)
        acknowledgements = load_inspiration_acknowledgements(version)
        lineage = load_lineage(version)
        result_payload = design_result_payload(version, spec, acknowledgements, lineage)
        assert "demo-assets/" not in str(result_payload)
        assert "demo-asset://" not in str(result_payload)


class TestModeFrozenAtExecution:
    def test_execution_ignores_a_settings_change_after_the_attempt_was_queued(self, settings):
        # The attempt was frozen as demo BEFORE this test runs (is_demo=True
        # set directly, matching what enqueue would have persisted). Flipping
        # every live gate closed here must have NO effect on execution — it
        # must only ever consult the frozen attempt.is_demo flag.
        _install_synthetic_pack()
        design = make_complete_design()
        attempt = _queued_demo_attempt(design)
        settings.DEMO_MODE = False
        settings.ALLOW_PAID_AI_CALLS = False
        settings.LIVE_GENERATION_ENABLED = False
        result = run_generation_attempt(attempt.id, config=_FAST)
        assert result.status == _Status.SUCCEEDED
        assert result.is_demo is True


class TestDemoResumeAndDuplicateDelivery:
    def test_terminal_demo_attempt_redelivery_is_idempotent(self):
        _install_synthetic_pack()
        design = make_complete_design()
        attempt = _queued_demo_attempt(design)
        first = run_generation_attempt(attempt.id, config=_FAST)
        assert first.status == _Status.SUCCEEDED
        completed = first.completed_at
        again = run_generation_attempt(attempt.id, config=_FAST)
        assert again.status == _Status.SUCCEEDED
        assert again.completed_at == completed
        assert DesignVersion.objects.filter(design=design).count() == 1

    @pytest.mark.django_db(transaction=True)
    def test_duplicate_concurrent_delivery_performs_no_demo_work(self):
        # Mirrors test_pipeline.py's generic advisory-lock proof for a demo
        # attempt: a second (duplicate) delivery whose lock is already held
        # performs NO work and returns None — never a second selection,
        # provider call or DesignVersion.
        design = make_complete_design()
        attempt = _queued_demo_attempt(design)
        key_high, key_low = _attempt_lock_keys(attempt.id)

        holding = threading.Event()
        release = threading.Event()

        def holder():
            from django.db import connection as thread_connection

            try:
                with thread_connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_lock(%s, %s)", [key_high, key_low])
                    holding.set()
                    release.wait(timeout=10)
                    cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [key_high, key_low])
            finally:
                thread_connection.close()

        thread = threading.Thread(target=holder)
        thread.start()
        try:
            assert holding.wait(timeout=10)
            result = run_generation_attempt(attempt.id, config=_FAST)
            assert result is None
            assert DesignVersion.objects.filter(design=design).count() == 0
            attempt.refresh_from_db()
            assert attempt.status == _Status.QUEUED
        finally:
            release.set()
            thread.join(timeout=10)


class TestFixturesRemainDistinctFromDemo:
    def test_test_fixture_provider_and_demo_provider_never_share_identity(self):
        # Spec §32 "test fixtures remain distinct from public demo": the
        # zero-network fixture provider used ONLY by the test suite
        # (sitara.generation.fixture_provider) is an entirely separate module
        # from the real deterministic demo engine
        # (sitara.generation.demo.design_spec_engine) — different provider
        # name, different template version, never interchangeable.
        from sitara.generation.demo.provider import DEMO_SPEC_MODEL

        fixture_provider = FixtureStructuredDesignProvider()
        assert fixture_provider.name != "demo"
        assert fixture_provider.name != DEMO_SPEC_MODEL
        assert DEMO_SPEC_MODEL.startswith("demo-")
