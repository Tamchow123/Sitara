"""Constrained DesignSpec refinement orchestration fixture tests (Phase 14
Part B) — injected fake providers, zero network calls."""

import copy
import logging

import pytest
from django.conf import settings

from sitara.ai_gateway.structured_design import StructuredDesignResult
from sitara.designs.models import DesignVersion, GenerationAttempt
from sitara.generation import cost_control
from sitara.generation.context import build_generation_context
from sitara.generation.design_spec import (
    DESIGN_SPEC_SCHEMA_VERSION,
    DesignSpec,
)
from sitara.generation.fixture_provider import build_fixture_spec
from sitara.generation.inspiration_context import (
    InspirationAcknowledgement,
    InspirationContextItem,
    InspirationContextSnapshot,
    InspirationProviderCues,
    inspiration_context_sha256,
)
from sitara.generation.refinement import (
    REFINEMENT_CHANGE_TYPES,
    canonical_refinement_fields,
    normalise_refinement_request,
)
from sitara.generation.refinement_service import (
    REFINEMENT_DESIGN_SPEC_TEMPLATE_VERSION,
    DesignChangedDuringRefinement,
    GenerationRefused,
    RefinementCategoryUnavailable,
    RefinementGenerationFailed,
    RefinementLimitReached,
    RefinementNoChangeProduced,
    RefinementSourceUnavailable,
    _generate_valid_refined_spec,
    generate_refined_design_spec_for_design,
)

from .factory import make_complete_design, make_complete_v4_design, make_source_version
from .fakes import SequenceProvider
from .test_refinement_prompt_effect import SOURCE_SPEC, canonical_refinement

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
}


def apply_allowed_edit(spec_payload: dict, change_type: str) -> dict:
    refined = copy.deepcopy(spec_payload)
    _ALLOWED_EDITS[change_type](refined)
    return refined


# The categories a version-1 spec can actually be refined in. Derived from the
# production dispatch, never hand-listed: version 1 has no ``neckline_style``
# field at all, so ``neckline`` owns nothing on it and is refused (ADR 0028).
V1_REFINABLE_CHANGE_TYPES = tuple(
    change_type
    for change_type in REFINEMENT_CHANGE_TYPES
    if canonical_refinement_fields(change_type, DESIGN_SPEC_SCHEMA_VERSION)
)

# One legal-looking edit per permanently-immutable canonical selection. Each
# value is a real questionnaire option, so the ONLY reason to reject it is that
# no refinement category may ever move that field.
_IMMUTABLE_SELECTION_EDITS = {
    "garment_type": "saree",
    "ceremony": "walima",
    "regional_style": "gujarati",
}


class TestSuccessfulRefinementPerCategory:
    @pytest.mark.parametrize("change_type", V1_REFINABLE_CHANGE_TYPES)
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


