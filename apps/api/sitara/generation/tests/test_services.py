"""DesignSpec generation orchestration: gates, retry, persistence, locking."""

import pytest

from sitara.ai_gateway.structured_design import StructuredDesignProviderError
from sitara.designs.models import Design, DesignVersion
from sitara.generation.context import DesignNotReady, build_generation_context
from sitara.generation.design_spec import (
    DESIGN_SPEC_SCHEMA_VERSION,
    SPEC_TEMPLATE_VERSION,
    DesignSpec,
)
from sitara.generation.input_safety import UnsafeUserTextError
from sitara.generation.services import (
    DesignChangedDuringGeneration,
    GenerationFailed,
    GenerationRefused,
    generate_design_spec_for_design,
)

from . import fakes
from .factory import COMPLETE_ANSWERS, make_active_v1, make_complete_design

pytestmark = pytest.mark.django_db


def _source_selections(design) -> dict:
    return build_generation_context(design).source_selections


class TestHappyPath:
    def test_one_valid_response_creates_one_version(self):
        design = make_complete_design()
        provider = fakes.SequenceProvider([fakes.valid_result(_source_selections(design))])
        version = generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 1
        assert DesignVersion.objects.filter(design=design).count() == 1
        assert version.version_number == 1

    def test_provenance_is_recorded_correctly(self):
        design = make_complete_design()
        provider = fakes.SequenceProvider([fakes.valid_result(_source_selections(design))])
        version = generate_design_spec_for_design(design, provider=provider)
        version.refresh_from_db()
        assert version.design_spec_schema_version == DESIGN_SPEC_SCHEMA_VERSION
        assert version.design_spec_template_version == SPEC_TEMPLATE_VERSION
        assert version.design_spec_provider == "fake"
        assert version.design_spec_model == "fake-model"
        assert version.design_spec_input_tokens == 1234
        assert version.design_spec_output_tokens == 567
        assert version.design_spec_generated_at is not None

    def test_persisted_json_equals_model_dump(self):
        design = make_complete_design()
        provider = fakes.SequenceProvider([fakes.valid_result(_source_selections(design))])
        version = generate_design_spec_for_design(design, provider=provider)
        version.refresh_from_db()
        spec = DesignSpec.model_validate(version.design_spec)
        assert version.design_spec == spec.model_dump(mode="json")

    def test_no_raw_prompt_or_response_is_stored(self):
        design = make_complete_design()
        provider = fakes.SequenceProvider([fakes.valid_result(_source_selections(design))])
        version = generate_design_spec_for_design(design, provider=provider)
        version.refresh_from_db()
        blob = str(version.design_spec)
        for marker in ("You are helping Sitara", "UNTRUSTED", "system prompt", "fake-model"):
            assert marker not in blob
        # design_spec holds only the validated DesignSpec keys.
        assert set(version.design_spec) == set(DesignSpec.model_fields)


class TestRetryPolicy:
    def test_first_invalid_then_valid_makes_two_calls_and_persists_once(self):
        design = make_complete_design()
        ss = _source_selections(design)
        provider = fakes.SequenceProvider([fakes.malformed_result(), fakes.valid_result(ss)])
        generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 2
        assert DesignVersion.objects.filter(design=design).count() == 1

    def test_two_invalid_responses_make_two_calls_and_persist_nothing(self):
        design = make_complete_design()
        provider = fakes.SequenceProvider([fakes.malformed_result(), fakes.malformed_result()])
        with pytest.raises(GenerationFailed) as excinfo:
            generate_design_spec_for_design(design, provider=provider)
        assert excinfo.value.attempts == 2
        assert provider.calls == 2
        assert DesignVersion.objects.filter(design=design).count() == 0

    def test_source_selection_mismatch_retries(self):
        design = make_complete_design()
        ss = _source_selections(design)
        provider = fakes.SequenceProvider(
            [fakes.source_mismatch_result(ss), fakes.valid_result(ss)]
        )
        generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 2
        assert DesignVersion.objects.filter(design=design).count() == 1

    def test_blocked_designer_reference_retries(self):
        design = make_complete_design()
        ss = _source_selections(design)
        provider = fakes.SequenceProvider(
            [fakes.blocked_designer_result(ss), fakes.valid_result(ss)]
        )
        generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 2
        assert DesignVersion.objects.filter(design=design).count() == 1

    def test_two_mismatches_persist_nothing(self):
        design = make_complete_design()
        ss = _source_selections(design)
        provider = fakes.SequenceProvider(
            [fakes.source_mismatch_result(ss), fakes.source_mismatch_result(ss)]
        )
        with pytest.raises(GenerationFailed):
            generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 2
        assert DesignVersion.objects.filter(design=design).count() == 0


