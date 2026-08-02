"""The provider-facing schema stripper (no network).

The bug this guards against: DesignSpec v3's compiled grammar exceeded
Anthropic's structured-output limit, so every live structured request failed
with ``invalid_request_error: The compiled grammar is too large``. No test could
catch it, because tests never call a provider and demo mode builds its
DesignSpec locally — so the guard here is on the SCHEMA's shape instead.
"""

import pytest
from pydantic import BaseModel, Field

from sitara.ai_gateway.output_schema import STRIPPED_KEYWORDS, provider_output_schema
from sitara.generation.design_spec import design_spec_model_for_version


def _keywords_present(node) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in STRIPPED_KEYWORDS:
                found.add(key)
            found |= _keywords_present(value)
    elif isinstance(node, list):
        for item in node:
            found |= _keywords_present(item)
    return found


class _Nested(BaseModel):
    tag: str = Field(pattern="^[a-z]+$")


class _Sample(BaseModel):
    title: str = Field(min_length=3, max_length=120, description="The title.")
    notes: list[str] = Field(min_length=1, max_length=8)
    nested: _Nested
    optional: str | None = Field(default=None, max_length=50)
    count: int = Field(ge=1, le=10)


class TestStripping:
    @pytest.mark.parametrize("version", [1, 2, 3])
    def test_every_live_schema_version_compiles_free_of_size_constraints(self, version):
        """The regression guard: this is what made v3 unsendable."""
        schema = provider_output_schema(design_spec_model_for_version(version))
        assert _keywords_present(schema) == set()

    def test_the_unstripped_v3_schema_really_does_carry_them(self):
        # Proves the test above is not vacuous — v3 genuinely has the keywords
        # that had to be removed.
        raw = design_spec_model_for_version(3).model_json_schema()
        assert _keywords_present(raw), "v3 was expected to carry size constraints"

    def test_nested_models_and_arrays_are_stripped_too(self):
        schema = provider_output_schema(_Sample)
        assert _keywords_present(schema) == set()

    def test_shape_is_preserved(self):
        schema = provider_output_schema(_Sample)
        properties = schema["properties"]
        assert set(properties) == {"title", "notes", "nested", "optional", "count"}
        assert properties["title"]["type"] == "string"
        assert properties["notes"]["type"] == "array"
        assert set(schema["required"]) == {"title", "notes", "nested", "count"}
        # Nested model definitions survive by reference, not by inlining.
        assert "$defs" in schema

    def test_numeric_bounds_are_kept(self):
        # ge/le compile cheaply and carry real meaning; only the size-and-shape
        # keywords are removed.
        count = provider_output_schema(_Sample)["properties"]["count"]
        assert count["minimum"] == 1
        assert count["maximum"] == 10

    def test_stripped_bounds_are_restated_in_the_description(self):
        properties = provider_output_schema(_Sample)["properties"]
        # Appended to an existing description rather than replacing it.
        assert properties["title"]["description"].startswith("The title.")
        assert "3-120 characters" in properties["title"]["description"]
        # Created where there was none.
        assert "1-8 items" in properties["notes"]["description"]
        assert "^[a-z]+$" in provider_output_schema(_Nested)["properties"]["tag"]["description"]

    @pytest.mark.parametrize("version", [1, 2, 3])
    def test_the_echoed_field_is_not_asked_for(self, version):
        """source_selections is the caller's own input, not the model's job.

        It is a flat object of ~20 optional fields, so its grammar has to accept
        every subset in every order — measured against the live API it exceeds
        the limit on its own, while the rest of v3 is accepted comfortably.
        """
        schema = provider_output_schema(design_spec_model_for_version(version))
        assert "source_selections" not in schema["properties"]
        assert "source_selections" not in schema.get("required", [])
        # Its definition goes too, rather than lingering as dead weight.
        assert not any(name.startswith("SourceSelections") for name in schema.get("$defs", {}))

    def test_everything_else_survives_the_removal(self):
        raw = design_spec_model_for_version(3).model_json_schema()
        schema = provider_output_schema(design_spec_model_for_version(3))
        expected = set(raw["properties"]) - {"source_selections"}
        assert set(schema["properties"]) == expected
        # The creative fields the model actually has to produce are all present.
        for field in ("title", "concept_summary", "construction_caveats", "image_alt_text"):
            assert field in schema["properties"]

    def test_definitions_still_referenced_are_kept(self):
        schema = provider_output_schema(design_spec_model_for_version(3))
        refs = set()

        def walk(node):
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/$defs/"):
                    refs.add(ref.removeprefix("#/$defs/"))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk({k: v for k, v in schema.items() if k != "$defs"})
        # Pruning must never remove a definition something still points at.
        assert refs <= set(schema.get("$defs", {}))

    def test_is_pure_and_repeatable(self):
        model = design_spec_model_for_version(3)
        before = model.model_json_schema()
        first = provider_output_schema(model)
        second = provider_output_schema(model)
        assert first == second
        # The model's own schema is untouched: strict validation elsewhere must
        # still see the constraints.
        assert model.model_json_schema() == before
        assert _keywords_present(before)
