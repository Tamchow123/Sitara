"""Constrained DesignSpec refinement orchestration fixture tests (Phase 14
Part B) — injected fake providers, zero network calls."""

import copy
import logging

import pytest
from django.utils import timezone

from sitara.ai_gateway.structured_design import StructuredDesignResult
from sitara.designs.models import Design, DesignVersion
from sitara.generation.context import build_generation_context
from sitara.generation.design_spec import DESIGN_SPEC_SCHEMA_VERSION, SPEC_TEMPLATE_VERSION
from sitara.generation.fixture_provider import build_fixture_spec
from sitara.generation.inspiration_context import (
    InspirationAcknowledgement,
    InspirationContextItem,
    InspirationContextSnapshot,
    InspirationProviderCues,
    inspiration_context_sha256,
)
from sitara.generation.prompt_builder import PROMPT_BUILDER_VERSION
from sitara.generation.refinement import REFINEMENT_CHANGE_TYPES, normalise_refinement_request
from sitara.generation.refinement_service import (
    REFINEMENT_DESIGN_SPEC_TEMPLATE_VERSION,
    DesignChangedDuringRefinement,
    GenerationRefused,
    RefinementGenerationFailed,
    RefinementLimitReached,
    RefinementNoChangeProduced,
    RefinementSourceUnavailable,
    generate_refined_design_spec_for_design,
)

from .factory import make_complete_design
from .fakes import SequenceProvider

pytestmark = pytest.mark.django_db

_USAGE = {"input_tokens": 111, "output_tokens": 222}
_TEST_PROVIDER = "fake"
_TEST_MODEL = "fake-model"


def _result(payload) -> StructuredDesignResult:
    return StructuredDesignResult(
        payload=payload,
        provider=_TEST_PROVIDER,
        model=_TEST_MODEL,
        input_tokens=_USAGE["input_tokens"],
        output_tokens=_USAGE["output_tokens"],
        stop_reason="end_turn",
    )


def _refused_result() -> StructuredDesignResult:
    return StructuredDesignResult(
        payload=None,
        provider=_TEST_PROVIDER,
        model=_TEST_MODEL,
        input_tokens=None,
        output_tokens=None,
        stop_reason="refusal",
        refused=True,
    )


def make_source_version(design: Design, spec_payload: dict, **overrides) -> DesignVersion:
    fields = {
        "design": design,
        "version_number": 1,
        "design_spec": spec_payload,
        "design_spec_schema_version": DESIGN_SPEC_SCHEMA_VERSION,
        "design_spec_template_version": SPEC_TEMPLATE_VERSION,
        "design_spec_provider": "fixture",
        "design_spec_model": "fixture-model",
        "design_spec_generated_at": timezone.now(),
        "image_prompt": "A deterministic placeholder prompt.",
        "prompt_builder_version": PROMPT_BUILDER_VERSION,
        "image_storage_key": f"design-images/{design.id}/v1/original.webp",
        "image_sha256": "a" * 64,
        "image_size_bytes": 100_000,
        "image_width": 900,
        "image_height": 1200,
        "thumbnail_storage_key": f"design-images/{design.id}/v1/thumbnail.webp",
        "thumbnail_sha256": "b" * 64,
        "thumbnail_size_bytes": 5_000,
        "thumbnail_width": 200,
        "thumbnail_height": 260,
        "image_processor_version": "1.0.0",
        "image_ingested_at": timezone.now(),
    }
    fields.update(overrides)
    return DesignVersion.objects.create(**fields)


def make_snapshot() -> InspirationContextSnapshot:
    item = InspirationContextItem(
        asset_id="11111111-1111-1111-1111-111111111111",
        position=1,
        provider_cues=InspirationProviderCues(
            garment_type="lehenga",
            visual_description="Front view of an emerald bridal outfit.",
            cultural_context="Broad Pakistani bridal styling reference.",
        ),
        acknowledgement=InspirationAcknowledgement(title="Emerald look", attribution="Studio A"),
    )
    return InspirationContextSnapshot(schema_version=1, items=[item])


