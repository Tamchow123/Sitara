"""Constrained DesignSpec refinement orchestration fixture tests (Phase 14
Part B) — injected fake providers, zero network calls."""

import ast
import copy
import json
import logging
import pathlib

import pytest
from django.conf import settings
from pydantic import ValidationError

from sitara.ai_gateway.structured_design import StructuredDesignResult
from sitara.content_safety import GeneratedContentRejected, RejectionCategory
from sitara.designs.models import DesignVersion, GenerationAttempt
from sitara.generation import cost_control, refinement_service
from sitara.generation.context import build_generation_context
from sitara.generation.demo.design_spec_engine import build_demo_design_spec
from sitara.generation.design_spec import (
    DESIGN_SPEC_SCHEMA_VERSION,
    SUPPORTED_DESIGN_SPEC_SCHEMA_VERSIONS,
    DesignSpec,
    UnsupportedDesignSpecVersion,
    validate_design_spec,
)
from sitara.generation.fixture_provider import build_fixture_spec
from sitara.generation.inspiration_context import (
    InspirationAcknowledgement,
    InspirationContextItem,
    InspirationContextSnapshot,
    InspirationProviderCues,
    inspiration_context_sha256,
)
from sitara.generation.prompt_builder import ImagePromptBuildError, build_image_prompt
from sitara.generation.refinement import (
    REFINEMENT_CHANGE_TYPES,
    canonical_refinement_fields,
    normalise_refinement_request,
    refinement_allowed_paths,
)
from sitara.generation.refinement_prompting import (
    REFINEMENT_RETRY_NOTE,
    REFINEMENT_RETRY_NOTES,
    REFINEMENT_UNTRUSTED_END,
    RETRY_DISALLOWED_FIELD,
    RETRY_NO_CHANGE,
)
from sitara.generation.refinement_selections import RefinedSelectionsInvalid
from sitara.generation.refinement_service import (
    _REJECTION_RETRY_REASONS,
    REFINEMENT_DESIGN_SPEC_TEMPLATE_VERSION,
    DesignChangedDuringRefinement,
    GenerationRefused,
    RefinementCategoryUnavailable,
    RefinementGenerationFailed,
    RefinementLimitReached,
    RefinementNoChangeProduced,
    RefinementOutputCategory,
    RefinementOutputRejected,
    RefinementSourceUnavailable,
    _generate_valid_refined_spec,
    _rejection_detail,
    generate_refined_design_spec_for_design,
)

from .factory import (
    make_complete_design,
    make_complete_v3_design,
    make_complete_v4_design,
    make_source_version,
)
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


