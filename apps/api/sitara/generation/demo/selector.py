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

# 2.0.0 (Phase 16B): adds a neckline scoring dimension and the fail-closed
# coverage/ceremony hard constraints below; the version participates in the
# seed and tie-break recipe, so a bump changes demo seeds and selections.
DEMO_SELECTOR_VERSION = "2.0.0"

# Explicit, source-controlled scoring weights. Never derived from user free
# text, database ordering or Python's process-randomised ``hash()``.
_WEIGHT_CEREMONY = 3
_WEIGHT_SILHOUETTE = 3
_WEIGHT_COLOUR = 2
_WEIGHT_FABRIC = 2
_WEIGHT_EMBELLISHMENT_STYLE = 2
_WEIGHT_EMBELLISHMENT_DENSITY = 2
_WEIGHT_COVERAGE = 1
_WEIGHT_NECKLINE = 2
_WEIGHT_DUPATTA = 2
_WEIGHT_SAREE_DRAPE = 2
_WEIGHT_REGIONAL = 1
# A smaller bonus for a controlled term appearing in the deterministic image
# prompt beyond what canonical source_selections already scored.
_WEIGHT_PROMPT_TERM_BONUS = 1

_NO_REGIONAL_DIRECTION = "no_specific_direction"

# Coverage machine values that trigger a fail-closed HARD constraint (Phase
# 16B): a selection that demands the head be covered or the midriff fully
# covered must never be satisfied by an asset that shows an uncovered head or an
# exposed midriff — the pipeline surfaces a controlled unavailable outcome
# rather than a misleading image.
_HEAD_COVER_PREF = "head_drape_preferred"
_FULL_MIDRIFF = "full_midriff"
_HEAD_DRAPE_DUPATTA = "head_drape"
_DOUBLE_DUPATTA = "double_dupatta"
# The culturally-distinct ceremony that must never be substituted with a
# nearest-neighbour asset (Phase 16B): an Anand Karaj design requires an asset
# explicitly tagged for it, or generation fails closed.
_ANAND_KARAJ = "anand_karaj"


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

    # The dedicated canonical neckline (DesignSpec v2). A v1 spec has no
    # neckline_style attribute, so ``getattr`` degrades gracefully.
    neckline = getattr(source_selections, "neckline_style", None)
    if neckline and neckline in asset.necklines:
        score += _WEIGHT_NECKLINE
        counted_terms.add(neckline)

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
        | set(asset.necklines)
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


def _design_wants_head_covered(source_selections) -> bool:
    return _HEAD_COVER_PREF in (source_selections.coverage_preferences or []) or (
        source_selections.dupatta_style in {_HEAD_DRAPE_DUPATTA, _DOUBLE_DUPATTA}
    )


def _asset_shows_covered_head(asset) -> bool:
    return _HEAD_COVER_PREF in asset.coverage_preferences or bool(
        {_HEAD_DRAPE_DUPATTA, _DOUBLE_DUPATTA} & set(asset.dupatta_styles)
    )


def select_demo_asset(design_spec, image_prompt: str, manifest: DemoManifest) -> DemoAssetSelection:
    """Deterministically select one manifest asset for ``design_spec``.

    Applies hard, fail-closed filters before scoring — an incompatible garment,
    a missing Anand Karaj asset, an uncovered-head asset for a covered-head
    selection, or an exposed-midriff asset for a full-midriff selection is never
    a fallback candidate. Raises :class:`DemoAssetUnavailable` when no asset
    survives the filters."""
    source_selections = design_spec.source_selections
    garment_type = source_selections.garment_type

    candidates = [a for a in manifest.assets if garment_type in a.garment_types]
    if not candidates:
        raise DemoAssetUnavailable(
            "no manifest asset is compatible with this design's garment type"
        )

    # Anand Karaj must never be shown a nearest-neighbour ceremony asset.
    if source_selections.ceremony == _ANAND_KARAJ:
        candidates = [a for a in candidates if _ANAND_KARAJ in a.ceremonies]
        if not candidates:
            raise DemoAssetUnavailable("no manifest asset is an approved match for this ceremony")

    # A covered-head selection must not be satisfied by an uncovered-head asset.
    if _design_wants_head_covered(source_selections):
        candidates = [a for a in candidates if _asset_shows_covered_head(a)]
        if not candidates:
            raise DemoAssetUnavailable("no manifest asset satisfies the requested head covering")

    # A full-midriff selection must not be satisfied by an exposed-midriff asset.
    if _FULL_MIDRIFF in (source_selections.coverage_preferences or []):
        candidates = [a for a in candidates if _FULL_MIDRIFF in a.coverage_preferences]
        if not candidates:
            raise DemoAssetUnavailable("no manifest asset satisfies the requested midriff coverage")

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
