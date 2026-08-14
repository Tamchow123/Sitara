"""Durable asynchronous refinement pipeline execution (Phase 14 Part C) —
resumability, seed reuse and duplicate-delivery guarantees. Zero network
calls; fakes injected for every provider/downloader/storage."""

import copy
import logging
import uuid
from unittest import mock

import pytest

from sitara.ai_gateway.structured_design import StructuredDesignResult
from sitara.designs.models import Design, DesignVersion, GenerationAttempt
from sitara.generation import errors
from sitara.generation.fixture_provider import FixtureStructuredDesignProvider
from sitara.generation.image_fixtures import (
    FakeImageProvider,
    InMemoryStorage,
    synthetic_webp_downloader,
)
from sitara.generation.pipeline import (
    PipelineConfig,
    enqueue_design_generation,
    enqueue_design_refinement,
    run_generation_attempt,
)
from sitara.generation.prompt_builder import PROMPT_BUILDER_VERSION
from sitara.generation.refinement import normalise_refinement_request

from .factory import make_complete_design
from .test_refinement_service import apply_allowed_edit

pytestmark = pytest.mark.django_db

_Status = GenerationAttempt.Status
_FAST = PipelineConfig(poll_interval_seconds=0.0, poll_max_attempts=10)
_AVAILABLE = "sitara.generation.pipeline.generation_is_available"


def _run(
    attempt,
    *,
    structured=None,
    image=None,
    downloader=synthetic_webp_downloader,
    storage=None,
    final=None,
    seed_factory=None,
    config=_FAST,
):
    return run_generation_attempt(
        attempt.id,
        structured_provider=structured or FixtureStructuredDesignProvider(),
        image_provider=image or FakeImageProvider(),
        image_downloader=downloader,
        storage=storage or InMemoryStorage(),
        final_storage=final,
        seed_factory=seed_factory or (lambda: 0),
        config=config,
    )


def _request(change_type="colour_story", note=""):
    return normalise_refinement_request(
        {"schema_version": 1, "change_type": change_type, "note": note}
    )


def _as_result(payload: dict) -> StructuredDesignResult:
    return StructuredDesignResult(
        payload=payload,
        provider="fake",
        model="fake-model",
        input_tokens=100,
        output_tokens=200,
        stop_reason="end_turn",
    )


def _refined_result(spec_payload) -> StructuredDesignResult:
    """A legal ``colour_story`` refinement — the default category these tests
    enqueue. Use ``_as_result(apply_allowed_edit(spec, change_type))`` when the
    category matters.

    Moves the canonical colour selection, not just the palette prose. A
    narrative-only payload is now refused as no change at all, because the
    prompt builder renders none of it — so a fixture that changed only prose
    would make every pipeline test here fail for a reason that has nothing to
    do with what it is testing."""
    return _as_result(apply_allowed_edit(spec_payload, "colour_story"))


def _generated_design(*, storage=None, image=None, seed_factory=None):
    """Run the REAL initial pipeline (fakes only) to a succeeded version 1,
    so refinement seed-reuse has genuine prior-attempt data to find."""
    design = make_complete_design()
    with mock.patch(_AVAILABLE, return_value=True):
        attempt, _created = enqueue_design_generation(
            design, idempotency_key=uuid.uuid4(), enqueue_task=lambda a: None
        )
    result = _run(attempt, storage=storage, image=image, seed_factory=seed_factory)
    assert result.status == _Status.SUCCEEDED
    version = DesignVersion.objects.get(design=design, version_number=1)
    return design, version, result


def _enqueue_refinement(design, source_version, *, change_type="colour_story", note=""):
    with mock.patch(_AVAILABLE, return_value=True):
        attempt, _created = enqueue_design_refinement(
            design,
            source_version_id=source_version.pk,
            refinement_request=_request(change_type, note),
            idempotency_key=uuid.uuid4(),
            enqueue_task=lambda a: None,
        )
    return attempt