# Exactly one NARRATIVE edit per category, matching REFINEMENT_ALLOWED_PATHS.
#
# On their own these are the shape of output that shipped the Phase 23 defect: a
# spec-level diff is satisfied, every allowlist check passes, and prompt builder
# 8.x renders none of it, so the image prompt comes out byte-identical. Kept as a
# separate table precisely so a test can produce that output deliberately — see
# TestTheRenderedPromptMustActuallyMove — and no longer used alone to stand for
# "a successful refinement".
_NARRATIVE_ONLY_EDITS = {
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

# The CANONICAL half: for each category, a real questionnaire-v1 option value
# that differs from COMPLETE_ANSWERS and is legal for this design's garment
# (a lehenga, so dupatta_style is asked and saree_drape is not). These are what
# the deterministic prompt builder actually renders, so these are what make a
# refinement visible. `neckline` is absent because a version-1 spec has no
# neckline_style field at all (ADR 0028) — it is refused, not refined.
_CANONICAL_EDITS = {
    "colour_story": ("colour_palette", ["emerald", "gold"]),
    "fabric_and_texture": ("fabrics", ["velvet", "organza"]),
    "embellishment": ("embellishment_density", "heavy"),
    "sleeves_and_coverage": ("coverage_preferences", ["elbow_sleeves", "high_neckline"]),
    "dupatta_or_saree_drape": ("dupatta_style", "one_shoulder"),
    "silhouette_detail": ("silhouette", "a_line_lehenga"),
}


def apply_narrative_only_edit(spec_payload: dict, change_type: str) -> dict:
    """An in-category edit that touches ONLY fields the image prompt drops.

    Valid by every rule the service enforced before this guard existed, and
    inert in the thing the customer looks at."""
    refined = copy.deepcopy(spec_payload)
    _NARRATIVE_ONLY_EDITS[change_type](refined)
    return refined


def apply_allowed_edit(spec_payload: dict, change_type: str) -> dict:
    """A realistic successful refinement: the canonical selection MOVES, and the
    descriptive prose is updated to agree with it.

    Until this phase's follow-up these fixtures changed narrative alone, which is
    why "every category succeeds" passed while a live refinement changed nothing
    — the tests were asserting the same inert output the provider was returning.
    """
    refined = apply_narrative_only_edit(spec_payload, change_type)
    field, value = _CANONICAL_EDITS[change_type]
    refined["source_selections"][field] = copy.deepcopy(value)
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
        # The canonical move is what makes this a refinement at all; the three
        # narrative edits ride along to prove several allowlisted paths may move
        # together. Without the canonical one the prompt would not budge and the
        # service would rightly call it no change.
        refined = apply_allowed_edit(spec_payload, "colour_story")
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
        # Prose alone is what this test used to assert, and prose alone is the
        # defect. Both halves are pinned now: the canonical selection really
        # moved, and the concept really renders differently for it.
        assert version.design_spec["source_selections"]["colour_palette"] == ["emerald", "gold"]
        assert build_image_prompt(validate_design_spec(version.design_spec)) != build_image_prompt(
            validate_design_spec(spec_payload)
        )


@pytest.mark.django_db
class TestTheGuardCannotItselfBreakARefinement:
    """The guard calls ``build_image_prompt`` inside output validation, so the
    builder's totality over a validated spec is now load-bearing for refinement
    and not only for initial generation."""

    # One design factory per DesignSpec schema version, derived from the
    # supported set rather than hand-listed, so a new version that nobody wires
    # up here fails loudly instead of going unrendered and untested.
    _DESIGN_FOR_VERSION = {
        1: make_complete_design,
        2: make_complete_v3_design,
        3: make_complete_v4_design,
    }

    def test_every_supported_version_has_a_factory(self):
        assert set(self._DESIGN_FOR_VERSION) == set(SUPPORTED_DESIGN_SPEC_SCHEMA_VERSIONS)

    @pytest.mark.parametrize("schema_version", sorted(SUPPORTED_DESIGN_SPEC_SCHEMA_VERSIONS))
    def test_the_builder_renders_every_supported_schema_version(self, schema_version):
        # If a future schema version reached the guard unrenderable, a perfectly
        # good refinement would die on an unclassified exception instead of a
        # controlled code — a worse failure than the one the guard fixes.
        # The DEMO spec engine, not build_fixture_spec: the fixture builder is
        # version-1 shaped and rejects a v4 design's per-role colours outright,
        # while the demo engine is the version-aware local builder every
        # zero-cost path already uses.
        design = self._DESIGN_FOR_VERSION[schema_version]()
        payload = build_demo_design_spec(build_generation_context(design))
        spec = validate_design_spec(payload)
        assert spec.schema_version == schema_version
        assert build_image_prompt(spec)


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

    def test_a_narrative_only_change_is_no_change_at_all(self):
        # The exact output that shipped the defect. Every allowlist check
        # passes; the prompt builder renders none of it; the prompt comes back
        # byte-identical. Observed live on 2026-08-13 for a neckline request
        # that rewrote four narrative fields and left neckline_style alone.
        design, source, spec_payload = make_ready_design()
        inert = apply_narrative_only_edit(spec_payload, "colour_story")
        assert inert != spec_payload, "the fixture must really differ at spec level"
        provider = SequenceProvider([_result(inert), _result(copy.deepcopy(inert))])

        # NoChangeProduced, not GenerationFailed: the output was well-formed and
        # in-category. It simply did nothing, and that is its own honest answer.
        with pytest.raises(RefinementNoChangeProduced):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )
        assert not DesignVersion.objects.filter(design=design, version_number=2).exists()

    def test_an_inert_first_attempt_is_retried_and_a_real_change_accepted(self):
        # The retry earns its place here: the model gets one corrected attempt
        # to move the canonical selection instead of the refinement being lost.
        design, source, spec_payload = make_ready_design()
        provider = SequenceProvider(
            [
                _result(apply_narrative_only_edit(spec_payload, "colour_story")),
                _result(apply_allowed_edit(spec_payload, "colour_story")),
            ]
        )

        version = generate_refined_design_spec_for_design(
            design, source, refinement_request("colour_story"), provider=provider
        )

        assert version.version_number == 2
        # The whole point, asserted directly rather than inferred from the spec.
        # Compared as RENDERED PROMPTS of the two specs, not against either row's
        # image_prompt column: this service does not persist one (the pipeline's
        # prompt stage does, later), so a column comparison here would pass
        # against an empty string no matter what the model returned.
        assert build_image_prompt(validate_design_spec(version.design_spec)) != build_image_prompt(
            validate_design_spec(spec_payload)
        )

    def test_a_narrative_change_the_prompt_DOES_render_is_still_accepted(self):
        # Guards against over-tightening into "a canonical selection must move".
        # Builder 8.x drops fabrics_and_texture only WHEN canonical fabrics
        # exist, so with none the narrative genuinely is what renders — and a
        # refinement that moves it is a real change, not an inert one.
        design, source, spec_payload = make_ready_design()
        no_canonical_fabrics = copy.deepcopy(spec_payload)
        no_canonical_fabrics["source_selections"]["fabrics"] = []
        source.design_spec = no_canonical_fabrics
        source.image_prompt = build_image_prompt(validate_design_spec(no_canonical_fabrics))
        source.save(update_fields=["design_spec", "image_prompt"])

        # The fabric NAME, not finish_and_movement: with no canonical fabrics the
        # builder renders the narrative entries' names as the design's only
        # fabric statement, so that is the narrative field that actually reaches
        # the image.
        refined = copy.deepcopy(no_canonical_fabrics)
        refined["fabrics_and_texture"][0]["fabric"] = "velvet"
        provider = SequenceProvider([_result(refined)])

        version = generate_refined_design_spec_for_design(
            design, source, refinement_request("fabric_and_texture"), provider=provider
        )

        assert version.version_number == 2
        assert build_image_prompt(validate_design_spec(version.design_spec)) != build_image_prompt(
            validate_design_spec(no_canonical_fabrics)
        )

        # And the converse, which is what makes the rule above necessary rather
        # than merely true: give the SAME spec canonical fabrics and the SAME
        # narrative edit stops reaching the prompt entirely. That asymmetry is
        # the whole reason this guard compares rendered prompts instead of
        # requiring a canonical field to move.
        with_canonical = copy.deepcopy(no_canonical_fabrics)
        with_canonical["source_selections"]["fabrics"] = ["silk"]
        narrative_edit = copy.deepcopy(with_canonical)
        narrative_edit["fabrics_and_texture"][0]["fabric"] = "velvet"
        assert build_image_prompt(validate_design_spec(narrative_edit)) == build_image_prompt(
            validate_design_spec(with_canonical)
        )

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

    def test_a_rejection_logs_WHY_and_still_never_logs_the_note(self, caplog):
        # The success path is where the note test lived, which is the one path
        # that writes no rejection line at all. A real incident produced two
        # lines reading only `exception_type=RefinementOutputRejected` — four
        # candidate causes, none distinguishable — while the discriminator was
        # computed at the raise site and dropped on the floor.
        design, source, spec_payload = make_ready_design()
        outside = copy.deepcopy(spec_payload)
        outside["coverage_and_drape"]["sleeves"] = "Now finished with elbow-length sleeves."
        secret_note = "A very distinctive unlikely-to-collide note fragment 9f8e7d."
        provider = SequenceProvider([_result(outside), _result(outside)])

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(RefinementGenerationFailed):
                generate_refined_design_spec_for_design(
                    design,
                    source,
                    refinement_request("colour_story", secret_note),
                    provider=provider,
                )

        messages = [r.getMessage() for r in caplog.records]
        rejections = [m for m in messages if "refinement output rejected" in m]
        assert len(rejections) == 2, messages
        for message in rejections:
            # The reason, read from the exception's ATTRIBUTE.
            assert "reason=RefinementOutputRejected:disallowed_field_changed" in message
            assert "change_type=colour_story" in message
            # Not `attempt=`: pipeline.py logs the GenerationAttempt UUID under
            # that key, so three different things used to share one name.
            assert "provider_request=" in message
            assert "attempt=" not in message
        for message in messages:
            assert secret_note not in message
            # The offending VALUE never appears either — only the category does.
            assert "elbow-length sleeves" not in message


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