def make_ready_design(*, with_inspiration=False):
    design = make_complete_design()
    source_selections = build_generation_context(design).source_selections
    spec_payload = build_fixture_spec(source_selections)
    kwargs = {}
    if with_inspiration:
        snapshot = make_snapshot()
        kwargs["inspiration_context"] = snapshot.model_dump(mode="json")
        kwargs["inspiration_context_schema_version"] = 1
        kwargs["inspiration_context_sha256"] = inspiration_context_sha256(snapshot)
    version = make_source_version(design, spec_payload, **kwargs)
    return design, version, spec_payload


def refinement_request(change_type: str, note: str = ""):
    return normalise_refinement_request(
        {"schema_version": 1, "change_type": change_type, "note": note}
    )


# Exactly one allowed-field edit per category, matching REFINEMENT_ALLOWED_PATHS.
_ALLOWED_EDITS = {
    "colour_story": lambda spec: spec["colour_story"].__setitem__(
        "palette_summary", "An updated blush and champagne palette summary."
    ),
    "fabric_and_texture": lambda spec: spec["fabrics_and_texture"][0].__setitem__(
        "finish_and_movement", "An updated finish with a softer sheen."
    ),
    "embellishment": lambda spec: spec["embellishment_plan"].__setitem__(
        "density", "An updated, slightly richer embellishment density."
    ),
    "sleeves_and_coverage": lambda spec: spec["coverage_and_drape"].__setitem__(
        "sleeves", "Updated sleeve notes with a longer silhouette."
    ),
    "neckline": lambda spec: spec["coverage_and_drape"].__setitem__(
        "neckline", "An updated boat neckline description."
    ),
    "dupatta_or_saree_drape": lambda spec: spec["coverage_and_drape"].__setitem__(
        "dupatta_or_saree_drape", "An updated drape description over one shoulder."
    ),
    "silhouette_detail": lambda spec: spec["garment_breakdown"].__setitem__(
        "key_proportions", "Updated proportion notes with a fuller flare."
    ),
    "styling_details": lambda spec: spec.__setitem__(
        "styling_notes", ["An updated styling suggestion for local review."]
    ),
}


def apply_allowed_edit(spec_payload: dict, change_type: str) -> dict:
    refined = copy.deepcopy(spec_payload)
    _ALLOWED_EDITS[change_type](refined)
    return refined


class TestSuccessfulRefinementPerCategory:
    @pytest.mark.parametrize("change_type", REFINEMENT_CHANGE_TYPES)
    def test_one_allowed_change_succeeds(self, change_type):
        design, source, spec_payload = make_ready_design()
        refined_payload = apply_allowed_edit(spec_payload, change_type)
        provider = SequenceProvider([_result(refined_payload)])
        request = refinement_request(change_type)

        version = generate_refined_design_spec_for_design(
            design, source, request, provider=provider
        )

        assert version.version_number == 2
        assert version.parent_version_id == source.pk
        assert version.design_spec_template_version == REFINEMENT_DESIGN_SPEC_TEMPLATE_VERSION
        assert version.design_spec_schema_version == DESIGN_SPEC_SCHEMA_VERSION
        assert version.refinement_request["change_type"] == change_type
        assert provider.calls == 1

    def test_multiple_allowed_changes_in_one_category_succeeds(self):
        design, source, spec_payload = make_ready_design()
        refined = copy.deepcopy(spec_payload)
        refined["colour_story"]["palette_summary"] = "An updated palette."
        refined["colour_story"]["rationale"] = "An updated rationale for the palette."
        refined["styling_notes"] = ["A new styling note."]
        provider = SequenceProvider([_result(refined)])

        version = generate_refined_design_spec_for_design(
            design, source, refinement_request("colour_story"), provider=provider
        )
        assert (
            version.design_spec["colour_story"]["rationale"]
            == "An updated rationale for the palette."
        )


