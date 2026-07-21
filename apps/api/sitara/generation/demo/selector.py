"""Deterministic demo-asset selector (Phase 15 Part A).

Selects one asset from a validated :class:`~.manifest.DemoManifest` for a
given, already-validated :class:`~sitara.generation.design_spec.DesignSpec`
and its deterministic image prompt. Exact garment compatibility is a hard
filter; everything else is a deterministic, source-controlled weighted score
with a stable SHA-256 tie-break. The same design input and manifest always
select the same asset, in any process, on any run."""

import hashlib
import json
from dataclasses import dataclass

from .manifest import DemoManifest, manifest_sha256

DEMO_SELECTOR_VERSION = "1.0.0"

# Explicit, source-controlled scoring weights. Never derived from user free
# text, database ordering or Python's process-randomised ``hash()``.
_WEIGHT_CEREMONY = 3
_WEIGHT_SILHOUETTE = 3
_WEIGHT_COLOUR = 2
_WEIGHT_FABRIC = 2
_WEIGHT_EMBELLISHMENT_STYLE = 2
_WEIGHT_EMBELLISHMENT_DENSITY = 2
_WEIGHT_COVERAGE = 1
_WEIGHT_DUPATTA = 2
_WEIGHT_SAREE_DRAPE = 2
_WEIGHT_REGIONAL = 1
# A smaller bonus for a controlled term appearing in the deterministic image
# prompt beyond what canonical source_selections already scored.
_WEIGHT_PROMPT_TERM_BONUS = 1

_NO_REGIONAL_DIRECTION = "no_specific_direction"


class DemoAssetUnavailable(Exception):
    """No manifest asset is compatible with (or available for) this design.

    Never a silent fallback to live generation and never a silent pick of an
    arbitrary asset — the caller must surface a controlled unavailable
    outcome."""


@dataclass(frozen=True)
class DemoAssetSelection:
    """Minimal private selection provenance to persist alongside an attempt."""

    asset_id: str
    manifest_hash: str
    manifest_schema_version: int
    selector_version: str


def _phrase(machine_value: str) -> str:
    """A machine value as a plain-language search phrase, e.g. ``gota_patti``
    -> ``gota patti``. A deterministic, source-controlled transform — never
    an attempt at general natural-language understanding."""
    return machine_value.replace("_", " ")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _score_asset(source_selections, asset, prompt_lower: str) -> int:
    score = 0
    counted_terms: set[str] = set()

    if source_selections.ceremony in asset.ceremonies:
        score += _WEIGHT_CEREMONY
    if source_selections.silhouette in asset.silhouettes:
        score += _WEIGHT_SILHOUETTE

    matched_colours = set(source_selections.colour_palette) & set(asset.colours)
    score += _WEIGHT_COLOUR * len(matched_colours)
    counted_terms |= matched_colours

    matched_fabrics = set(source_selections.fabrics) & set(asset.fabrics)
    score += _WEIGHT_FABRIC * len(matched_fabrics)
    counted_terms |= matched_fabrics

    matched_embellishment = set(source_selections.embellishment_styles) & set(
        asset.embellishment_styles
    )
    score += _WEIGHT_EMBELLISHMENT_STYLE * len(matched_embellishment)
    counted_terms |= matched_embellishment

    if (
        source_selections.embellishment_density
        and source_selections.embellishment_density in asset.embellishment_densities
    ):
        score += _WEIGHT_EMBELLISHMENT_DENSITY
        counted_terms.add(source_selections.embellishment_density)

    matched_coverage = set(source_selections.coverage_preferences) & set(asset.coverage_preferences)
    score += _WEIGHT_COVERAGE * len(matched_coverage)
    counted_terms |= matched_coverage

    if source_selections.dupatta_style and source_selections.dupatta_style in asset.dupatta_styles:
        score += _WEIGHT_DUPATTA
        counted_terms.add(source_selections.dupatta_style)

    if source_selections.saree_drape and source_selections.saree_drape in asset.saree_drapes:
        score += _WEIGHT_SAREE_DRAPE
        counted_terms.add(source_selections.saree_drape)

    regional_style = source_selections.regional_style
    if (
        regional_style
        and regional_style != _NO_REGIONAL_DIRECTION
        and regional_style in asset.regional_styles
    ):
        score += _WEIGHT_REGIONAL
        counted_terms.add(regional_style)

    # Controlled terms present in the deterministic image prompt but not
    # already scored via an exact source_selections match.
    bonus_pool = (
        set(asset.colours)
        | set(asset.fabrics)
        | set(asset.embellishment_styles)
        | set(asset.embellishment_densities)
        | set(asset.coverage_preferences)
    ) - counted_terms
    for term in sorted(bonus_pool):
        if _phrase(term) in prompt_lower:
            score += _WEIGHT_PROMPT_TERM_BONUS

    return score


def _tie_break_key(*, manifest_hash: str, source_selections_json: str, asset_id: str) -> str:
    payload = _canonical_json(
        {
            "selector_version": DEMO_SELECTOR_VERSION,
            "manifest_hash": manifest_hash,
            "canonical_design_input": source_selections_json,
            "asset_id": asset_id,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def select_demo_asset(design_spec, image_prompt: str, manifest: DemoManifest) -> DemoAssetSelection:
    """Deterministically select one manifest asset for ``design_spec``.

    Raises :class:`DemoAssetUnavailable` if no asset's ``garment_types``
    exactly includes ``design_spec.source_selections.garment_type`` — an
    incompatible garment is never a fallback candidate."""
    source_selections = design_spec.source_selections
    garment_type = source_selections.garment_type

    candidates = [a for a in manifest.assets if garment_type in a.garment_types]
    if not candidates:
        raise DemoAssetUnavailable(
            "no manifest asset is compatible with this design's garment type"
        )

    manifest_hash = manifest_sha256(manifest)
    prompt_lower = image_prompt.lower()
    source_selections_json = _canonical_json(source_selections.model_dump(mode="json"))

    scored = [
        (candidate, _score_asset(source_selections, candidate, prompt_lower))
        for candidate in sorted(candidates, key=lambda a: a.asset_id)
    ]
    best_score = max(score for _asset, score in scored)
    tied = [candidate for candidate, score in scored if score == best_score]

    if len(tied) == 1:
        winner = tied[0]
    else:
        winner = min(
            tied,
            key=lambda candidate: _tie_break_key(
                manifest_hash=manifest_hash,
                source_selections_json=source_selections_json,
                asset_id=candidate.asset_id,
            ),
        )

    return DemoAssetSelection(
        asset_id=winner.asset_id,
        manifest_hash=manifest_hash,
        manifest_schema_version=manifest.schema_version,
        selector_version=DEMO_SELECTOR_VERSION,
    )
