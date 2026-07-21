"""Versioned demo-asset manifest contract (Phase 15 Part A).

A strict Pydantic v2 model describing the small, curated, rights-cleared pack
of pre-generated concept images the deterministic demo pipeline selects from.
Nothing here reads or writes storage or network — this module only defines
and validates the manifest's shape and its cultural/coverage guarantees.

Every option value used to tag an asset (``garment_types``, ``ceremonies``,
etc.) is drawn from questionnaire v1's own machine values so a tag is always
meaningful against real user selections, but this module never re-implements
questionnaire *answer* validation — it only constrains manifest content.
"""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# Versions the persisted JSON STRUCTURE of a demo manifest. Bump only with a
# new schema file and a migration/reviewed-manifest update.
DEMO_MANIFEST_SCHEMA_VERSION = 1

_MODEL_CONFIG = ConfigDict(
    extra="forbid",
    str_strip_whitespace=True,
    validate_assignment=True,
)

# Questionnaire v1 machine values (apps/api/sitara/questionnaire/fixtures/
# questionnaire_v1.json). Kept here as the demo package's own controlled
# tagging vocabulary — the questionnaire schema itself remains the sole
# authority over what a *user* may answer.
GARMENT_TYPES = frozenset({"lehenga", "saree", "gharara", "sharara", "anarkali", "shalwar_kameez"})
CEREMONIES = frozenset({"nikah", "mehndi", "baraat", "walima", "pheras", "reception"})
SILHOUETTES = frozenset(
    {
        "flared_lehenga",
        "a_line_lehenga",
        "mermaid_lehenga",
        "classic_saree_drape",
        "lehenga_style_saree",
        "gharara_construction",
        "sharara_construction",
        "floor_length_anarkali",
        "knee_length_anarkali",
        "straight_kameez",
        "a_line_kameez",
    }
)
COLOURS = frozenset(
    {
        "ivory",
        "white",
        "red",
        "maroon",
        "blush",
        "pink",
        "peach",
        "orange",
        "yellow",
        "green",
        "emerald",
        "teal",
        "blue",
        "navy",
        "purple",
        "gold",
        "silver",
        "champagne",
        "beige",
        "brown",
        "black",
        "multicolour",
    }
)
FABRICS = frozenset(
    {
        "silk",
        "raw_silk",
        "velvet",
        "organza",
        "chiffon",
        "georgette",
        "net",
        "brocade",
        "jamawar",
        "tissue",
        "cotton_silk",
    }
)
EMBELLISHMENT_STYLES = frozenset(
    {
        "zardozi",
        "dabka",
        "nakshi",
        "gota_patti",
        "mirror_work",
        "resham_threadwork",
        "chikankari",
        "sequins",
        "pearls",
        "crystals",
        "beads",
        "applique",
        "none",
    }
)
EMBELLISHMENT_DENSITIES = frozenset({"minimal", "balanced", "heavy"})
COVERAGE_PREFERENCES = frozenset(
    {
        "sleeveless",
        "short_sleeves",
        "elbow_sleeves",
        "three_quarter_sleeves",
        "full_sleeves",
        "high_neckline",
        "full_back",
        "full_midriff",
        "head_drape_preferred",
    }
)
DUPATTA_STYLES = frozenset(
    {
        "head_drape",
        "one_shoulder",
        "both_shoulders",
        "front_drape",
        "double_dupatta",
        "cape_drape",
        "arm_drape",
    }
)
SAREE_DRAPES = frozenset(
    {"nivi_drape", "seedha_pallu", "bengali_drape", "open_pallu", "pinned_pleats"}
)
REGIONAL_STYLES = frozenset(
    {
        "pakistani",
        "bangladeshi",
        "north_indian",
        "south_indian",
        "punjabi",
        "gujarati",
        "rajasthani",
        "hyderabadi",
    }
)

# Garment/silhouette compatibility, mirroring the questionnaire v1
# ``restrict_options`` rules — used only to reject internally contradictory
# manifest tagging, never to validate a live questionnaire answer.
_GARMENT_SILHOUETTES = {
    "lehenga": frozenset({"flared_lehenga", "a_line_lehenga", "mermaid_lehenga"}),
    "saree": frozenset({"classic_saree_drape", "lehenga_style_saree"}),
    "gharara": frozenset({"gharara_construction"}),
    "sharara": frozenset({"sharara_construction"}),
    "anarkali": frozenset({"floor_length_anarkali", "knee_length_anarkali"}),
    "shalwar_kameez": frozenset({"straight_kameez", "a_line_kameez"}),
}

# A single asset may tag at most this many garment types — prevents any one
# asset from being treated as "universally compatible."
_MAX_GARMENT_TYPES_PER_ASSET = 2

MachineValue = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]{1,63}$")
]
AssetId = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9-]{1,63}$")
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

ProvenanceStatus = Literal[
    "verified_project_owned",
    "verified_licensed_commercial_use",
    "synthetic_development_placeholder",
]

# A pack never satisfying production readiness.
NON_PRODUCTION_PROVENANCE_STATUSES = frozenset({"synthetic_development_placeholder"})