class TestRejectedChanges:
    def test_unrelated_field_change_is_rejected(self):
        design, source, spec_payload = make_ready_design()
        refined = copy.deepcopy(spec_payload)
        # embellishment_plan is not in colour_story's allowlist.
        refined["embellishment_plan"]["density"] = "An unrelated embellishment change."
        provider = SequenceProvider([_result(refined), _result(refined)])

        with pytest.raises(RefinementGenerationFailed):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )
        assert provider.calls == 2
        assert not DesignVersion.objects.filter(design=design, version_number=2).exists()

    def test_source_selections_change_is_rejected(self):
        design, source, spec_payload = make_ready_design()
        refined = copy.deepcopy(spec_payload)
        refined["source_selections"] = copy.deepcopy(spec_payload["source_selections"])
        refined["source_selections"]["garment_type"] = "sharara"
        refined["colour_story"]["palette_summary"] = "An updated palette."
        provider = SequenceProvider([_result(refined), _result(refined)])

        with pytest.raises(RefinementGenerationFailed):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )
        assert not DesignVersion.objects.filter(design=design, version_number=2).exists()

    def test_no_op_output_is_rejected(self):
        design, source, spec_payload = make_ready_design()
        provider = SequenceProvider(
            [_result(copy.deepcopy(spec_payload)), _result(copy.deepcopy(spec_payload))]
        )

        with pytest.raises(RefinementNoChangeProduced):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )
        assert not DesignVersion.objects.filter(design=design, version_number=2).exists()

    def test_unsafe_output_is_rejected(self):
        design, source, spec_payload = make_ready_design()
        refined = apply_allowed_edit(spec_payload, "styling_details")
        refined["styling_notes"] = ["Style it the way Sabyasachi would."]
        provider = SequenceProvider([_result(refined), _result(refined)])

        with pytest.raises(RefinementGenerationFailed):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("styling_details"), provider=provider
            )
        assert not DesignVersion.objects.filter(design=design, version_number=2).exists()

    def test_refinement_process_mention_is_rejected(self):
        design, source, spec_payload = make_ready_design()
        refined = apply_allowed_edit(spec_payload, "styling_details")
        refined["concept_summary"] = (
            spec_payload["concept_summary"] + " This is the refined version."
        )
        provider = SequenceProvider([_result(refined), _result(refined)])

        with pytest.raises(RefinementGenerationFailed):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("styling_details"), provider=provider
            )


class TestRetryPolicy:
    def test_invalid_first_then_valid_second_succeeds(self):
        design, source, spec_payload = make_ready_design()
        bad = copy.deepcopy(spec_payload)
        bad["embellishment_plan"]["density"] = "An unrelated change on attempt one."
        good = apply_allowed_edit(spec_payload, "colour_story")
        provider = SequenceProvider([_result(bad), _result(good)])

        version = generate_refined_design_spec_for_design(
            design, source, refinement_request("colour_story"), provider=provider
        )
        assert provider.calls == 2
        assert version.version_number == 2

    def test_two_invalid_outputs_fail_safely(self):
        design, source, spec_payload = make_ready_design()
        bad = copy.deepcopy(spec_payload)
        bad["embellishment_plan"]["density"] = "An unrelated change."
        provider = SequenceProvider([_result(bad), _result(bad)])

        with pytest.raises(RefinementGenerationFailed) as excinfo:
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )
        assert excinfo.value.attempts == 2
        assert provider.calls == 2
        source.refresh_from_db()
        assert source.design_spec == spec_payload

    def test_at_most_two_provider_requests_ever(self):
        design, source, spec_payload = make_ready_design()
        bad = copy.deepcopy(spec_payload)
        bad["embellishment_plan"]["density"] = "An unrelated change."
        provider = SequenceProvider([_result(bad), _result(bad)])
        with pytest.raises(RefinementGenerationFailed):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )
        assert provider.calls == 2

    def test_refusal_aborts_immediately_without_retry(self):
        design, source, _ = make_ready_design()
        provider = SequenceProvider([_refused_result()])
        with pytest.raises(GenerationRefused):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )
        assert provider.calls == 1


