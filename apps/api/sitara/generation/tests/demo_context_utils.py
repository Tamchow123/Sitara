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


def a_context(
    *,
    selections: dict | None = None,
    untrusted_texts: list[dict] | None = None,
    inspiration_cues: list[dict] | None = None,
) -> GenerationContext:
    return GenerationContext(
        source_selections=selections if selections is not None else a_selections_dict(),
        trusted_answers=[],
        untrusted_texts=untrusted_texts or [],
        inspiration_context=EMPTY_INSPIRATION_SNAPSHOT,
        inspiration_cues=inspiration_cues or [],
    )


def an_inspiration_cue(position: int = 0, garment_type: str = "lehenga") -> dict:
    return {
        "position": position,
        "garment_type": garment_type,
        "visual_description": "A richly worked bridal silhouette with a flowing drape.",
        "cultural_context": "Broad South Asian bridal styling influence.",
    }
