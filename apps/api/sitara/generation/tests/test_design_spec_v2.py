"""Version-aware DesignSpec dispatch and the dedicated canonical neckline.

Phase 16B introduces DesignSpec schema version 2, which adds
``source_selections.neckline_style``. Version 1 stays fully supported and
byte-identical; validation dispatches on the persisted ``schema_version`` and
an unknown/malformed version fails safely.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sitara.generation.design_spec import (
    SUPPORTED_DESIGN_SPEC_SCHEMA_VERSIONS,
    DesignSpec,
    DesignSpecV2,
    UnsupportedDesignSpecVersion,
    design_spec_model_for_version,
    validate_design_spec,
)
from sitara.generation.fixture_provider import FixtureStructuredDesignProvider
from sitara.generation.prompt_builder import build_image_prompt
from sitara.generation.refinement import STYLING_DETAILS
from sitara.generation.refinement_service import (
    RefinementOutputCategory,
    RefinementOutputRejected,
    _validate_refined_output,
)
from sitara.generation.services import generate_design_spec_for_design

from .factory import COMPLETE_ANSWERS_V3, make_complete_v3_design

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "prompt_builder"


def _v1_spec_dict() -> dict:
    return json.loads((_FIXTURE_DIR / "nikah_lehenga_head_drape.json").read_text(encoding="utf-8"))


def _v2_spec_dict() -> dict:
    return json.loads(
        (_FIXTURE_DIR / "anand_karaj_lehenga_satin_high_neck.json").read_text(encoding="utf-8")
    )


class TestVersionDispatch:
    def test_supported_versions_are_one_and_two(self):
        assert sorted(SUPPORTED_DESIGN_SPEC_SCHEMA_VERSIONS) == [1, 2]

    def test_v1_payload_validates_as_version_one(self):
        spec = validate_design_spec(_v1_spec_dict())
        assert spec.schema_version == 1
        assert not hasattr(spec.source_selections, "neckline_style")

    def test_v2_payload_validates_with_a_canonical_neckline(self):
        spec = validate_design_spec(_v2_spec_dict())
        assert isinstance(spec, DesignSpecV2)
        assert spec.schema_version == 2
        assert spec.source_selections.neckline_style == "high_neck"

    @pytest.mark.parametrize("bad", [0, 3, 99, "1", 1.0, True, None])
    def test_unsupported_version_fails_safely(self, bad):
        with pytest.raises(UnsupportedDesignSpecVersion):
            design_spec_model_for_version(bad)

    @pytest.mark.parametrize("bad", [None, [], "x", 42])
    def test_non_object_payload_fails_safely(self, bad):
        with pytest.raises(UnsupportedDesignSpecVersion):
            validate_design_spec(bad)

    def test_unknown_version_payload_fails_safely(self):
        payload = _v2_spec_dict()
        payload["schema_version"] = 99
        with pytest.raises(UnsupportedDesignSpecVersion):
            validate_design_spec(payload)

    def test_v1_rejects_a_neckline_field(self):
        payload = _v1_spec_dict()
        payload["source_selections"]["neckline_style"] = "high_neck"
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(payload)

    def test_v2_requires_the_neckline_field(self):
        payload = _v2_spec_dict()
        del payload["source_selections"]["neckline_style"]
        with pytest.raises(ValidationError):
            DesignSpecV2.model_validate(payload)

    def test_v2_no_preference_neckline_is_null(self):
        payload = _v2_spec_dict()
        payload["source_selections"]["neckline_style"] = None
        spec = validate_design_spec(payload)
        assert spec.source_selections.neckline_style is None


class TestCanonicalNecklinePrompt:
    def test_canonical_neckline_leads_the_coverage_directive(self):
        prompt = build_image_prompt(validate_design_spec(_v2_spec_dict()))
        # The high-neck canonical clause appears in the leading coverage
        # directive and the closing reinforcement.
        assert "fully closed high neckline covering the collarbone" in prompt
        assert "closed high neckline" in prompt.rsplit("\n\n", 1)[-1]

    def test_generated_neckline_narrative_is_suppressed_for_a_canonical_neckline(self):
        # The v2 sweetheart fixture's narrative says "scooped"; the canonical
        # neckline must win and the contradicting narrative must not appear.
        payload = json.loads(
            (_FIXTURE_DIR / "walima_saree_sweetheart.json").read_text(encoding="utf-8")
        )
        prompt = build_image_prompt(validate_design_spec(payload))
        assert "sweetheart neckline" in prompt
        assert "scooped" not in prompt.lower()
        # The model-authored "Neckline:" narrative line is dropped entirely.
        assert "Neckline:" not in prompt

    def test_satin_and_new_colour_render_from_canonical_values(self):
        prompt = build_image_prompt(validate_design_spec(_v2_spec_dict()))
        assert "satin" in prompt.lower()
        assert "ruby" in prompt.lower()

    def test_v1_spec_still_renders_without_a_canonical_neckline_clause(self):
        # A v1 spec keeps its generated neckline narrative (unchanged behaviour).
        prompt = build_image_prompt(validate_design_spec(_v1_spec_dict()))
        assert "Neckline:" in prompt


class TestRefinementCannotContradictCanonicalNeckline:
    """A refinement of a v2 spec can never change the canonical neckline: it
    lives in ``source_selections``, which is immutable across every refinement
    category, and the structure version itself is pinned."""

    def _v2_source_spec(self):
        # A clean v2 source: the prompt-builder fixture narrative contains
        # "as requested" (a refinement-process phrase), harmless for prompt
        # tests but not permitted in a refinement OUTPUT — strip it here so the
        # process-leakage guard only reacts to genuine changes.
        payload = _v2_spec_dict()
        payload["coverage_and_drape"]["sleeves"] = "Full-length fitted sleeves reaching the wrists."
        return validate_design_spec(payload)

    def test_allowed_narrative_refinement_of_a_v2_spec_passes(self):
        source = self._v2_source_spec()
        refined = source.model_dump(mode="json")
        refined["styling_notes"] = ["A fresh, distinct styling suggestion for local review."]
        spec = _validate_refined_output(refined, source, STYLING_DETAILS)
        assert spec.schema_version == 2
        assert spec.source_selections.neckline_style == "high_neck"

    def test_changing_the_canonical_neckline_is_rejected(self):
        source = self._v2_source_spec()
        refined = source.model_dump(mode="json")
        refined["source_selections"]["neckline_style"] = "deep_v_neck"
        with pytest.raises(RefinementOutputRejected) as excinfo:
            _validate_refined_output(refined, source, STYLING_DETAILS)
        assert excinfo.value.category == RefinementOutputCategory.SOURCE_SELECTIONS_CHANGED

    def test_downgrading_the_schema_version_is_rejected(self):
        source = self._v2_source_spec()
        refined = source.model_dump(mode="json")
        refined["schema_version"] = 1
        del refined["source_selections"]["neckline_style"]
        with pytest.raises(RefinementOutputRejected) as excinfo:
            _validate_refined_output(refined, source, STYLING_DETAILS)
        assert excinfo.value.category == RefinementOutputCategory.IMMUTABLE_FIELD_CHANGED


@pytest.mark.django_db
class TestV3GenerationEchoesCanonicalValues:
    def test_v3_design_produces_a_v2_designversion_with_canonical_values(self):
        design = make_complete_v3_design()
        version = generate_design_spec_for_design(
            design, provider=FixtureStructuredDesignProvider()
        )
        assert version.design_spec_schema_version == 2
        ss = version.design_spec["source_selections"]
        # Every canonical answer echoed exactly, including the new ones.
        assert ss["neckline_style"] == "high_neck"
        assert ss["ceremony"] == "anand_karaj"
        assert ss["fabrics"] == ["satin", "organza"]
        assert ss["colour_palette"] == ["ruby", "gold"]

    def test_v3_no_preference_neckline_persists_as_null(self):
        answers = dict(COMPLETE_ANSWERS_V3)
        answers.pop("neckline_style", None)
        design = make_complete_v3_design(answers=answers)
        version = generate_design_spec_for_design(
            design, provider=FixtureStructuredDesignProvider()
        )
        assert version.design_spec_schema_version == 2
        assert version.design_spec["source_selections"]["neckline_style"] is None