class TestNoteHandling:
    def test_optional_note_absent(self):
        design, source, spec_payload = make_ready_design()
        refined = apply_allowed_edit(spec_payload, "colour_story")
        provider = SequenceProvider([_result(refined)])
        version = generate_refined_design_spec_for_design(
            design, source, refinement_request("colour_story", ""), provider=provider
        )
        assert version.refinement_request["note"] == ""

    def test_optional_note_present_and_delimited(self):
        design, source, spec_payload = make_ready_design()
        refined = apply_allowed_edit(spec_payload, "colour_story")
        provider = SequenceProvider([_result(refined)])
        request = refinement_request("colour_story", "Use a softer blush tone please.")

        generate_refined_design_spec_for_design(design, source, request, provider=provider)

        sent = provider.requests[0].user_message
        assert "<<<BEGIN_UNTRUSTED_USER_PREFERENCE_TEXT>>>" in sent
        assert "Use a softer blush tone please." in sent

    def test_raw_note_is_never_logged(self, caplog):
        design, source, spec_payload = make_ready_design()
        refined = apply_allowed_edit(spec_payload, "colour_story")
        provider = SequenceProvider([_result(refined)])
        secret_note = "A very distinctive unlikely-to-collide note fragment 9f8e7d."
        request = refinement_request("colour_story", secret_note)

        with caplog.at_level(logging.DEBUG):
            generate_refined_design_spec_for_design(design, source, request, provider=provider)

        for record in caplog.records:
            assert secret_note not in record.getMessage()


class TestInspirationSnapshotCopy:
    def test_exact_original_inspiration_snapshot_copied(self):
        design, source, spec_payload = make_ready_design(with_inspiration=True)
        refined = apply_allowed_edit(spec_payload, "colour_story")
        provider = SequenceProvider([_result(refined)])

        version = generate_refined_design_spec_for_design(
            design, source, refinement_request("colour_story"), provider=provider
        )

        assert version.inspiration_context == source.inspiration_context
        assert (
            version.inspiration_context_schema_version == source.inspiration_context_schema_version
        )
        assert version.inspiration_context_sha256 == source.inspiration_context_sha256

    def test_no_catalogue_query_needed_to_rebuild_inspiration_data(self):
        design, source, spec_payload = make_ready_design(with_inspiration=True)
        refined = apply_allowed_edit(spec_payload, "colour_story")
        provider = SequenceProvider([_result(refined)])

        generate_refined_design_spec_for_design(
            design, source, refinement_request("colour_story"), provider=provider
        )
        # Combined with test_refinement_service_never_imports_catalogue below
        # (the module never even imports the catalogue app, so it cannot
        # issue a catalogue query), this proves the child's snapshot is the
        # exact source bytes, never a catalogue rebuild.
        version = DesignVersion.objects.get(design=design, version_number=2)
        assert version.inspiration_context == source.inspiration_context

    def test_legacy_source_with_no_inspiration_context_yields_none_on_child(self):
        design, source, spec_payload = make_ready_design(with_inspiration=False)
        refined = apply_allowed_edit(spec_payload, "colour_story")
        provider = SequenceProvider([_result(refined)])

        version = generate_refined_design_spec_for_design(
            design, source, refinement_request("colour_story"), provider=provider
        )
        assert version.inspiration_context is None


def test_refinement_service_never_imports_catalogue():
    import inspect

    from sitara.generation import refinement_service

    source = inspect.getsource(refinement_service)
    for marker in ("import sitara.catalogue", "from sitara.catalogue", "from .catalogue"):
        assert marker not in source


class TestNoImageDataSentToProvider:
    def test_user_message_carries_no_image_bytes_or_urls(self):
        design, source, spec_payload = make_ready_design()
        refined = apply_allowed_edit(spec_payload, "colour_story")
        provider = SequenceProvider([_result(refined)])

        generate_refined_design_spec_for_design(
            design, source, refinement_request("colour_story"), provider=provider
        )

        sent = provider.requests[0].user_message.lower()
        for marker in ("image_storage_key", "signed", "http://", "https://", "seed"):
            assert marker not in sent


