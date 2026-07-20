"""DesignSpec generation orchestration: gates, retry, persistence, locking."""

import json

import pytest

from sitara.ai_gateway.structured_design import (
    StructuredDesignProviderError,
    StructuredDesignResult,
)
from sitara.designs.models import Design, DesignVersion
from sitara.generation.context import DesignNotReady, build_generation_context
from sitara.generation.design_spec import (
    DESIGN_SPEC_SCHEMA_VERSION,
    SPEC_TEMPLATE_VERSION,
    DesignSpec,
)
from sitara.generation.fixture_provider import build_fixture_spec
from sitara.generation.input_safety import UnsafeUserTextError
from sitara.generation.inspiration_context import inspiration_context_sha256
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


def _trusted_json(user_message: str) -> dict:
    """Parse just the trusted JSON block from a built user message, ignoring
    any untrusted section or retry note that may follow it."""
    from sitara.generation.prompting import _TRUSTED_HEADER

    start = user_message.index(_TRUSTED_HEADER) + len(_TRUSTED_HEADER) + 1
    obj, _ = json.JSONDecoder().raw_decode(user_message, start)
    return obj


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
        from sitara.catalogue.tests.utils import make_eligible_asset, make_rights
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset(rights=make_rights(verified=True, attribution_text="Studio A"))
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        ss = _source_selections(design)
        provider = fakes.SequenceProvider([fakes.valid_result(ss)])
        generate_design_spec_for_design(design, provider=provider)
        request = provider.requests[0]
        blob = request.system_prompt + request.user_message + str(request.source_selections)
        assert str(asset.id) not in blob
        assert asset.image_storage_key not in blob
        assert asset.title not in blob
        assert "Studio A" not in blob