class TestRejectionDetailIsSafeForEveryCaughtType:
    """`_rejection_detail` reads `.category`/`.fields` by name, off exceptions
    from five different modules plus pydantic. Every one of them is safe today —
    both reviewers checked class by class — but nothing STRUCTURAL said so, and
    an implicit protocol nobody declared is how a seventh type opts itself into
    a log line by naming coincidence.

    So the set is not hand-maintained here: it is read out of the production
    `except` tuple with `ast`, the same technique this repository already uses to
    confine `django.core.mail`. Add a type to that tuple without deciding what it
    is safe to log, and this file fails."""

    # What each currently-caught type must produce. A value here is a promise
    # that the string is a source-controlled machine name — never model output,
    # user text, a rejected value or a questionnaire label.
    EXPECTED = {
        "ValidationError": (
            # Pydantic's. Carries the rejected INPUT in `.errors()`, which is
            # exactly why nothing here may read a message or an error list.
            lambda: _pydantic_validation_error(),
            "ValidationError",
        ),
        "UnsupportedDesignSpecVersion": (
            lambda: UnsupportedDesignSpecVersion("schema version 99 is not supported"),
            "UnsupportedDesignSpecVersion",
        ),
        "GeneratedContentRejected": (
            lambda: GeneratedContentRejected(RejectionCategory.DESIGNER_OR_BRAND),
            "GeneratedContentRejected:designer_or_brand_reference",
        ),
        "RefinementOutputRejected": (
            lambda: RefinementOutputRejected(RefinementOutputCategory.DISALLOWED_FIELD_CHANGED),
            "RefinementOutputRejected:disallowed_field_changed",
        ),
        "RefinedSelectionsInvalid": (
            lambda: RefinedSelectionsInvalid(["neckline_style", "fabrics"]),
            # Sorted question ids. Machine names in the schema (CLAUDE.md §7),
            # never the rejected value.
            "RefinedSelectionsInvalid:fabrics,neckline_style",
        ),
        "ImagePromptBuildError": (
            lambda: ImagePromptBuildError("the prompt could not be built"),
            "ImagePromptBuildError",
        ),
    }

    def _caught_type_names(self):
        """Every exception type caught by a handler that calls
        `_rejection_detail`, read out of the module's own source.

        Deliberately scoped to the WHOLE module rather than to one named
        function: the property under test is "nothing reaches
        `_rejection_detail` without a decided rendering", and that is true
        wherever the handler lives. Anchoring on the enclosing function name
        instead would break on a rename or on extracting the retry-loop body
        into a helper — refactors that change nothing about what is safe to
        log — and would fail with a message pointing at the test rather than at
        the code. It would also go quiet if a SECOND handler elsewhere started
        calling `_rejection_detail`, which is the case most worth catching."""
        source = pathlib.Path(refinement_service.__file__).read_text(encoding="utf-8")
        found = set()
        handlers = 0
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            if not any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Name)
                and c.func.id == "_rejection_detail"
                for c in ast.walk(node)
            ):
                # Includes the sibling `_NoChangeInAttempt` handler, which logs
                # its own line and must not be counted here.
                continue
            handlers += 1
            caught = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            found.update(n.id for n in caught if isinstance(n, ast.Name))
        assert handlers, "no except handler calls _rejection_detail — has it been removed?"
        return found

    def test_every_caught_type_has_a_decided_safe_rendering(self):
        assert self._caught_type_names() == set(self.EXPECTED), (
            "A type was added to or removed from the except tuple that feeds "
            "_rejection_detail. Decide what is safe to log for it and record "
            "that decision in EXPECTED — do not let it inherit the duck-typed "
            "behaviour unreviewed."
        )

    @pytest.mark.parametrize("name", sorted(EXPECTED))
    def test_the_rendering_is_the_decided_one(self, name):
        build, expected = self.EXPECTED[name]
        exc = build()
        assert type(exc).__name__ == name
        assert _rejection_detail(exc) == expected

    @pytest.mark.parametrize("name", sorted(EXPECTED))
    def test_the_rendering_never_carries_the_exception_message(self, name):
        # The one channel §15 forbids. `RefinementOutputRejected` and
        # `GeneratedContentRejected` both park their category in the message too,
        # so a message-reading implementation would pass the assertions above
        # while being wrong for the other four.
        build, _expected = self.EXPECTED[name]
        exc = build()
        message = str(exc)
        rendered = _rejection_detail(exc)
        assert message not in rendered
        for word in ("could not", "not supported", "rejected:", "not valid"):
            assert word not in rendered