class TestHappyPath:
    def test_full_refinement_pipeline_reaches_succeeded(self):
        storage = InMemoryStorage()
        design, v1, _initial_attempt = _generated_design(storage=storage)
        attempt = _enqueue_refinement(design, v1)
        spec_payload = v1.design_spec
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = _refined_result(spec_payload)

        result = _run(attempt, structured=refined_provider, storage=storage)

        assert result.status == _Status.SUCCEEDED
        assert result.error_code == ""
        design.refresh_from_db()
        assert design.status == Design.Status.GENERATED
        version = DesignVersion.objects.get(pk=result.design_version_id)
        assert version.version_number == 2
        assert version.parent_version_id == v1.pk
        assert version.design_spec_template_version == "refinement-4.0.0"
        # The same deterministic prompt builder ran against the refined spec.
        assert version.prompt_builder_version == PROMPT_BUILDER_VERSION
        assert version.image_prompt != ""
        assert version.has_permanent_image
        # Version 1 remains untouched and readable.
        v1.refresh_from_db()
        assert v1.design_spec == spec_payload
        assert v1.has_permanent_image

    def test_refined_version_uses_current_prompt_builder_version(self):
        # Refinement introduces no second prompt-builder path: a refined version
        # carries the same current PROMPT_BUILDER_VERSION as an initial version.
        design, v1, _initial = _generated_design()
        attempt = _enqueue_refinement(design, v1)
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = _refined_result(v1.design_spec)
        result = _run(attempt, structured=refined_provider)
        version = DesignVersion.objects.get(pk=result.design_version_id)
        assert version.prompt_builder_version == PROMPT_BUILDER_VERSION


class TestSeedReuse:
    def test_seed_copied_from_succeeded_initial_attempt(self):
        design, v1, initial_result = _generated_design(seed_factory=lambda: 777)
        assert initial_result.image_seed == 777
        attempt = _enqueue_refinement(design, v1)
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = _refined_result(v1.design_spec)
        image_provider = FakeImageProvider()

        result = _run(attempt, structured=refined_provider, image=image_provider)

        assert result.status == _Status.SUCCEEDED
        result.refresh_from_db()
        assert result.image_seed == 777
        assert result.seed_reused is True
        assert image_provider.last_request.seed == 777

    def test_new_seed_generated_when_source_seed_unavailable(self):
        # Bare source version (no succeeded initial attempt on record) still
        # passes validate_source_version once given complete provenance —
        # simulate by deleting the initial attempt's seed after the fact.
        design, v1, initial_result = _generated_design(seed_factory=lambda: 42)
        GenerationAttempt.objects.filter(pk=initial_result.pk).update(image_seed=None)
        attempt = _enqueue_refinement(design, v1)
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = _refined_result(v1.design_spec)
        image_provider = FakeImageProvider()

        result = _run(
            attempt, structured=refined_provider, image=image_provider, seed_factory=lambda: 999
        )

        assert result.status == _Status.SUCCEEDED
        result.refresh_from_db()
        assert result.image_seed == 999
        assert result.seed_reused is False

    def test_redelivery_reuses_the_already_persisted_seed(self):
        design, v1, _initial = _generated_design(seed_factory=lambda: 5)
        attempt = _enqueue_refinement(design, v1)
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = _refined_result(v1.design_spec)
        storage = InMemoryStorage()
        image_provider = FakeImageProvider()

        first = _run(attempt, structured=refined_provider, storage=storage, image=image_provider)
        assert first.status == _Status.SUCCEEDED
        persisted_seed = first.image_seed

        # A redelivery of an already-succeeded attempt is a terminal no-op —
        # confirm the persisted seed is never altered.
        second = _run(
            attempt,
            structured=refined_provider,
            storage=storage,
            image=image_provider,
            seed_factory=lambda: 123456,
        )
        assert second.image_seed == persisted_seed
        assert second.seed_reused == first.seed_reused

    def test_seed_never_exposed_in_public_job_payload(self):
        from sitara.designs.jobs import public_job_payload

        design, v1, _initial = _generated_design(seed_factory=lambda: 314159)
        attempt = _enqueue_refinement(design, v1)
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = _refined_result(v1.design_spec)
        result = _run(attempt, structured=refined_provider)
        payload = public_job_payload(result)["job"]
        assert "seed" not in payload
        assert "seed_reused" not in payload
        assert "314159" not in str(payload)