class TestNonRetryableOutcomes:
    def test_refusal_persists_nothing_and_does_not_retry(self):
        design = make_complete_design()
        provider = fakes.SequenceProvider([fakes.refusal_result()])
        with pytest.raises(GenerationRefused):
            generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 1
        assert DesignVersion.objects.filter(design=design).count() == 0

    def test_provider_transport_error_makes_one_call_and_persists_nothing(self):
        design = make_complete_design()
        provider = fakes.RaisingProvider(StructuredDesignProviderError("timeout"))
        with pytest.raises(StructuredDesignProviderError):
            generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 1
        assert DesignVersion.objects.filter(design=design).count() == 0


class TestPreSpendGates:
    def test_incomplete_design_blocks_before_provider(self):
        design = make_complete_design(answers={"garment_type": "lehenga"})  # missing required
        provider = fakes.SequenceProvider([])
        with pytest.raises(DesignNotReady) as excinfo:
            generate_design_spec_for_design(design, provider=provider)
        assert excinfo.value.code == "incomplete"
        assert provider.calls == 0

    def test_existing_version_blocks_before_provider(self):
        design = make_complete_design()
        ss = _source_selections(design)
        generate_design_spec_for_design(
            design, provider=fakes.SequenceProvider([fakes.valid_result(ss)])
        )
        blocked = fakes.SequenceProvider([fakes.valid_result(ss)])
        with pytest.raises(DesignNotReady) as excinfo:
            generate_design_spec_for_design(design, provider=blocked)
        assert excinfo.value.code == "already_generated"
        assert blocked.calls == 0

    def test_missing_questionnaire_blocks(self):
        from sitara.designs.models import Design, DesignSession

        design = Design.objects.create(design_session=DesignSession.objects.create())
        provider = fakes.SequenceProvider([])
        with pytest.raises(DesignNotReady) as excinfo:
            generate_design_spec_for_design(design, provider=provider)
        assert excinfo.value.code == "questionnaire_missing"
        assert provider.calls == 0

    def test_unsafe_free_text_blocks_before_provider(self):
        design = make_complete_design(
            answers={
                "garment_type": "lehenga",
                "ceremony": "nikah",
                "silhouette": "flared_lehenga",
                "colour_palette": ["ivory"],
                "embellishment_styles": ["zardozi"],
                "final_notes": "Please make it look like Sabyasachi's designs.",
            }
        )
        provider = fakes.SequenceProvider([])
        with pytest.raises(UnsafeUserTextError):
            generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 0


class TestNoInspirationLeakage:
    def test_request_carries_no_selected_inspiration_metadata(self, inmemory_storage):
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        ss = _source_selections(design)
        provider = fakes.SequenceProvider([fakes.valid_result(ss)])
        generate_design_spec_for_design(design, provider=provider)
        request = provider.requests[0]
        blob = request.system_prompt + request.user_message + str(request.source_selections)
        assert str(asset.id) not in blob
        assert asset.image_storage_key not in blob
        assert asset.title not in blob


class TestStaleInputProtection:
    """A valid spec built from inputs that changed during the (un-transacted)
    provider call is discarded — the newer draft is never overwritten."""

    def test_answer_change_during_generation_is_not_persisted(self):
        design = make_complete_design()
        ss = _source_selections(design)

        def edit_answers():
            changed = dict(COMPLETE_ANSWERS)
            changed["colour_palette"] = ["ivory"]  # still complete, but different
            Design.objects.filter(pk=design.pk).update(answers=changed)

        provider = fakes.MutatingProvider(ss, edit_answers)
        with pytest.raises(DesignChangedDuringGeneration):
            generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 1
        assert DesignVersion.objects.filter(design=design).count() == 0

    def test_questionnaire_version_change_during_generation_is_not_persisted(self):
        design = make_complete_design()
        ss = _source_selections(design)
        other = make_active_v1(version=2, status="retired")

        def swap_version():
            Design.objects.filter(pk=design.pk).update(questionnaire_version=other)

        provider = fakes.MutatingProvider(ss, swap_version)
        with pytest.raises(DesignChangedDuringGeneration):
            generate_design_spec_for_design(design, provider=provider)
        assert DesignVersion.objects.filter(design=design).count() == 0

    def test_inspiration_selection_change_during_generation_is_not_persisted(
        self, inmemory_storage
    ):
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        ss = _source_selections(design)
        asset = make_eligible_asset()

        def add_inspiration():
            DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)

        provider = fakes.MutatingProvider(ss, add_inspiration)
        with pytest.raises(DesignChangedDuringGeneration):
            generate_design_spec_for_design(design, provider=provider)
        assert DesignVersion.objects.filter(design=design).count() == 0

    def test_inspiration_becoming_ineligible_during_generation_is_not_persisted(
        self, inmemory_storage
    ):
        from sitara.catalogue.services import retire_inspiration_asset
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        ss = _source_selections(design)

        def retire():
            retire_inspiration_asset(asset)

        provider = fakes.MutatingProvider(ss, retire)
        with pytest.raises(DesignChangedDuringGeneration):
            generate_design_spec_for_design(design, provider=provider)
        assert DesignVersion.objects.filter(design=design).count() == 0


