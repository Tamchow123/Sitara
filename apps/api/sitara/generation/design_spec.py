"""The authoritative DesignSpec contract (Phase 8).

A strict Pydantic v2 model is the single source of truth for the shape of a
generated bridalwear *concept* specification. It is what the Anthropic
structured-output call is parsed into, what Django re-validates, what is
persisted onto ``DesignVersion.design_spec`` and what the committed JSON
Schema (``schemas/design_spec_v1.json``) is generated from.

Deliberately bounded and shallow: no recursion, no free-form dictionaries for
the primary sections, no unconstrained ``Any``, no provider metadata, no
image-generation prompt, and no measurements or sewing-pattern fields. The
model is meant to be directly useful to the Phase 9 prompt builder and the
Phase 12 results page.
"""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .input_safety import contains_phrase

# Versions the persisted JSON STRUCTURE of a DesignSpec. Bump only with a new
# schema file (schemas/design_spec_vN.json) and a migration strategy.
DESIGN_SPEC_SCHEMA_VERSION = 1

# The canonical "no broad regional direction" machine value. When the user
# picks this (or nothing) the cultural_context regional direction must stay
# null; any real regional_style requires a non-empty regional direction.
NO_REGIONAL_DIRECTION = "no_specific_direction"

# Flexible phrasing used to recognise the two REQUIRED construction caveats.
# Matched with token-boundary awareness (see input_safety.contains_phrase), so
# original prose and recorded fixtures alike satisfy them without demanding one
# exact English sentence.
_CONCEPT_ONLY_PHRASES = (
    "concept visualisation",
    "concept visualization",
    "concept only",
    "not a sewing pattern",
    "not a pattern",
)
_NO_CONSTRUCT_GUARANTEE_PHRASES = (
    "does not guarantee",
    "not guarantee",
    "no guarantee",
    "not guaranteed",
    "cannot be constructed",
    "not guaranteed to be constructible",
)


def _any_caveat_mentions(caveats: list[str], phrases: tuple[str, ...]) -> bool:
    return any(contains_phrase(caveat, phrase) for caveat in caveats for phrase in phrases)


# Versions the TRUSTED system instructions and context format (Part B's system
# prompt + context builder). It must change whenever the system prompt, the
# context layout or the generation semantics materially change; a prompt-hash
# test guards it. Defined here so the contract and the prompt version live
# together.
SPEC_TEMPLATE_VERSION = "2.1.0"

_MODEL_CONFIG = ConfigDict(
    extra="forbid",
    str_strip_whitespace=True,
    validate_assignment=True,
)

# A canonical questionnaire OPTION value — a lower-case machine identifier,
# never a label or free text. Deliberately a PATTERN, not an enum, so the
# committed JSON Schema never duplicates the questionnaire's option lists.
MachineValue = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]{1,63}$")
]

# Bounded narrative building blocks. Every string and list is capped so a
# malformed or hostile generation can never balloon the persisted payload.
NarrativeItem = Annotated[str, StringConstraints(min_length=1, max_length=400)]
NarrativeList = Annotated[list[NarrativeItem], Field(min_length=1, max_length=8)]
CaveatList = Annotated[list[NarrativeItem], Field(min_length=1, max_length=6)]

_ColourList = Annotated[list[MachineValue], Field(min_length=1, max_length=8)]
_OptionalList = Annotated[list[MachineValue], Field(max_length=12)]
_EmbellishmentList = Annotated[list[MachineValue], Field(min_length=1, max_length=8)]
_FabricValueList = Annotated[list[MachineValue], Field(max_length=8)]


class SourceSelections(BaseModel):
    """The canonical machine values the validated questionnaire supplied.

    Echoed verbatim by the generation and verified to match the trusted input
    exactly (see the generation service). No free-text note is ever copied
    here; optional questionnaire choices are null or empty as appropriate, and
    ordered lists preserve their submitted order."""

    model_config = _MODEL_CONFIG

    garment_type: MachineValue
    ceremony: MachineValue
    regional_style: MachineValue | None
    silhouette: MachineValue
    colour_palette: _ColourList
    fabrics: _FabricValueList
    embellishment_styles: _EmbellishmentList
    embellishment_density: MachineValue | None
    coverage_preferences: _OptionalList
    dupatta_style: MachineValue | None
    saree_drape: MachineValue | None


