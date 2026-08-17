"""Phase 23 §1-§2: what a refinement can and cannot change in the image prompt.

This module is the phase's spine. It exists because two individually-correct
decisions produced an empty intersection:

- Phase 14 (ADR 0015) allowlisted only NARRATIVE DesignSpec paths for a
  refinement and froze ``source_selections`` outright;
- prompt builder 8.0.0 inverted the balance the other way, rendering canonical
  selections as the skeleton and dropping most model-authored prose.

So under questionnaire v4 / DesignSpec v3 — the current shape — a refinement
that may change only narrative cannot move the prompt in the field the user
actually named.

Measured against the fully-answered v4 concept below, the position is worse
than §2 estimated. Mandatory canonical content is 1,399 characters against an
``IMAGE_PROMPT_TARGET_CHARS`` of 1,500, so the whole narrative budget is 53
characters and exactly ONE generated slot renders at all — the 39-character
embellishment placement. Three of the eight categories are dead by
construction (nothing in their allowlist is rendered for any spec); on this
concept three more are dead by budget as well, and the two that do move the
prompt move only that embellishment-placement clause, which is not what either
of them is named after.

Part A measures that and keeps measuring it. It is NOT a bug report the fix
deletes: after Phase 23 a narrative-only refinement still changes nothing here,
which is precisely why the canonical path in Part B had to be added. Its
assertions are deliberately budget-INDEPENDENT where they can be — a canonical
clause is built from ``source_selections`` alone and can never move for a
narrative edit, whatever the budget does. Do not weaken either part to make a
change pass.
"""

import copy

import pytest

from sitara.generation.design_spec import validate_design_spec
from sitara.generation.prompt_builder import build_image_prompt
from sitara.generation.refinement import (
    REFINEMENT_ALLOWED_PATHS,
    diff_design_spec_paths,
    normalise_refinement_request,
    path_is_allowed,
)
from sitara.generation.refinement_service import generate_refined_design_spec_for_design

from .factory import make_complete_v4_design, make_source_version
from .fakes import SequenceProvider, payload_result