class TestResumeAndDuplicateDelivery:
    def test_linked_child_skips_the_text_provider_on_redelivery(self):
        design, v1, _initial = _generated_design()
        attempt = _enqueue_refinement(design, v1)
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = _refined_result(v1.design_spec)
        storage = InMemoryStorage()

        first = _run(attempt, structured=refined_provider, storage=storage)
        assert first.status == _Status.SUCCEEDED
        assert refined_provider.generate.call_count == 1

        # A redelivery must never call the text provider again — the version
        # is already linked.
        second_provider = mock.Mock()
        second = _run(attempt, structured=second_provider, storage=storage)
        assert second.status == _Status.SUCCEEDED
        second_provider.generate.assert_not_called()

    def test_accepted_prediction_is_never_resubmitted(self):
        design, v1, _initial = _generated_design()
        attempt = _enqueue_refinement(design, v1)
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = _refined_result(v1.design_spec)
        image_provider = FakeImageProvider()
        storage = InMemoryStorage()

        result = _run(attempt, structured=refined_provider, image=image_provider, storage=storage)
        assert result.status == _Status.SUCCEEDED
        assert image_provider.create_calls == 1

    def test_no_version_3_is_ever_created(self):
        design, v1, _initial = _generated_design()
        attempt = _enqueue_refinement(design, v1)
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = _refined_result(v1.design_spec)
        storage = InMemoryStorage()

        first = _run(attempt, structured=refined_provider, storage=storage)
        assert first.status == _Status.SUCCEEDED

        # Redeliver the SAME attempt repeatedly; no new version ever appears.
        for _ in range(3):
            _run(attempt, structured=mock.Mock(), storage=storage)
        assert DesignVersion.objects.filter(design=design).count() == 2
        assert not DesignVersion.objects.filter(design=design, version_number=3).exists()

    def test_ambiguous_text_submission_blocks_unsafe_retry(self):
        design, v1, _initial = _generated_design()
        attempt = _enqueue_refinement(design, v1)
        GenerationAttempt.objects.filter(pk=attempt.pk).update(text_submission_in_flight=True)
        attempt.refresh_from_db()
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = _refined_result(v1.design_spec)

        result = _run(attempt, structured=refined_provider)

        assert result.status == _Status.FAILED
        assert result.error_code == errors.STRUCTURED_SUBMISSION_AMBIGUOUS
        refined_provider.generate.assert_not_called()
        design.refresh_from_db()
        assert design.status == Design.Status.GENERATION_FAILED
        # Version 1 is never touched.
        v1.refresh_from_db()
        assert v1.design_spec is not None