def _pydantic_validation_error():
    """A real pydantic ValidationError, raised the way the service gets one."""
    try:
        validate_design_spec({"schema_version": 1})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


# The categories a version-1 spec can actually be refined in. Derived from the
# production dispatch, never hand-listed: `neckline` names no canonical field on
# version 1, so it is refused before any provider request is built and there is
# no transmitted message to assert about.
_V1_REFINABLE = tuple(
    change_type
    for change_type in REFINEMENT_CHANGE_TYPES
    if canonical_refinement_fields(change_type, DESIGN_SPEC_SCHEMA_VERSION)
)


class TestTheModelIsToldWhatItIsGradedAgainst:
    """The defect a live refinement hit twice.

    The output was checked against `refinement_allowed_paths(change_type,
    schema_version)` and the model was never shown it — asked instead to judge
    which fields were "relevant to the selected category" against a table only
    the server could see. A note that asked for two things at once, only one of
    them inside the chosen category, was applied in full and refused in full.
    Both attempts. Nothing saved.

    These assertions compare the message against the SAME call the grader makes,
    not against a copy of the list, so the two cannot drift apart."""

    def _sent(self, provider, index=0) -> dict:
        """The TRUSTED JSON block out of a built message.

        `raw_decode` from the first brace, not `index("{")` to `rindex("}")`:
        a note is appended after this block in its own JSON object, so a note
        containing a brace would move the last `}` into the untrusted section
        and this helper would parse the wrong span — or nothing at all.
        `test_a_note_containing_braces_does_not_confuse_the_reader` holds that
        down."""
        message = provider.requests[index].user_message
        trusted, _end = json.JSONDecoder().raw_decode(message, message.index("{"))
        return trusted

    @pytest.mark.parametrize("change_type", _V1_REFINABLE)
    def test_the_transmitted_allowlist_is_the_graded_one(self, change_type):
        design, source, spec_payload = make_ready_design()
        # An identical spec back, twice: the message is built, both attempts are
        # refused as no change, and the named exception is the ONLY one this may
        # raise. A bare `suppress(Exception)` here would hide a genuine error in
        # message assembly behind a request that had already been recorded.
        provider = SequenceProvider(
            [_result(copy.deepcopy(spec_payload)), _result(copy.deepcopy(spec_payload))]
        )
        with pytest.raises(RefinementNoChangeProduced):
            generate_refined_design_spec_for_design(
                design, source, refinement_request(change_type), provider=provider
            )
        sent = self._sent(provider)["editable_design_spec_paths"]
        graded = refinement_allowed_paths(change_type, DESIGN_SPEC_SCHEMA_VERSION)
        assert set(sent) == set(graded)
        assert sent == sorted(sent), "transmitted unsorted — the message is not deterministic"

    def test_the_allowlist_is_never_empty(self):
        # An empty list reads as "you may change nothing", which is why the
        # argument is required rather than defaulted to ().
        design, source, spec_payload = make_ready_design()
        provider = SequenceProvider(
            [_result(copy.deepcopy(spec_payload)), _result(copy.deepcopy(spec_payload))]
        )
        with pytest.raises(RefinementNoChangeProduced):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )
        assert self._sent(provider)["editable_design_spec_paths"]

    def test_a_note_containing_braces_does_not_confuse_the_reader(self):
        # The note is appended in its own JSON object after the trusted block, so
        # a brace in it moves the message's LAST `}` out of the block these
        # assertions read. A real customer writes "make it {like this}".
        design, source, spec_payload = make_ready_design()
        provider = SequenceProvider(
            [_result(copy.deepcopy(spec_payload)), _result(copy.deepcopy(spec_payload))]
        )
        with pytest.raises(RefinementNoChangeProduced):
            generate_refined_design_spec_for_design(
                design,
                source,
                refinement_request("colour_story", "make it {like this} please"),
                provider=provider,
            )
        trusted = self._sent(provider)
        assert trusted["change_type"] == "colour_story"
        assert set(trusted["editable_design_spec_paths"]) == set(
            refinement_allowed_paths("colour_story", DESIGN_SPEC_SCHEMA_VERSION)
        )
        # And the note is still where it belongs — outside the trusted block.
        assert "like this" not in json.dumps(trusted)

    def test_a_rejection_retries_with_the_correction_for_that_reason(self):
        # The customer's own failure: an out-of-category path changed. The retry
        # used to name four possible causes at once; it now names the one.
        design, source, spec_payload = make_ready_design()
        outside = copy.deepcopy(spec_payload)
        outside["coverage_and_drape"]["sleeves"] = "Now finished with elbow-length sleeves."
        provider = SequenceProvider([_result(outside), _result(outside)])

        with pytest.raises(RefinementGenerationFailed):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )

        assert len(provider.requests) == 2
        first = provider.requests[0].user_message
        retry = provider.requests[1].user_message
        for note in REFINEMENT_RETRY_NOTES.values():
            assert note not in first, "a first attempt must carry no correction"
        assert REFINEMENT_RETRY_NOTES[RETRY_DISALLOWED_FIELD] in retry
        assert REFINEMENT_RETRY_NOTE not in retry
        assert REFINEMENT_RETRY_NOTES[RETRY_NO_CHANGE] not in retry

    def test_an_inert_attempt_retries_with_the_no_change_correction(self):
        design, source, spec_payload = make_ready_design()
        inert = apply_narrative_only_edit(spec_payload, "colour_story")
        provider = SequenceProvider([_result(inert), _result(copy.deepcopy(inert))])

        with pytest.raises(RefinementNoChangeProduced):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )

        retry = provider.requests[1].user_message
        assert REFINEMENT_RETRY_NOTES[RETRY_NO_CHANGE] in retry
        assert REFINEMENT_RETRY_NOTES[RETRY_DISALLOWED_FIELD] not in retry

    def test_a_safety_rejection_gets_the_generic_correction(self):
        # Deliberate: naming the safety check invites the model to word its way
        # around the denylist on the one retry it has.
        design, source, spec_payload = make_ready_design()
        blocked = copy.deepcopy(spec_payload)
        blocked["colour_story"]["palette_summary"] = "Style it the way Sabyasachi would."
        provider = SequenceProvider([_result(blocked), _result(copy.deepcopy(blocked))])

        with pytest.raises(RefinementGenerationFailed):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story"), provider=provider
            )

        retry = provider.requests[1].user_message
        assert REFINEMENT_RETRY_NOTE in retry
        for note in REFINEMENT_RETRY_NOTES.values():
            assert note not in retry

    def test_every_rejection_category_has_a_decided_correction(self):
        # Total by construction, so a fifth category must choose an instruction
        # rather than silently inherit the generic one.
        assert set(_REJECTION_RETRY_REASONS) == set(RefinementOutputCategory)
        for reason in _REJECTION_RETRY_REASONS.values():
            assert reason in REFINEMENT_RETRY_NOTES

    def test_a_correction_never_carries_the_note(self):
        design, source, spec_payload = make_ready_design()
        outside = copy.deepcopy(spec_payload)
        outside["coverage_and_drape"]["sleeves"] = "Now finished with elbow-length sleeves."
        secret = "an unmistakable fragment 4b2c1a"
        provider = SequenceProvider([_result(outside), _result(outside)])

        with pytest.raises(RefinementGenerationFailed):
            generate_refined_design_spec_for_design(
                design, source, refinement_request("colour_story", secret), provider=provider
            )

        # The note belongs in the delimited untrusted section and nowhere else —
        # least of all inside a source-controlled correction instruction.
        retry = provider.requests[1].user_message
        correction = retry[retry.index(REFINEMENT_UNTRUSTED_END) :]
        assert secret not in correction
        assert "elbow-length sleeves" not in correction