class TestCanonicalSelectionRefinement:
    """ADR 0028: a category may change the one canonical selection it is named
    after, and nothing else in ``source_selections``."""

    def test_a_category_changes_its_own_canonical_selection(self):
        design, source, spec_payload = make_ready_design()
        refined = copy.deepcopy(spec_payload)
        refined["source_selections"]["fabrics"] = ["velvet", "net"]
        provider = SequenceProvider([_result(refined)])

        version = generate_refined_design_spec_for_design(
            design, source, refinement_request("fabric_and_texture"), provider=provider
        )

        assert version.design_spec["source_selections"]["fabrics"] == ["velvet", "net"]
        # Everything else in the echo is untouched.
        untouched = {
            key: value
            for key, value in version.design_spec["source_selections"].items()
            if key != "fabrics"
        }
        assert untouched == {
            key: value
            for key, value in spec_payload["source_selections"].items()
            if key != "fabrics"
        }

    def test_another_categorys_canonical_selection_is_rejected(self):
        # A fabric refinement may not move the silhouette, even though the
        # silhouette IS refinable — by a different category.
        design, source, spec_payload = make_ready_design()
        refined = copy.deepcopy(spec_payload)
        refined["source_selections"]["silhouette"] = "mermaid_lehenga"
        provider = SequenceProvider([_result(refined), _result(refined)])

        with pytest.raises(RefinementGenerationFailed):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("fabric_and_texture"), provider=provider
            )
        assert not DesignVersion.objects.filter(design=design, version_number=2).exists()

    @pytest.mark.parametrize("field, value", sorted(_IMMUTABLE_SELECTION_EDITS.items()))
    @pytest.mark.parametrize("change_type", V1_REFINABLE_CHANGE_TYPES)
    def test_a_permanently_immutable_selection_is_rejected_for_every_category(
        self, change_type, field, value
    ):
        design, source, spec_payload = make_ready_design()
        refined = apply_allowed_edit(spec_payload, change_type)
        refined["source_selections"][field] = value
        provider = SequenceProvider([_result(refined), _result(refined)])

        with pytest.raises(RefinementGenerationFailed):
            generate_refined_design_spec_for_design(
                design, source, refinement_request(change_type), provider=provider
            )
        assert not DesignVersion.objects.filter(design=design, version_number=2).exists()

    @pytest.mark.parametrize("change_type", ["colour_story", "fabric_and_texture", "neckline"])
    def test_custom_colours_is_immutable_on_a_v3_spec_too(self, change_type):
        # The fourth permanently-immutable selection, which the version-1
        # parametrisation above cannot reach: `custom_colours` exists only from
        # DesignSpec v3. It is the bride's own typed-in palette, so a category
        # that owns `colour_palette` is exactly the one that might plausibly
        # reach for it — hence colour_story is in this list, not excluded from
        # it. Driven end to end through the real validator rather than asserted
        # against the mapping table, so a bypass introduced next to the
        # immutability check itself is caught as well as one in the table.
        design = make_complete_v4_design()
        source = make_source_version(
            design, copy.deepcopy(SOURCE_SPEC), design_spec_schema_version=3
        )
        refined = canonical_refinement(change_type)
        refined["source_selections"]["custom_colours"] = ["#7B1E3A"]
        provider = SequenceProvider([_result(refined), _result(copy.deepcopy(refined))])

        with pytest.raises(RefinementGenerationFailed):
            generate_refined_design_spec_for_design(
                design, source, refinement_request(change_type), provider=provider
            )
        assert not DesignVersion.objects.filter(design=design, version_number=2).exists()
        source.refresh_from_db()
        assert source.design_spec["source_selections"]["custom_colours"] == []

    def test_a_v1_spec_refuses_a_neckline_refinement_with_a_controlled_code(self):
        # Version dispatch is mandatory, not optional: a version-1 spec has no
        # neckline_style attribute at all. A controlled refusal, never an
        # AttributeError and never an accepted no-op.
        design, source, _spec_payload = make_ready_design()
        provider = SequenceProvider([])

        with pytest.raises(RefinementCategoryUnavailable) as excinfo:
            generate_refined_design_spec_for_design(
                design, source, refinement_request("neckline"), provider=provider
            )

        assert excinfo.value.code == "refinement_category_unavailable"
        # Refused BEFORE any provider request — nothing was spent.
        assert provider.calls == 0
        assert not DesignVersion.objects.filter(design=design, version_number=2).exists()


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
        # styling_notes is in colour_story's allowlist, so this reaches the
        # safety scan rather than being refused as an out-of-category change.
        refined = apply_allowed_edit(spec_payload, "colour_story")
        refined["styling_notes"] = ["Style it the way Sabyasachi would."]
        provider = SequenceProvider([_result(refined), _result(refined)])

        with pytest.raises(RefinementGenerationFailed):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )
        assert not DesignVersion.objects.filter(design=design, version_number=2).exists()

    def test_refinement_process_mention_is_rejected(self):
        design, source, spec_payload = make_ready_design()
        refined = apply_allowed_edit(spec_payload, "colour_story")
        refined["concept_summary"] = (
            spec_payload["concept_summary"] + " This is the refined version."
        )
        provider = SequenceProvider([_result(refined), _result(refined)])

        with pytest.raises(RefinementGenerationFailed):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
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


class TestRefinementPartialUsageRetainsReservation:
    """P1: the refinement text stage must reconcile only when BOTH token counts
    are present; a partial usage report retains the full conservative reservation
    (mirrors the initial-generation fix) rather than refunding the unknown
    dimension and undercounting spend."""

    def _refined_result(self, refined_payload, *, input_tokens, output_tokens):
        return StructuredDesignResult(
            payload=refined_payload,
            provider=_TEST_PROVIDER,
            model=_TEST_MODEL,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason="end_turn",
        )

    def _run_refinement(self, input_tokens, output_tokens):
        design, _source, spec_payload = make_ready_design()
        source_spec = DesignSpec.model_validate(spec_payload)
        refined = apply_allowed_edit(spec_payload, "colour_story")
        provider = SequenceProvider(
            [self._refined_result(refined, input_tokens=input_tokens, output_tokens=output_tokens)]
        )
        attempt = GenerationAttempt.objects.create(
            design=design, status=GenerationAttempt.Status.QUEUED
        )
        _generate_valid_refined_spec(
            provider, source_spec, "colour_story", "", design.id, generation_attempt=attempt
        )
        attempt.refresh_from_db()
        text_max = cost_control.anthropic_call_max_micro_usd(
            cost_control.active_pricing_profile(), settings.DESIGN_SPEC_MAX_OUTPUT_TOKENS
        )
        return attempt, text_max

    def test_missing_output_tokens_retains_full_reservation(self, in_memory_budget_ledger):
        attempt, text_max = self._run_refinement(input_tokens=1000, output_tokens=None)
        assert attempt.cost_unresolved_micro_usd == text_max  # retained, not partial

    def test_missing_input_tokens_retains_full_reservation(self, in_memory_budget_ledger):
        attempt, text_max = self._run_refinement(input_tokens=None, output_tokens=1000)
        assert attempt.cost_unresolved_micro_usd == text_max  # retained, not partial

    def test_both_tokens_present_reconciles_down(self, in_memory_budget_ledger):
        attempt, text_max = self._run_refinement(input_tokens=1, output_tokens=1)
        # With both present it reconciles to the (tiny) measured actual, well below
        # the conservative reservation, so nothing stays unresolved.
        assert attempt.cost_unresolved_micro_usd == 0
        assert attempt.cost_estimated_micro_usd < text_max