# --- the source concept ----------------------------------------------------
#
# Written out in full here rather than loaded from the prompt-builder snapshot
# fixtures: those exist to pin the BUILDER's output and may be re-curated, and a
# characterisation of the refinement contract must not move when they do. Every
# coverage area is answered on purpose — that is the questionnaire v4 shape §2
# reasons about, and it is what suppresses each coverage narrative slot.
SOURCE_SPEC: dict = {
    "schema_version": 3,
    "source_selections": {
        "garment_type": "lehenga",
        "ceremony": "walima",
        "regional_style": "hyderabadi",
        "silhouette": "panelled_kali_lehenga",
        "fabric_colour": "aubergine",
        "embroidery_colour": "silver_grey",
        "dupatta_colour": "match_fabric",
        "custom_colours": [],
        "fabrics": ["velvet", "net"],
        "embellishment_styles": ["zardozi", "crystals"],
        "embellishment_density": "balanced",
        "neckline_style": "sweetheart_neck",
        "sleeves": "full_sleeve",
        "back_coverage": "modest_back",
        "midriff": "semi_sheer_midriff",
        "head_covering": "dupatta_over_head",
        "dupatta_style": "head_drape",
        "saree_drape": None,
    },
    "title": "Aubergine kali lehenga for a walima",
    "concept_summary": (
        "A poised walima concept in an aubergine panelled kali lehenga whose vertical "
        "panels swing as they flare. Silver-grey zardozi and crystal work catch the "
        "light along the panel seams, and a matching aubergine dupatta is drawn over "
        "the head before falling behind the shoulders."
    ),
    "garment_breakdown": {
        "overall_form": "A fitted choli over a kali-panelled lehenga skirt with a long dupatta.",
        "garment_components": ["Fitted choli", "Panelled kali lehenga skirt", "Dupatta"],
        "silhouette": "Vertical tapering panels that hold close at the waist and swing wide.",
        "drape_or_layering": "The dupatta is taken over the head and falls behind the shoulders.",
        "key_proportions": "A close bodice and defined waist read against the panelled skirt.",
    },
    "colour_story": {
        "palette_summary": "A deep aubergine ground worked in cool silver grey.",
        "placement": (
            "Aubergine carries the choli, skirt and dupatta alike, with silver grey drawn "
            "along the panel seams."
        ),
        "rationale": "A single deep tone keeps the panelled skirt reading as one sweep.",
    },
    "fabrics_and_texture": [
        {
            "fabric": "Velvet",
            "placement": "Choli and kali panels",
            "finish_and_movement": "A dense pile that deepens the aubergine and adds weight.",
        },
        {
            "fabric": "Net",
            "placement": "Dupatta and midriff panel",
            "finish_and_movement": "A fine open mesh that veils rather than covers.",
        },
    ],
    "embellishment_plan": {
        "techniques": ["Zardozi metal-thread work", "Crystal embellishment"],
        "density": "A balanced amount of work following the panel seams and hem.",
        "placement": ["Panel seams", "Skirt hem"],
        "motifs": ["Fine trailing vines", "Scattered crystal clusters"],
        "restraint_notes": "Plain velvet is left between the seams so the panels stay readable.",
    },
    "coverage_and_drape": {
        "sleeves": "Full-length sleeves reaching the wrists.",
        "neckline": "A sweetheart neckline curved like the top of a heart.",
        "back_and_midriff": "A covered back with the waist veiled in sheer net.",
        "head_covering": "The dupatta is drawn up over the head.",
        "dupatta_or_saree_drape": "A matching aubergine dupatta taken over the head.",
    },
    "cultural_context": {
        "regional_direction": "A broad Hyderabadi bridal influence guides the deep tone.",
        "interpretation_notes": ["Offered as one broad direction rather than a rule."],
        "safeguards": ["No family custom is presented as the only correct walima approach."],
    },
    "styling_notes": [
        "Cool-toned jewellery echoes the silver work.",
        "Keep the dupatta clear of the panel seams.",
    ],
    "construction_caveats": [
        "This is a concept visualisation, not a sewing pattern, and includes no measurements.",
        "It does not guarantee that the garment can be constructed exactly as shown.",
    ],
    "image_alt_text": (
        "A model in an aubergine panelled kali lehenga with silver-grey zardozi, a "
        "sweetheart neckline and a matching dupatta drawn over the head."
    ),
}

# Appended to every narrative string a category is allowed to rewrite. Short
# enough that no schema bound is crossed, distinctive enough that a rendered
# slot cannot coincidentally match, and sentence-terminated so
# ``_truncate_at_sentence`` never has to discard the piece.
_REWORDED = " Reworded for this characterisation."


def _rewrite_strings(value):
    """Every string leaf of ``value``, deterministically reworded."""
    if isinstance(value, str):
        return value + _REWORDED
    if isinstance(value, list):
        return [_rewrite_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_strings(item) for key, item in value.items()}
    return value


def narrative_only_refinement(change_type: str) -> dict:
    """The MAXIMAL legal narrative-only refinement for ``change_type``.

    Every path the category's allowlist admits is rewritten — nothing is left
    unchanged that the category is permitted to change — so an empty prompt diff
    afterwards is a statement about the category, not about a timid edit."""
    refined = copy.deepcopy(SOURCE_SPEC)
    for path in sorted(REFINEMENT_ALLOWED_PATHS[change_type]):
        if path.startswith("source_selections"):
            continue
        target = refined
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = _rewrite_strings(target[parts[-1]])
    return refined


def _prompt(payload: dict) -> str:
    return build_image_prompt(validate_design_spec(payload))


def _sentences(prompt: str) -> list[str]:
    return prompt.replace("\n\n", " ").split(". ")