class TestFailurePreservesVersionOne:
    def test_refinement_generation_failure_preserves_version_1(self):
        design, v1, _initial = _generated_design()
        attempt = _enqueue_refinement(design, v1)
        bad_payload = copy.deepcopy(v1.design_spec)
        bad_payload["embellishment_plan"]["density"] = "An unrelated change."
        bad_result = StructuredDesignResult(
            payload=bad_payload,
            provider="fake",
            model="fake-model",
            input_tokens=10,
            output_tokens=10,
            stop_reason="end_turn",
        )
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = bad_result

        result = _run(attempt, structured=refined_provider)

        assert result.status == _Status.FAILED
        assert result.error_code == errors.REFINEMENT_GENERATION_FAILED
        design.refresh_from_db()
        assert design.status == Design.Status.GENERATION_FAILED
        v1.refresh_from_db()
        assert v1.design_spec is not None
        assert v1.has_permanent_image
        assert not DesignVersion.objects.filter(design=design, version_number=2).exists()

    def test_no_change_produced_maps_to_refinement_no_change(self):
        design, v1, _initial = _generated_design()
        attempt = _enqueue_refinement(design, v1)
        no_op_result = StructuredDesignResult(
            payload=copy.deepcopy(v1.design_spec),
            provider="fake",
            model="fake-model",
            input_tokens=10,
            output_tokens=10,
            stop_reason="end_turn",
        )
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = no_op_result

        result = _run(attempt, structured=refined_provider)

        assert result.status == _Status.FAILED
        assert result.error_code == errors.REFINEMENT_NO_CHANGE
        v1.refresh_from_db()
        assert v1.design_spec is not None

    def test_terminalisation_logs_the_code_the_customer_was_told(self, caplog):
        # `_finalise_failure` is the one place every terminal failure passes
        # through, and it used to say nothing. Each stage logs why it gave up,
        # but nothing recorded which of the eleven-odd codes actually reached
        # the customer, so an incident meant guessing from whichever stage line
        # happened to be last — and the codes that share a stage are precisely
        # the ones worth telling apart.
        design, v1, _initial = _generated_design()
        attempt = _enqueue_refinement(design, v1)
        no_op_result = StructuredDesignResult(
            payload=copy.deepcopy(v1.design_spec),
            provider="fake",
            model="fake-model",
            input_tokens=10,
            output_tokens=10,
            stop_reason="end_turn",
        )
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = no_op_result

        with caplog.at_level(logging.DEBUG):
            result = _run(attempt, structured=refined_provider)

        assert result.error_code == errors.REFINEMENT_NO_CHANGE
        terminal = [m for m in (r.getMessage() for r in caplog.records) if "attempt failed" in m]
        assert len(terminal) == 1, terminal
        assert f"code={errors.REFINEMENT_NO_CHANGE}" in terminal[0]
        assert f"attempt={attempt.id}" in terminal[0]
        assert "kind=refinement" in terminal[0]
        assert "demo=False" in terminal[0]


class TestNoImageToImageInput:
    """Refinement is always a FRESH text-to-image generation. ADR 0019 lets the
    user's chosen INSPIRATIONS reach the image provider; it changes nothing
    about the design's own previously generated image, which must never be sent
    back as an input under any name."""

    def test_a_refinement_with_no_inspiration_sends_no_reference_at_all(self):
        design, v1, _initial = _generated_design()
        attempt = _enqueue_refinement(design, v1)
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = _refined_result(v1.design_spec)
        image_provider = FakeImageProvider()

        result = _run(attempt, structured=refined_provider, image=image_provider)

        assert result.status == _Status.SUCCEEDED
        request = image_provider.last_request
        assert request.reference_image_urls == ()
        assert v1.image_storage_key not in request.prompt

    def test_the_source_versions_own_image_is_never_sent_as_a_reference(
        self, settings, inmemory_storage
    ):
        # The dangerous confusion ADR 0019 could invite: signing the design's
        # OWN generated image alongside the inspirations would silently turn
        # refinement into image-to-image editing, which CLAUDE.md §7 forbids.
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        settings.S3_SIGNED_URL_ENDPOINT_URL = "https://storage.example.test"
        storage = InMemoryStorage()
        design, v1, _initial = _generated_design(storage=storage)
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)

        # NOT colour_story: that category is the one ADR 0028 suppresses
        # references for, and this test is about what happens when they ARE
        # sent. The refined payload must therefore be built for THIS category —
        # `_refined_result` moves the canonical colour selection, which
        # `fabric_and_texture` may not touch.
        attempt = _enqueue_refinement(design, v1, change_type="fabric_and_texture")
        refined_provider = mock.Mock()
        refined_provider.generate.return_value = _as_result(
            apply_allowed_edit(v1.design_spec, "fabric_and_texture")
        )
        image_provider = FakeImageProvider()

        result = _run(attempt, structured=refined_provider, image=image_provider, storage=storage)

        assert result.status == _Status.SUCCEEDED
        urls = image_provider.last_request.reference_image_urls
        # Exactly the inspiration — one reference, not two.
        assert len(urls) == 1
        assert asset.image_storage_key in urls[0]
        v1.refresh_from_db()
        assert v1.image_storage_key
        for url in urls:
            assert v1.image_storage_key not in url
            assert v1.thumbnail_storage_key not in url