_MIN_ASSET_BYTES = 500
_MAX_ASSET_BYTES = 8_000_000
_MIN_ASSET_EDGE = 512
_MAX_ASSET_EDGE = 4096


class DemoAsset(BaseModel):
    """One curated demo concept image and its manifest-level tagging."""

    model_config = _MODEL_CONFIG

    asset_id: AssetId
    filename: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    sha256: Sha256Hex
    size_bytes: Annotated[int, Field(gt=0)]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    alt_text: Annotated[str, StringConstraints(min_length=10, max_length=400)]
    garment_types: Annotated[list[MachineValue], Field(min_length=1, max_length=8)]
    ceremonies: Annotated[list[MachineValue], Field(min_length=1, max_length=8)]
    silhouettes: Annotated[list[MachineValue], Field(min_length=1, max_length=8)]
    colours: Annotated[list[MachineValue], Field(min_length=1, max_length=8)]
    fabrics: Annotated[list[MachineValue], Field(max_length=8)]
    embellishment_styles: Annotated[list[MachineValue], Field(max_length=8)]
    embellishment_densities: Annotated[list[MachineValue], Field(max_length=8)]
    coverage_preferences: Annotated[list[MachineValue], Field(max_length=8)]
    dupatta_styles: Annotated[list[MachineValue], Field(max_length=8)]
    saree_drapes: Annotated[list[MachineValue], Field(max_length=8)]
    regional_styles: Annotated[list[MachineValue], Field(max_length=8)]
    provenance_status: ProvenanceStatus

    @field_validator("filename")
    @classmethod
    def _filename_is_safe_webp(cls, value: str) -> str:
        if "/" in value or "\\" in value or ".." in value:
            raise ValueError("filename must not contain a path separator or traversal")
        if not value.lower().endswith(".webp"):
            raise ValueError("filename must be a .webp file")
        if value.startswith("."):
            raise ValueError("filename must not be hidden or empty-stem")
        return value

    @field_validator("size_bytes")
    @classmethod
    def _bounded_size(cls, value: int) -> int:
        if not (_MIN_ASSET_BYTES <= value <= _MAX_ASSET_BYTES):
            raise ValueError(
                f"size_bytes must be between {_MIN_ASSET_BYTES} and {_MAX_ASSET_BYTES}"
            )
        return value

    @model_validator(mode="after")
    def _bounded_dimensions_and_ratio(self) -> "DemoAsset":
        if not (_MIN_ASSET_EDGE <= self.width <= _MAX_ASSET_EDGE):
            raise ValueError(f"width must be between {_MIN_ASSET_EDGE} and {_MAX_ASSET_EDGE}")
        if not (_MIN_ASSET_EDGE <= self.height <= _MAX_ASSET_EDGE):
            raise ValueError(f"height must be between {_MIN_ASSET_EDGE} and {_MAX_ASSET_EDGE}")
        # Portrait 3:4 output, exactly.
        if self.width * 4 != self.height * 3:
            raise ValueError("asset must be an exact portrait 3:4 image (width*4 == height*3)")
        return self

    @model_validator(mode="after")
    def _known_taxonomy_values(self) -> "DemoAsset":
        _reject_unknown(self.garment_types, GARMENT_TYPES, "garment_types")
        _reject_unknown(self.ceremonies, CEREMONIES, "ceremonies")
        _reject_unknown(self.silhouettes, SILHOUETTES, "silhouettes")
        _reject_unknown(self.colours, COLOURS, "colours")
        _reject_unknown(self.fabrics, FABRICS, "fabrics")
        _reject_unknown(self.embellishment_styles, EMBELLISHMENT_STYLES, "embellishment_styles")
        _reject_unknown(
            self.embellishment_densities, EMBELLISHMENT_DENSITIES, "embellishment_densities"
        )
        _reject_unknown(self.coverage_preferences, COVERAGE_PREFERENCES, "coverage_preferences")
        _reject_unknown(self.dupatta_styles, DUPATTA_STYLES, "dupatta_styles")
        _reject_unknown(self.saree_drapes, SAREE_DRAPES, "saree_drapes")
        _reject_unknown(self.regional_styles, REGIONAL_STYLES, "regional_styles")
        return self

    @model_validator(mode="after")
    def _garment_tagging_is_coherent(self) -> "DemoAsset":
        if len(set(self.garment_types)) != len(self.garment_types):
            raise ValueError("garment_types must not repeat a value")
        if len(self.garment_types) > _MAX_GARMENT_TYPES_PER_ASSET:
            raise ValueError(
                f"an asset may not claim more than {_MAX_GARMENT_TYPES_PER_ASSET} "
                "garment types (no asset is universally compatible)"
            )
        if "gharara" in self.garment_types and "sharara" in self.garment_types:
            raise ValueError(
                "gharara and sharara are distinct garments and must not both be tagged"
            )
        allowed_silhouettes: set[str] = set()
        for garment in self.garment_types:
            allowed_silhouettes |= _GARMENT_SILHOUETTES.get(garment, frozenset())
        if allowed_silhouettes and not set(self.silhouettes) <= allowed_silhouettes:
            raise ValueError(
                "silhouettes must be compatible with the tagged garment_types "
                f"(allowed: {sorted(allowed_silhouettes)})"
            )
        is_saree = "saree" in self.garment_types
        if is_saree and not self.saree_drapes:
            raise ValueError("a saree asset must tag at least one saree_drape")
        if not is_saree and self.saree_drapes:
            raise ValueError("a non-saree asset must not tag a saree_drape")
        if is_saree and self.dupatta_styles:
            raise ValueError(
                "a saree asset must not tag a dupatta_style "
                "(sarees are not draped with a dupatta)"
            )
        return self