def _clause(prompt: str, needle: str) -> str:
    """The single sentence of ``prompt`` containing ``needle`` — so an assertion
    can pin one canonical clause without pinning the whole prompt."""
    matches = [part for part in _sentences(prompt) if needle in part]
    assert len(matches) == 1, f"expected exactly one clause containing {needle!r}"
    return matches[0]


def _drop_clause(prompt: str, needle: str) -> str:
    """``prompt`` with every sentence mentioning ``needle`` removed, so two
    prompts can be compared for everything EXCEPT one named slot."""
    return " | ".join(part for part in _sentences(prompt) if needle not in part)


# --- Part A: a narrative-only refinement, category by category --------------
#
# The categories whose ENTIRE narrative allowlist is unrendered for a
# fully-answered v4 concept, whatever the narrative budget does.
# `dupatta_or_saree_drape`: head_covering is dropped for every version, the drape
# narrative is replaced by the canonical clause and drape_or_layering is not
# rendered. `sleeves_and_coverage`: both narrative slots are suppressed by the
# canonical answers and garment_components is not rendered.
#
# `styling_details` was the third and is absent because ADR 0028 retired it: not
# one of its five paths was rendered for any spec at all, and it named no
# canonical selection to fall back on. See TestTheRetiredCategory below.
PROMPT_DEAD_CATEGORIES = ("dupatta_or_saree_drape", "sleeves_and_coverage")

# The canonical clause each remaining category is NAMED AFTER. Every one is built
# from source_selections alone, so a narrative-only refinement can never move it
# — which is the whole defect, stated as an invariant rather than a measurement.
# `styling_details` is absent because it names no canonical field at all, which
# is why Phase 23 retires it.
CANONICAL_CLAUSE_PER_CATEGORY = {
    "colour_story": "Colours:",
    "fabric_and_texture": "Fabrics:",
    "embellishment": "Embellishment density:",
    "neckline": "a sweetheart neckline",
    "silhouette_detail": "The silhouette is",
    "sleeves_and_coverage": "full-length sleeves reaching the wrists",
    "dupatta_or_saree_drape": "Drape:",
}


class TestNarrativeOnlyRefinementCannotChangeWhatWasAsked:
    @pytest.mark.parametrize("change_type", sorted(REFINEMENT_ALLOWED_PATHS))
    def test_the_refinement_itself_is_legal(self, change_type):
        # Nothing below is evidence of anything unless these edits are exactly
        # what Phase 14 permits: a real, non-empty, wholly in-allowlist diff.
        refined = narrative_only_refinement(change_type)
        changed = diff_design_spec_paths(SOURCE_SPEC, refined)
        assert changed, "the characterisation edit changed nothing"
        allowed = REFINEMENT_ALLOWED_PATHS[change_type]
        assert all(path_is_allowed(path, allowed) for path in changed)
        assert not any(path.startswith("source_selections") for path in changed)
        validate_design_spec(refined)

    @pytest.mark.parametrize("change_type", PROMPT_DEAD_CATEGORIES)
    def test_the_image_prompt_is_character_for_character_identical(self, change_type):
        # Phase 23 §2: three of the eight categories provably cannot alter a
        # single character of the image prompt. Still true after the phase — the
        # fix adds a canonical path, it does not render this narrative back.
        assert _prompt(narrative_only_refinement(change_type)) == _prompt(SOURCE_SPEC)

    @pytest.mark.parametrize(
        "change_type, canonical_needle", sorted(CANONICAL_CLAUSE_PER_CATEGORY.items())
    )
    def test_the_canonical_clause_the_category_is_named_after_never_moves(
        self, change_type, canonical_needle
    ):
        # The durable statement of the defect: whatever else a narrative-only
        # refinement does to the prompt, the colour stays the same colour, the
        # fabric the same fabric, the density the same density, the neckline the
        # same neckline, the silhouette the same silhouette, the sleeves the same
        # sleeves and the drape the same drape.
        before = _prompt(SOURCE_SPEC)
        after = _prompt(narrative_only_refinement(change_type))
        assert _clause(after, canonical_needle) == _clause(before, canonical_needle)

    def test_only_the_embellishment_placement_slot_can_move_at_all(self):
        # The measured severity, recorded once rather than per category: on this
        # fully-answered concept the narrative budget fits exactly one slot, so
        # the ONLY narrative a refinement of any category can move is where the
        # embellishment sits — which no category is named after.
        before = _prompt(SOURCE_SPEC)
        for change_type in sorted(REFINEMENT_ALLOWED_PATHS):
            after = _prompt(narrative_only_refinement(change_type))
            assert _drop_clause(after, "Concentrated at ") == _drop_clause(
                before, "Concentrated at "
            ), f"{change_type} moved prompt content outside the embellishment placement slot"