class TestInspirationCuesInRequest:
    """Fixture-provider tests proving the exact provider-facing cue shape
    (Phase 13 §19/§23): zero, one and up to three assets preserve order and
    reach Anthropic's user message as trusted structured JSON, while image
    bytes, storage keys, public URLs, rights evidence, asset ids, titles and
    attribution never do. No network socket is opened (enforced by the
    module-level ``no_network`` autouse fixture)."""

    def test_zero_selected_assets_produces_no_cues(self):
        design = make_complete_design()
        ss = _source_selections(design)
        provider = fakes.SequenceProvider([fakes.valid_result(ss)])
        generate_design_spec_for_design(design, provider=provider)
        assert '"curated_inspiration_cues": []' in provider.requests[0].user_message

    def test_one_to_three_assets_preserve_order(self, inmemory_storage):
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        assets = [
            make_eligible_asset(
                title=f"Look {i}",
                alt_text=f"A safe visual description number {i}.",
                garment_type="lehenga",
                cultural_context="",
            )
            for i in range(3)
        ]
        for index, asset in enumerate(assets, start=1):
            DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=index)
        ss = _source_selections(design)
        provider = fakes.SequenceProvider([fakes.valid_result(ss)])
        generate_design_spec_for_design(design, provider=provider)
        message = provider.requests[0].user_message
        positions = [message.index(f'"A safe visual description number {i}.') for i in range(3)]
        assert positions == sorted(positions)

    def test_cues_contain_only_the_four_documented_fields(self, inmemory_storage):
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        ss = _source_selections(design)
        provider = fakes.SequenceProvider([fakes.valid_result(ss)])
        generate_design_spec_for_design(design, provider=provider)
        message = provider.requests[0].user_message
        cues_line_start = message.index('"curated_inspiration_cues"')
        cues_block = message[cues_line_start : cues_line_start + 400]
        forbidden_values = (
            "asset_id",
            "title",
            "attribution",
            asset.image_storage_key,
            str(asset.id),
        )
        for forbidden in forbidden_values:
            assert forbidden not in cues_block

    def test_no_image_bytes_storage_keys_or_urls_reach_the_request(self, inmemory_storage):
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        ss = _source_selections(design)
        provider = fakes.SequenceProvider([fakes.valid_result(ss)])
        generate_design_spec_for_design(design, provider=provider)
        blob = provider.requests[0].system_prompt + provider.requests[0].user_message
        assert asset.image_storage_key not in blob
        assert asset.thumbnail_storage_key not in blob
        assert "http://" not in blob and "https://" not in blob

    def test_unsafe_inspiration_metadata_blocks_before_provider(self, inmemory_storage):
        from sitara.catalogue.models import InspirationAsset
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset()
        InspirationAsset.objects.filter(pk=asset.pk).update(
            alt_text="Styled after Sabyasachi's signature look."
        )
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        provider = fakes.SequenceProvider([])
        with pytest.raises(DesignNotReady) as excinfo:
            generate_design_spec_for_design(design, provider=provider)
        assert excinfo.value.code == "inspiration_metadata_unavailable"
        assert provider.calls == 0

    def test_retry_reuses_the_exact_same_inspiration_context(self, inmemory_storage):
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        ss = _source_selections(design)
        provider = fakes.SequenceProvider([fakes.malformed_result(), fakes.valid_result(ss)])
        generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 2
        first_cues = _trusted_json(provider.requests[0].user_message)["curated_inspiration_cues"]
        second_cues = _trusted_json(provider.requests[1].user_message)["curated_inspiration_cues"]
        assert first_cues == second_cues
        assert first_cues  # non-empty: the retry genuinely carried the cue

    def test_adversarial_conflicting_cue_does_not_change_canonical_selections(
        self, inmemory_storage
    ):
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()  # garment_type == "lehenga"
        conflicting = make_eligible_asset(garment_type="saree", alt_text="A saree drape look.")
        DesignInspiration.objects.create(design=design, inspiration_asset=conflicting, position=1)
        ss = _source_selections(design)
        assert ss["garment_type"] == "lehenga"
        provider = fakes.SequenceProvider([fakes.valid_result(ss)])
        version = generate_design_spec_for_design(design, provider=provider)
        # The conflicting cue genuinely reached the request...
        assert '"garment_type": "saree"' in provider.requests[0].user_message
        # ...but the canonical selections in the persisted output are
        # unaffected — the system prompt plus exact echo validation remain
        # the authoritative control.
        assert version.design_spec["source_selections"]["garment_type"] == "lehenga"


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

    def test_inspiration_metadata_mutation_during_generation_is_not_persisted(
        self, inmemory_storage
    ):
        # The asset stays publicly eligible throughout — only its alt_text
        # (a provider cue) changes. Only the CONTENT-level snapshot
        # comparison, not the eligibility recheck, can catch this.
        from sitara.catalogue.models import InspirationAsset
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        ss = _source_selections(design)

        def mutate_alt_text():
            InspirationAsset.objects.filter(pk=asset.pk).update(
                alt_text="A different, still-safe visual description."
            )

        provider = fakes.MutatingProvider(ss, mutate_alt_text)
        with pytest.raises(DesignChangedDuringGeneration):
            generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 1  # no provider retry after a stale-context failure
        assert DesignVersion.objects.filter(design=design).count() == 0

    def test_attribution_mutation_during_generation_is_not_persisted(self, inmemory_storage):
        # Attribution never reaches the provider, but it is still part of the
        # audit snapshot's exact content and must still block persistence.
        from sitara.catalogue.models import UsageRights
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        ss = _source_selections(design)

        def mutate_attribution():
            UsageRights.objects.filter(pk=asset.usage_rights_id).update(
                attribution_text="A different studio"
            )

        provider = fakes.MutatingProvider(ss, mutate_attribution)
        with pytest.raises(DesignChangedDuringGeneration):
            generate_design_spec_for_design(design, provider=provider)
        assert DesignVersion.objects.filter(design=design).count() == 0