class DemoManifest(BaseModel):
    """The complete versioned demo-asset manifest."""

    model_config = _MODEL_CONFIG

    schema_version: Literal[1]
    pack_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,63}$")]
    assets: Annotated[list[DemoAsset], Field(min_length=1, max_length=200)]

    @field_validator("schema_version", mode="before")
    @classmethod
    def _reject_boolean_schema_version(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("schema_version must be the integer 1, not a boolean")
        return value

    @model_validator(mode="after")
    def _unique_identity(self) -> "DemoManifest":
        asset_ids = [a.asset_id for a in self.assets]
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("asset_id values must be unique")
        filenames = [a.filename for a in self.assets]
        if len(set(filenames)) != len(filenames):
            raise ValueError("filename values must be unique")
        hashes = [a.sha256 for a in self.assets]
        if len(set(hashes)) != len(hashes):
            raise ValueError("sha256 values must be unique (duplicate asset content is rejected)")
        return self


def _reject_unknown(values: list[str], allowed: frozenset[str], field: str) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"{field} contains unknown value(s): {unknown}")


class ManifestCoverageError(ValueError):
    """The manifest is internally valid but fails the pack-wide cultural and
    coverage guarantees (e.g. a garment or ceremony has no compatible asset)."""


def validate_manifest_coverage(manifest: DemoManifest) -> None:
    """Enforce pack-wide cultural accuracy and representation guarantees.

    Runs for every manifest (development synthetic packs included) — a small
    development pack must satisfy the same structural guarantees as a
    production pack; only :func:`assert_production_content_ready` is allowed
    to differ between them."""
    assets = manifest.assets

    covered_garments: set[str] = set()
    covered_ceremonies: set[str] = set()
    covered_colours: set[str] = set()
    covered_fabrics: set[str] = set()
    covered_densities: set[str] = set()
    covered_coverage: set[str] = set()
    for asset in assets:
        covered_garments.update(asset.garment_types)
        covered_ceremonies.update(asset.ceremonies)
        covered_colours.update(asset.colours)
        covered_fabrics.update(asset.fabrics)
        covered_densities.update(asset.embellishment_densities)
        covered_coverage.update(asset.coverage_preferences)

    missing_garments = sorted(GARMENT_TYPES - covered_garments)
    if missing_garments:
        raise ManifestCoverageError(f"no compatible asset for garment(s): {missing_garments}")

    missing_ceremonies = sorted(CEREMONIES - covered_ceremonies)
    if missing_ceremonies:
        raise ManifestCoverageError(
            f"no asset represents ceremony/ceremonies: {missing_ceremonies}"
        )

    if "minimal" not in covered_densities:
        raise ManifestCoverageError("no asset represents minimal embellishment density")
    if "heavy" not in covered_densities:
        raise ManifestCoverageError("no asset represents heavy embellishment density")

    _MODEST_COVERAGE_TAGS = frozenset(
        {"full_sleeves", "high_neckline", "full_back", "full_midriff", "head_drape_preferred"}
    )
    if not covered_coverage & _MODEST_COVERAGE_TAGS:
        raise ManifestCoverageError("no asset represents a modest/full-coverage example")

    if len(covered_colours) < 5:
        raise ManifestCoverageError("fewer than 5 distinct colours are represented across the pack")
    if len(covered_fabrics) < 3:
        raise ManifestCoverageError("fewer than 3 distinct fabrics are represented across the pack")


def assert_production_content_ready(manifest: DemoManifest) -> None:
    """Additional gate for a manifest intended to serve real users.

    A pack containing any non-production provenance status (e.g. the
    development synthetic placeholder pack) never satisfies this — it can
    only ever be installed/served in a development or test environment."""
    validate_manifest_coverage(manifest)
    placeholder_assets = sorted(
        a.asset_id
        for a in manifest.assets
        if a.provenance_status in NON_PRODUCTION_PROVENANCE_STATUSES
    )
    if placeholder_assets:
        raise ManifestCoverageError(
            "manifest contains non-production-provenance asset(s), "
            f"never eligible for production content readiness: {placeholder_assets}"
        )


def canonical_manifest_json(manifest: DemoManifest) -> str:
    """Deterministic, sorted-key, compact JSON serialisation of a manifest."""
    return json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def manifest_sha256(manifest: DemoManifest) -> str:
    """A stable content fingerprint of the whole manifest."""
    return hashlib.sha256(canonical_manifest_json(manifest).encode("utf-8")).hexdigest()