# --- Part B: a refinement that changes its own canonical selection ----------

# One canonical edit per surviving category, each a real questionnaire v4 option
# that the design's own answers still permit, plus the prompt wording it must
# introduce and the wording it must retire.
CANONICAL_REFINEMENTS = {
    "colour_story": ({"fabric_colour": "emerald"}, "rich emerald green", "deep aubergine purple"),
    "fabric_and_texture": (
        {"fabrics": ["silk", "organza"]},
        "Fabrics: silk, organza",
        "Fabrics: velvet, net",
    ),
    "embellishment": (
        {"embellishment_density": "minimal"},
        "Embellishment density: minimal",
        "Embellishment density: balanced",
    ),
    "sleeves_and_coverage": (
        {"sleeves": "cap_sleeve"},
        "short cap sleeves covering the top of the shoulder",
        "full-length sleeves reaching the wrists",
    ),
    "neckline": (
        {"neckline_style": "square_neck"},
        "a square neckline with a flat straight edge",
        "a sweetheart neckline curved like the top of a heart",
    ),
    "dupatta_or_saree_drape": (
        {"dupatta_style": "double_dupatta"},
        "two dupattas, one at the head, one trailing",
        "a dupatta drawn up from the shoulders to frame the face",
    ),
    "silhouette_detail": (
        {"silhouette": "flared_lehenga"},
        "The silhouette is flared lehenga",
        "The silhouette is panelled kali lehenga",
    ),
}


def canonical_refinement(change_type: str) -> dict:
    """``SOURCE_SPEC`` with ONLY the category's own canonical selection changed.

    Deliberately no narrative edit alongside it: the point under test is that the
    canonical selection alone is an acceptable refinement, so any prompt movement
    is attributable to it and nothing else."""
    refined = copy.deepcopy(SOURCE_SPEC)
    edits, _introduces, _retires = CANONICAL_REFINEMENTS[change_type]
    refined["source_selections"].update(copy.deepcopy(edits))
    return refined


@pytest.mark.django_db
class TestCanonicalRefinementReachesTheImagePrompt:
    # Until ADR 0028 every case below failed with
    # RefinementOutputCategory.SOURCE_SELECTIONS_CHANGED and this class carried a
    # strict xfail marker recording that. The marker is gone because the defect
    # is: each category now changes the canonical selection it is named after,
    # and the prompt says so.
    @pytest.mark.parametrize("change_type", sorted(CANONICAL_REFINEMENTS))
    def test_the_refined_selection_is_accepted_and_moves_the_prompt(self, change_type):
        _edits, introduces, retires = CANONICAL_REFINEMENTS[change_type]
        design = make_complete_v4_design()
        source_version = make_source_version(
            design, copy.deepcopy(SOURCE_SPEC), design_spec_schema_version=3
        )
        refined_payload = canonical_refinement(change_type)
        provider = SequenceProvider([payload_result(refined_payload)])

        version = generate_refined_design_spec_for_design(
            design,
            source_version,
            normalise_refinement_request({"schema_version": 1, "change_type": change_type}),
            provider=provider,
        )

        assert provider.calls == 1
        prompt = _prompt(version.design_spec)
        assert introduces in prompt
        assert retires not in prompt