class TestSourceVersionUnchanged:
    @pytest.mark.parametrize(
        "outcome",
        ["success", "no_change", "invalid", "refused"],
    )
    def test_source_version_unchanged_after_every_outcome(self, outcome):
        design, source, spec_payload = make_ready_design()
        original_snapshot = DesignVersion.objects.get(pk=source.pk).design_spec

        if outcome == "success":
            provider = SequenceProvider([_result(apply_allowed_edit(spec_payload, "colour_story"))])
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )
        elif outcome == "no_change":
            provider = SequenceProvider(
                [_result(copy.deepcopy(spec_payload)), _result(copy.deepcopy(spec_payload))]
            )
            with pytest.raises(RefinementNoChangeProduced):
                generate_refined_design_spec_for_design(
                    design, source, refinement_request("colour_story"), provider=provider
                )
        elif outcome == "invalid":
            bad = copy.deepcopy(spec_payload)
            bad["embellishment_plan"]["density"] = "unrelated"
            provider = SequenceProvider([_result(bad), _result(bad)])
            with pytest.raises(RefinementGenerationFailed):
                generate_refined_design_spec_for_design(
                    design, source, refinement_request("colour_story"), provider=provider
                )
        else:
            provider = SequenceProvider([_refused_result()])
            with pytest.raises(GenerationRefused):
                generate_refined_design_spec_for_design(
                    design, source, refinement_request("colour_story"), provider=provider
                )

        source.refresh_from_db()
        assert source.design_spec == original_snapshot
        assert source.version_number == 1


class TestSourceValidation:
    def test_source_must_be_version_one(self):
        design, v1, spec_payload = make_ready_design()
        request = refinement_request("colour_story")
        v2 = make_source_version(
            design,
            spec_payload,
            version_number=2,
            parent_version=v1,
            refinement_request=request.model_dump(mode="json"),
            refinement_request_schema_version=1,
            refinement_request_sha256="e" * 64,
        )
        with pytest.raises(RefinementSourceUnavailable):
            generate_refined_design_spec_for_design(
                design, v2, refinement_request("colour_story"), provider=SequenceProvider([])
            )

    def test_source_without_permanent_image_is_unavailable(self):
        design, source, spec_payload = make_ready_design()
        source.image_storage_key = ""
        source.image_sha256 = ""
        source.image_size_bytes = None
        source.image_width = None
        source.image_height = None
        source.thumbnail_storage_key = ""
        source.thumbnail_sha256 = ""
        source.thumbnail_size_bytes = None
        source.thumbnail_width = None
        source.thumbnail_height = None
        source.image_processor_version = ""
        source.image_ingested_at = None
        source.save()
        with pytest.raises(RefinementSourceUnavailable):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=SequenceProvider([])
            )

    def test_source_without_spec_is_unavailable(self):
        design = make_complete_design()
        version = DesignVersion.objects.create(design=design, version_number=1)
        with pytest.raises(RefinementSourceUnavailable):
            generate_refined_design_spec_for_design(
                design, version, refinement_request("colour_story"), provider=SequenceProvider([])
            )


class TestRefinementLimit:
    def test_second_refinement_is_rejected(self):
        design, source, spec_payload = make_ready_design()
        refined = apply_allowed_edit(spec_payload, "colour_story")
        provider = SequenceProvider([_result(refined)])
        generate_refined_design_spec_for_design(
            design, source, refinement_request("colour_story"), provider=provider
        )
        with pytest.raises(RefinementLimitReached):
            generate_refined_design_spec_for_design(
                design,
                source,
                refinement_request("colour_story"),
                provider=SequenceProvider(
                    [_result(apply_allowed_edit(spec_payload, "colour_story"))]
                ),
            )
        assert DesignVersion.objects.filter(design=design).count() == 2


class TestDesignChangedDuringRefinement:
    def test_source_changed_between_snapshot_and_persistence_blocks_persistence(self):
        design, source, spec_payload = make_ready_design()
        mutated = copy.deepcopy(spec_payload)
        mutated["title"] = "A title changed out from under the refinement."

        class MutatingProvider:
            name = "fake"

            def __init__(self):
                self.calls = 0

            def generate(self, request):
                self.calls += 1
                DesignVersion.objects.filter(pk=source.pk).update(design_spec=mutated)
                return _result(apply_allowed_edit(spec_payload, "colour_story"))

        provider = MutatingProvider()
        with pytest.raises(DesignChangedDuringRefinement):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )
        assert not DesignVersion.objects.filter(design=design, version_number=2).exists()
        # The provider was NOT retried after the persistence-time freshness check failed.
        assert provider.calls == 1