class TestFinalPersistenceAtomicity:
    """The final freshness re-check and DesignVersion creation happen in ONE
    transaction under the Design row lock (Phase 9 Part A)."""

    def test_snapshot_check_and_version_creation_share_one_transaction(self, monkeypatch):
        from django.db import connection

        from sitara.generation import services as gen_services

        design = make_complete_design()
        ss = _source_selections(design)
        captured: dict = {}
        real = gen_services.create_next_design_version_locked

        def spy(locked):
            # Version creation must run inside the atomic block, on the same
            # locked Design row that the freshness check just validated.
            captured["in_atomic"] = connection.in_atomic_block
            captured["design_pk"] = locked.pk
            return real(locked)

        monkeypatch.setattr(gen_services, "create_next_design_version_locked", spy)
        provider = fakes.SequenceProvider([fakes.valid_result(ss)])
        version = generate_design_spec_for_design(design, provider=provider)

        assert captured["in_atomic"] is True
        assert captured["design_pk"] == design.pk
        assert version.version_number == 1

    def test_change_detected_at_final_lock_creates_no_version_and_no_retry(self):
        # MutatingProvider commits a draft edit DURING the (un-transacted)
        # provider call — i.e. before the final row lock. The finalise step
        # locks the row, recomputes the snapshot, sees the change and rejects.
        design = make_complete_design()
        ss = _source_selections(design)

        def edit_answers():
            changed = dict(COMPLETE_ANSWERS)
            changed["colour_palette"] = ["gold"]  # complete, but different
            Design.objects.filter(pk=design.pk).update(answers=changed)

        provider = fakes.MutatingProvider(ss, edit_answers)
        with pytest.raises(DesignChangedDuringGeneration):
            generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 1  # no provider retry after a freshness failure
        assert DesignVersion.objects.filter(design=design).count() == 0


class TestTokenAggregation:
    def test_single_success_stores_its_usage(self):
        design = make_complete_design()
        ss = _source_selections(design)
        provider = fakes.SequenceProvider(
            [fakes.result_with_usage(ss, input_tokens=1200, output_tokens=340)]
        )
        version = generate_design_spec_for_design(design, provider=provider)
        version.refresh_from_db()
        assert version.design_spec_input_tokens == 1200
        assert version.design_spec_output_tokens == 340

    def test_invalid_then_valid_sums_both_attempts(self):
        design = make_complete_design()
        ss = _source_selections(design)
        provider = fakes.SequenceProvider(
            [
                fakes.result_with_usage(ss, input_tokens=10, output_tokens=20, valid=False),
                fakes.result_with_usage(ss, input_tokens=100, output_tokens=200),
            ]
        )
        version = generate_design_spec_for_design(design, provider=provider)
        version.refresh_from_db()
        assert provider.calls == 2
        assert version.design_spec_input_tokens == 110
        assert version.design_spec_output_tokens == 220

    def test_missing_dimension_on_any_attempt_stores_none(self):
        design = make_complete_design()
        ss = _source_selections(design)
        provider = fakes.SequenceProvider(
            [
                fakes.result_with_usage(ss, input_tokens=None, output_tokens=20, valid=False),
                fakes.result_with_usage(ss, input_tokens=100, output_tokens=200),
            ]
        )
        version = generate_design_spec_for_design(design, provider=provider)
        version.refresh_from_db()
        # input unknown on attempt 1 → total input None; output known on both → 220.
        assert version.design_spec_input_tokens is None
        assert version.design_spec_output_tokens == 220


class TestSemanticRetry:
    def test_semantic_failure_then_valid_retries_and_persists_once(self):
        design = make_complete_design()
        ss = _source_selections(design)
        provider = fakes.SequenceProvider(
            [fakes.semantic_invalid_result(ss), fakes.valid_result(ss)]
        )
        generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 2
        assert DesignVersion.objects.filter(design=design).count() == 1

    def test_two_semantic_failures_persist_nothing(self):
        design = make_complete_design()
        ss = _source_selections(design)
        provider = fakes.SequenceProvider(
            [fakes.semantic_invalid_result(ss), fakes.semantic_invalid_result(ss)]
        )
        with pytest.raises(GenerationFailed):
            generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 2
        assert DesignVersion.objects.filter(design=design).count() == 0
