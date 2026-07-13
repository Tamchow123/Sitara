"""Prompt rendering: determinism, capability gating, positive-only wording."""

import pytest

from conftest import make_brief, make_refinement_brief
from model_eval.prompt_formats import (
    CONTROLLED_EXCLUSIONS,
    FORMAT_EDITORIAL,
    FORMAT_EDITORIAL_NEGATIVE,
    FORMAT_JSON,
    FORMAT_SECTIONED,
    PromptFormatError,
    apply_refinement,
    formats_for,
    render_edit_instruction,
    render_prompt,
    unsupported_formats,
)

NEGATIVE_PHRASES = ["no text", "no logos", "no watermarks", "without text"]


class TestFormatSelection:
    def test_plain_model_gets_only_positive_text_formats(self, plain_candidate):
        assert formats_for(plain_candidate, "auto") == [FORMAT_EDITORIAL, FORMAT_SECTIONED]

    def test_json_only_when_supported(self, plain_candidate, reffy_candidate):
        assert FORMAT_JSON not in formats_for(plain_candidate, "auto")
        assert FORMAT_JSON in formats_for(reffy_candidate, "auto")

    def test_negative_variant_only_for_negative_capable_models(
        self, plain_candidate, negative_candidate
    ):
        assert FORMAT_EDITORIAL_NEGATIVE not in formats_for(plain_candidate, "auto")
        assert FORMAT_EDITORIAL_NEGATIVE in formats_for(negative_candidate, "auto")

    def test_explicit_unsupported_request_reported_not_silently_run(self, plain_candidate):
        requested = [FORMAT_EDITORIAL, FORMAT_JSON]
        assert formats_for(plain_candidate, requested) == [FORMAT_EDITORIAL]
        assert unsupported_formats(plain_candidate, requested) == [FORMAT_JSON]


class TestRendering:
    def test_rendering_is_deterministic(self, plain_candidate):
        brief = make_brief(inspiration_metadata={"fabric": "silk", "colour_palette": "red"})
        for fmt in (FORMAT_EDITORIAL, FORMAT_SECTIONED):
            for mode in ("text_only", "metadata"):
                a = render_prompt(brief, fmt, mode, plain_candidate.capabilities)
                b = render_prompt(brief, fmt, mode, plain_candidate.capabilities)
                assert a == b

    def test_positive_only_formats_have_no_negative_phrasing(self, plain_candidate):
        brief = make_brief()
        for fmt in (FORMAT_EDITORIAL, FORMAT_SECTIONED):
            rendered = render_prompt(brief, fmt, "text_only", plain_candidate.capabilities)
            assert rendered.negative_text is None
            text = (rendered.text or "").lower()
            for phrase in NEGATIVE_PHRASES:
                assert phrase not in text
            # Positive presentation vocabulary instead:
            assert "unbranded" in text
            assert "modest full-coverage" in text
            assert "anatomically coherent fingers" in text

    def test_exclusions_only_via_dedicated_negative_param(self, negative_candidate, plain_candidate):
        brief = make_brief()
        rendered = render_prompt(
            brief, FORMAT_EDITORIAL_NEGATIVE, "text_only", negative_candidate.capabilities
        )
        assert rendered.negative_text is not None
        for term in CONTROLLED_EXCLUSIONS:
            assert term in rendered.negative_text
            # Never leaked into the positive prompt:
            assert f"no {term}" not in (rendered.text or "").lower()
        with pytest.raises(PromptFormatError):
            render_prompt(brief, FORMAT_EDITORIAL_NEGATIVE, "text_only", plain_candidate.capabilities)

    def test_json_format_gated_and_structured(self, reffy_candidate, plain_candidate):
        brief = make_brief()
        rendered = render_prompt(brief, FORMAT_JSON, "text_only", reffy_candidate.capabilities)
        assert rendered.json_payload is not None
        assert rendered.json_payload["garment"]["type"] == "South Asian bridal lehenga"
        assert rendered.text is None
        # Serialised deterministically for the provider:
        assert rendered.as_provider_input() == rendered.as_provider_input()
        with pytest.raises(PromptFormatError):
            render_prompt(brief, FORMAT_JSON, "text_only", plain_candidate.capabilities)

    def test_metadata_mode_adds_curated_cues_and_reference_mode_matches_text_only(
        self, plain_candidate
    ):
        brief = make_brief(
            inspiration_metadata={"embroidery": "kamdani", "colour_palette": "rose gold"},
            reference_ids=["ref-x"],
        )
        text_only = render_prompt(brief, FORMAT_EDITORIAL, "text_only", plain_candidate.capabilities)
        metadata = render_prompt(brief, FORMAT_EDITORIAL, "metadata", plain_candidate.capabilities)
        reference = render_prompt(
            brief, FORMAT_EDITORIAL, "reference_image", plain_candidate.capabilities
        )
        assert "kamdani" in (metadata.text or "")
        assert "curated catalogue metadata" in (metadata.text or "")
        assert "kamdani" not in (text_only.text or "")
        # Controlled comparison: reference mode changes the attachment, not the words.
        assert reference.text == text_only.text


class TestRefinement:
    def test_apply_refinement_changes_exactly_one_field(self):
        brief = make_refinement_brief()
        refined = apply_refinement(brief, brief.refinement)
        assert refined.palette == "deep red and gold"
        base = brief.model_dump(exclude={"palette", "refinement"})
        assert refined.model_dump(exclude={"palette", "refinement"}) == base

    def test_edit_instruction_asks_for_preservation(self):
        brief = make_refinement_brief()
        instruction = render_edit_instruction(brief, brief.refinement)
        assert "Preserve every unspecified detail" in instruction
        assert "ivory" in instruction and "deep red" in instruction
