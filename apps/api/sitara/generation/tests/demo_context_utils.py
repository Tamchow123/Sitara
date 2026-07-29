"""Shared GenerationContext builder for Phase 15 Part B demo-engine tests."""

from sitara.generation.context import GenerationContext
from sitara.generation.inspiration_context import InspirationContextSnapshot

EMPTY_INSPIRATION_SNAPSHOT = InspirationContextSnapshot(schema_version=1, items=[])

_SILHOUETTE_BY_GARMENT = {
    "lehenga": "flared_lehenga",
    "saree": "classic_saree_drape",
    "gharara": "gharara_construction",
    "sharara": "sharara_construction",
    "anarkali": "floor_length_anarkali",
    "shalwar_kameez": "straight_kameez",
}


def a_selections_dict(**overrides) -> dict:
    base = {
        "garment_type": "lehenga",
        "ceremony": "nikah",
        "regional_style": "pakistani",
        "silhouette": "flared_lehenga",
        "colour_palette": ["ivory", "gold"],
        "fabrics": ["silk", "organza"],
        "embellishment_styles": ["zardozi", "dabka"],
        "embellishment_density": "balanced",
        "coverage_preferences": ["full_sleeves", "high_neckline"],
        "dupatta_style": "head_drape",
        "saree_drape": None,
    }
    base.update(overrides)
    if "garment_type" in overrides and "silhouette" not in overrides:
        base["silhouette"] = _SILHOUETTE_BY_GARMENT[overrides["garment_type"]]
    return base


_V4_SILHOUETTE_BY_GARMENT = {
    "lehenga": "panelled_kali_lehenga",
    "saree": "pre_stitched_saree",
    "gharara": "farshi_gharara",
    "sharara": "classic_sharara",
    "anarkali": "kalidar_anarkali",
    "shalwar_kameez": "angrakha_kameez",
}


def a_v3_selections_dict(**overrides) -> dict:
    """Questionnaire v4 / DesignSpec v3 selections: a colour per garment role and
    a coverage answer per body area."""
    base = {
        "garment_type": "lehenga",
        "ceremony": "nikah",
        "regional_style": "pakistani",
        "silhouette": "panelled_kali_lehenga",
        "fabric_colour": "deep_maroon",
        "embroidery_colour": "antique_gold",
        "dupatta_colour": "match_fabric",
        "custom_colours": [],
        "fabrics": ["satin", "organza"],
        "embellishment_styles": ["zardozi", "dabka"],
        "embellishment_density": "balanced",
        "neckline_style": "high_neck",
        "sleeves": "full_sleeve",
        "back_coverage": "modest_back",
        "midriff": "covered_midriff",
        "head_covering": "dupatta_over_head",
        "dupatta_style": "double_dupatta",
        "saree_drape": None,
    }
    base.update(overrides)
    if "garment_type" in overrides and "silhouette" not in overrides:
        base["silhouette"] = _V4_SILHOUETTE_BY_GARMENT[overrides["garment_type"]]
    return base


def a_context(
    *,
    selections: dict | None = None,
    untrusted_texts: list[dict] | None = None,
    inspiration_cues: list[dict] | None = None,
    design_spec_schema_version: int = 1,
) -> GenerationContext:
    return GenerationContext(
        source_selections=selections if selections is not None else a_selections_dict(),
        trusted_answers=[],
        untrusted_texts=untrusted_texts or [],
        inspiration_context=EMPTY_INSPIRATION_SNAPSHOT,
        inspiration_cues=inspiration_cues or [],
        design_spec_schema_version=design_spec_schema_version,
    )


def a_v3_context(*, selections: dict | None = None, **kwargs) -> GenerationContext:
    return a_context(
        selections=selections if selections is not None else a_v3_selections_dict(),
        design_spec_schema_version=3,
        **kwargs,
    )


def an_inspiration_cue(position: int = 0, garment_type: str = "lehenga") -> dict:
    return {
        "position": position,
        "garment_type": garment_type,
        "visual_description": "A richly worked bridal silhouette with a flowing drape.",
        "cultural_context": "Broad South Asian bridal styling influence.",
    }