class GarmentBreakdown(BaseModel):
    model_config = _MODEL_CONFIG

    overall_form: NarrativeItem
    garment_components: NarrativeList
    silhouette: NarrativeItem
    drape_or_layering: NarrativeItem
    key_proportions: NarrativeItem


class ColourStory(BaseModel):
    model_config = _MODEL_CONFIG

    palette_summary: NarrativeItem
    placement: NarrativeItem
    rationale: NarrativeItem


class FabricEntry(BaseModel):
    model_config = _MODEL_CONFIG

    fabric: NarrativeItem
    placement: NarrativeItem
    finish_and_movement: NarrativeItem


class EmbellishmentPlan(BaseModel):
    model_config = _MODEL_CONFIG

    techniques: NarrativeList
    density: NarrativeItem
    placement: NarrativeList
    motifs: NarrativeList
    restraint_notes: NarrativeItem


class CoverageAndDrape(BaseModel):
    model_config = _MODEL_CONFIG

    sleeves: NarrativeItem
    neckline: NarrativeItem
    back_and_midriff: NarrativeItem
    head_covering: NarrativeItem
    dupatta_or_saree_drape: NarrativeItem


class CulturalContext(BaseModel):
    model_config = _MODEL_CONFIG

    # Null when no broad regional direction was requested.
    regional_direction: NarrativeItem | None
    interpretation_notes: NarrativeList
    safeguards: NarrativeList


class DesignSpec(BaseModel):
    """A complete, validated bridalwear CONCEPT specification."""

    model_config = _MODEL_CONFIG

    schema_version: Literal[1]
    source_selections: SourceSelections
    title: Annotated[str, StringConstraints(min_length=3, max_length=120)]
    concept_summary: Annotated[str, StringConstraints(min_length=80, max_length=700)]
    garment_breakdown: GarmentBreakdown
    colour_story: ColourStory
    fabrics_and_texture: Annotated[list[FabricEntry], Field(min_length=1, max_length=8)]
    embellishment_plan: EmbellishmentPlan
    coverage_and_drape: CoverageAndDrape
    cultural_context: CulturalContext
    styling_notes: NarrativeList
    # Non-empty: must frame the output as concept visualisation, not a sewing
    # pattern or a guarantee of constructibility.
    construction_caveats: CaveatList
    image_alt_text: Annotated[str, StringConstraints(min_length=40, max_length=300)]

    @field_validator("schema_version", mode="before")
    @classmethod
    def _reject_boolean_schema_version(cls, value: object) -> object:
        # bool is an int subclass (True == 1); a schema saying ``true`` is a
        # mistake, not the integer 1.
        if isinstance(value, bool):
            raise ValueError("schema_version must be the integer 1, not a boolean")
        return value

    @model_validator(mode="after")
    def _enforce_semantic_invariants(self) -> "DesignSpec":
        # The two required caveats must actually be present (flexible phrasing).
        if not _any_caveat_mentions(self.construction_caveats, _CONCEPT_ONLY_PHRASES):
            raise ValueError(
                "construction_caveats must include an explicit concept-only / "
                "not-a-sewing-pattern caveat"
            )
        if not _any_caveat_mentions(self.construction_caveats, _NO_CONSTRUCT_GUARANTEE_PHRASES):
            raise ValueError(
                "construction_caveats must include an explicit "
                "no-constructibility-guarantee caveat"
            )
        # Regional direction must agree with the selected regional style.
        regional_style = self.source_selections.regional_style
        has_direction = regional_style is not None and regional_style != NO_REGIONAL_DIRECTION
        if has_direction and self.cultural_context.regional_direction is None:
            raise ValueError(
                "cultural_context.regional_direction must be non-empty when a "
                "regional style is selected"
            )
        if not has_direction and self.cultural_context.regional_direction is not None:
            raise ValueError(
                "cultural_context.regional_direction must be null when no regional "
                "style is selected"
            )
        return self


def design_spec_json_schema() -> dict:
    """The DesignSpec JSON Schema (also written to the committed file)."""
    return DesignSpec.model_json_schema()
