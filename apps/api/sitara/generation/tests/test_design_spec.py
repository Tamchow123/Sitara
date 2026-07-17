"""The strict DesignSpec Pydantic contract."""

import copy

import pytest
from pydantic import ValidationError

from sitara.generation.design_spec import (
    DESIGN_SPEC_SCHEMA_VERSION,
    DesignSpec,
    SourceSelections,
)

from .utils import VALID_FIXTURES, a_valid_spec_dict, load_spec_dict


class TestValidFixtures:
    @pytest.mark.parametrize("name", VALID_FIXTURES)
    def test_recorded_fixtures_parse(self, name):
        spec = DesignSpec.model_validate(load_spec_dict(name))
        assert spec.schema_version == DESIGN_SPEC_SCHEMA_VERSION

    def test_roundtrip_is_stable(self):
        spec = DesignSpec.model_validate(a_valid_spec_dict())
        again = DesignSpec.model_validate(spec.model_dump(mode="json"))
        assert again.model_dump(mode="json") == spec.model_dump(mode="json")

    def test_ordered_lists_preserve_submitted_order(self):
        data = a_valid_spec_dict()
        data["source_selections"]["colour_palette"] = ["gold", "ivory"]
        spec = DesignSpec.model_validate(data)
        assert spec.source_selections.colour_palette == ["gold", "ivory"]


class TestStrictness:
    def test_extra_top_level_field_is_rejected(self):
        data = a_valid_spec_dict()
        data["unexpected"] = "nope"
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)

    def test_extra_source_selection_field_is_rejected(self):
        data = a_valid_spec_dict()
        data["source_selections"]["designer"] = "someone"
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)

    def test_extra_nested_field_is_rejected(self):
        data = a_valid_spec_dict()
        data["garment_breakdown"]["extra"] = "x"
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)

    def test_boolean_is_not_accepted_as_schema_version(self):
        data = a_valid_spec_dict()
        data["schema_version"] = True
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)

    def test_wrong_schema_version_rejected(self):
        data = a_valid_spec_dict()
        data["schema_version"] = 2
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)


class TestBounds:
    def test_title_too_short(self):
        data = a_valid_spec_dict()
        data["title"] = "ab"
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)

    def test_title_too_long(self):
        data = a_valid_spec_dict()
        data["title"] = "x" * 121
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)

    def test_concept_summary_below_minimum(self):
        data = a_valid_spec_dict()
        data["concept_summary"] = "too short"
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)

    def test_image_alt_text_below_minimum(self):
        data = a_valid_spec_dict()
        data["image_alt_text"] = "short alt"
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)

    def test_construction_caveats_must_be_non_empty(self):
        data = a_valid_spec_dict()
        data["construction_caveats"] = []
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)

    def test_narrative_list_over_maximum(self):
        data = a_valid_spec_dict()
        data["styling_notes"] = ["a note"] * 9
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)

    def test_narrative_item_over_maximum(self):
        data = a_valid_spec_dict()
        data["styling_notes"] = ["x" * 401]
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)

    def test_fabrics_and_texture_must_be_non_empty(self):
        data = a_valid_spec_dict()
        data["fabrics_and_texture"] = []
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)


class TestSourceSelectionsStrict:
    def test_machine_value_pattern_enforced(self):
        with pytest.raises(ValidationError):
            SourceSelections.model_validate(
                {
                    "garment_type": "Not A Machine Value",
                    "ceremony": "nikah",
                    "regional_style": None,
                    "silhouette": "flared_lehenga",
                    "colour_palette": ["ivory"],
                    "fabrics": [],
                    "embellishment_styles": ["zardozi"],
                    "embellishment_density": None,
                    "coverage_preferences": [],
                    "dupatta_style": None,
                    "saree_drape": None,
                }
            )

    def test_optional_fields_may_be_null(self):
        selections = SourceSelections.model_validate(
            {
                "garment_type": "lehenga",
                "ceremony": "nikah",
                "regional_style": None,
                "silhouette": "flared_lehenga",
                "colour_palette": ["ivory"],
                "fabrics": [],
                "embellishment_styles": ["zardozi"],
                "embellishment_density": None,
                "coverage_preferences": [],
                "dupatta_style": None,
                "saree_drape": None,
            }
        )
        assert selections.regional_style is None
        assert selections.fabrics == []

    def test_colour_palette_must_be_non_empty(self):
        data = a_valid_spec_dict()
        data["source_selections"]["colour_palette"] = []
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)


class TestMalformedShapes:
    @pytest.mark.parametrize(
        "path,value",
        [
            (["source_selections"], "a string, not an object"),
            (["garment_breakdown"], []),
            (["fabrics_and_texture"], {"not": "a list"}),
            (["colour_story", "palette_summary"], 5),
            (["source_selections", "colour_palette"], "ivory"),
        ],
    )
    def test_malformed_nested_shapes_rejected(self, path, value):
        data = a_valid_spec_dict()
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(data)

    def test_no_incidental_exception_on_wildly_wrong_input(self):
        for bad in [None, "string", 5, [], {"schema_version": 1}]:
            with pytest.raises(ValidationError):
                DesignSpec.model_validate(bad if isinstance(bad, dict) else copy.copy({"x": bad}))