class TestInspirationOutputLeakage:
    """The exact source_selections echo validation remains unchanged;
    post-output semantic checks additionally reject any inspiration
    title/attribution appearing in the generated narrative (Phase 13 §14)."""

    def test_leaked_title_is_rejected_and_retried(self, inmemory_storage):
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset(title="Emerald velvet showcase look")
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        ss = _source_selections(design)

        def _leaking_result():
            payload = build_fixture_spec(ss)
            payload["styling_notes"] = [f"Inspired by the {asset.title}."]
            return StructuredDesignResult(
                payload=payload,
                provider="fake",
                model="fake-model",
                input_tokens=1234,
                output_tokens=567,
                stop_reason="end_turn",
            )

        provider = fakes.SequenceProvider([_leaking_result(), fakes.valid_result(ss)])
        version = generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 2
        assert asset.title not in json.dumps(version.design_spec)

    def test_persistently_leaking_title_persists_nothing(self, inmemory_storage):
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset(title="Emerald velvet showcase look")
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        ss = _source_selections(design)

        def _leaking_result():
            payload = build_fixture_spec(ss)
            payload["styling_notes"] = [f"Inspired by the {asset.title}."]
            return StructuredDesignResult(
                payload=payload,
                provider="fake",
                model="fake-model",
                input_tokens=1234,
                output_tokens=567,
                stop_reason="end_turn",
            )

        provider = fakes.SequenceProvider([_leaking_result(), _leaking_result()])
        with pytest.raises(GenerationFailed):
            generate_design_spec_for_design(design, provider=provider)
        assert DesignVersion.objects.filter(design=design).count() == 0

    def test_leaked_attribution_is_rejected(self, inmemory_storage):
        from sitara.catalogue.tests.utils import make_eligible_asset, make_rights
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset(rights=make_rights(verified=True, attribution_text="Studio A"))
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        attribution = asset.usage_rights.attribution_text
        ss = _source_selections(design)

        def _leaking_result():
            payload = build_fixture_spec(ss)
            payload["styling_notes"] = [f"Credit: {attribution}."]
            return StructuredDesignResult(
                payload=payload,
                provider="fake",
                model="fake-model",
                input_tokens=1234,
                output_tokens=567,
                stop_reason="end_turn",
            )

        provider = fakes.SequenceProvider([_leaking_result(), fakes.valid_result(ss)])
        generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 2

    def test_ordinary_output_without_inspiration_text_is_unaffected(self, inmemory_storage):
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset(title="Emerald velvet showcase look")
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        ss = _source_selections(design)
        provider = fakes.SequenceProvider([fakes.valid_result(ss)])
        generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 1

    def test_short_common_word_title_does_not_spuriously_leak(self, inmemory_storage):
        # A single ordinary word used as a catalogue title (nothing requires
        # titles to be distinctive) must never collide with unrelated
        # generated prose that happens to use the same word.
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset(title="Rose")
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        ss = _source_selections(design)

        def _result_mentioning_rose():
            payload = build_fixture_spec(ss)
            payload["styling_notes"] = ["Finished with delicate rose gold embroidery accents."]
            return StructuredDesignResult(
                payload=payload,
                provider="fake",
                model="fake-model",
                input_tokens=1234,
                output_tokens=567,
                stop_reason="end_turn",
            )

        provider = fakes.SequenceProvider([_result_mentioning_rose()])
        generate_design_spec_for_design(design, provider=provider)
        assert provider.calls == 1


class TestInspirationSnapshotPersistence:
    def test_no_inspiration_generation_persists_a_versioned_empty_snapshot(self):
        design = make_complete_design()
        ss = _source_selections(design)
        provider = fakes.SequenceProvider([fakes.valid_result(ss)])
        version = generate_design_spec_for_design(design, provider=provider)
        version.refresh_from_db()
        assert version.inspiration_context == {"schema_version": 1, "items": []}
        assert version.inspiration_context_schema_version == 1
        assert len(version.inspiration_context_sha256) == 64

    def test_selected_inspiration_generation_persists_the_exact_snapshot(self, inmemory_storage):
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration
        from sitara.generation.inspiration_context import build_inspiration_context_snapshot

        design = make_complete_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        expected = build_inspiration_context_snapshot(design)
        ss = _source_selections(design)
        provider = fakes.SequenceProvider([fakes.valid_result(ss)])
        version = generate_design_spec_for_design(design, provider=provider)
        version.refresh_from_db()
        assert version.inspiration_context == expected.model_dump(mode="json")
        assert version.inspiration_context_sha256 == inspiration_context_sha256(expected)

    def test_failure_persists_neither_spec_nor_inspiration_context(self, inmemory_storage):
        from sitara.catalogue.tests.utils import make_eligible_asset
        from sitara.designs.models import DesignInspiration

        design = make_complete_design()
        asset = make_eligible_asset()
        DesignInspiration.objects.create(design=design, inspiration_asset=asset, position=1)
        provider = fakes.SequenceProvider([fakes.malformed_result(), fakes.malformed_result()])
        with pytest.raises(GenerationFailed):
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