class TestReferencesPerRefinementCategory:
    """ADR 0028, §7: a colour refinement is sent NO reference images, and every
    other category keeps exactly the set its source attempt was sent.

    A reference photograph is a far stronger colour signal than a text clause,
    so "make it green" against a red reference kept losing to the reference —
    the reported defect. This is the narrowest change that addresses it, and it
    is the one place in this phase that touches the live provider path, so both
    halves are pinned by asserting what ``ImageGenerationRequest`` was actually
    constructed with rather than that some intermediate function was called.

    Every test here requests the ``inmemory_storage`` fixture for its SETTINGS
    side-effect rather than for a value: it points the ``default`` storage alias
    at in-memory storage, which is what lets ``make_eligible_asset`` ingest and
    what lets a reference URL be signed at all — without it the conftest network
    guard fires. The separate ``InMemoryStorage()`` each test builds is the
    pipeline's own injected staging storage, a different thing entirely."""

    @staticmethod
    def _design_with_one_reference(settings, storage):
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        settings.S3_SIGNED_URL_ENDPOINT_URL = "https://storage.example.test"
        image_provider = FakeImageProvider()
        design, v1, _initial = _generated_design(storage=storage, image=image_provider)
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        return design, v1, asset

    def _refine(self, design, v1, storage, change_type):
        attempt = _enqueue_refinement(design, v1, change_type=change_type)
        refined_provider = mock.Mock()
        # Each category gets an edit inside its OWN allowlist, reusing the
        # service tests' single table rather than a second copy of that policy.
        refined_provider.generate.return_value = _as_result(
            apply_allowed_edit(v1.design_spec, change_type)
        )
        image_provider = FakeImageProvider()
        result = _run(attempt, structured=refined_provider, image=image_provider, storage=storage)
        assert result.status == _Status.SUCCEEDED
        return image_provider.last_request

    def test_a_colour_refinement_is_handed_no_references_at_all(self, settings, inmemory_storage):
        storage = InMemoryStorage()
        design, v1, asset = self._design_with_one_reference(settings, storage)

        request = self._refine(design, v1, storage, "colour_story")

        # Not "fewer", not "reordered" — none. And the suppression is about the
        # provider call only: the design's own reference selection is untouched,
        # so a later category still gets it.
        assert request.reference_image_urls == ()
        assert asset.image_storage_key not in request.prompt
        assert design.inspiration_selections.count() == 1

    @pytest.mark.parametrize(
        "change_type",
        ["fabric_and_texture", "embellishment", "sleeves_and_coverage", "silhouette_detail"],
    )
    def test_every_other_category_still_gets_its_references(
        self, settings, inmemory_storage, change_type
    ):
        storage = InMemoryStorage()
        design, v1, asset = self._design_with_one_reference(settings, storage)

        request = self._refine(design, v1, storage, change_type)

        # Compared by storage key, not by URL string: a signed URL carries a
        # fresh expiry and signature every time it is minted, so two mintings of
        # the SAME object are never byte-equal. The key is what identifies the
        # reference; the signature is the part that must not be reused.
        assert len(request.reference_image_urls) == 1
        assert asset.image_storage_key in request.reference_image_urls[0]

    def test_the_suppression_never_applies_to_an_initial_generation(
        self, settings, inmemory_storage
    ):
        # The branch reads the attempt's own refinement request, and an initial
        # attempt has none. Asserted rather than assumed, because a change that
        # made this branch fire on `None` would silently strip references from
        # every first generation in the product.
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        settings.S3_SIGNED_URL_ENDPOINT_URL = "https://storage.example.test"
        storage = InMemoryStorage()
        design = make_complete_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        image_provider = FakeImageProvider()
        with mock.patch(_AVAILABLE, return_value=True):
            attempt, _created = enqueue_design_generation(
                design, idempotency_key=uuid.uuid4(), enqueue_task=lambda a: None
            )

        result = _run(attempt, storage=storage, image=image_provider)

        assert result.status == _Status.SUCCEEDED
        urls = image_provider.last_request.reference_image_urls
        assert len(urls) == 1
        assert asset.image_storage_key in urls[0]
