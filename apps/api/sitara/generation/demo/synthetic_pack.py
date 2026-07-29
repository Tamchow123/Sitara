"""Development-only synthetic demo pack (Phase 15 Part A).

A small, programmatically generated, zero-network, deterministic manifest and
matching abstract/stylised images covering every garment category — never
real product photography, never rights-cleared imagery, and never eligible
for production content readiness (see
:func:`sitara.generation.demo.manifest.assert_production_content_ready`).
Every asset's ``provenance_status`` is ``synthetic_development_placeholder``
and every ``alt_text`` says so explicitly."""

import hashlib
import io
from dataclasses import dataclass

from django.conf import settings
from PIL import Image

from .manifest import DemoAsset, DemoManifest

SYNTHETIC_PACK_ID = "sitara-demo-dev-synthetic-v1"
_PLACEHOLDER_NOTICE = (
    "Abstract synthetic development placeholder — not representative of "
    "production imagery and never used to serve real users."
)
_ASSET_WIDTH = 768
_ASSET_HEIGHT = 1024


class SyntheticPackNotAllowed(Exception):
    """Raised when the synthetic pack is requested in a production environment."""


@dataclass(frozen=True)
class _SyntheticAssetSpec:
    asset_id: str
    garment_types: list[str]
    ceremonies: list[str]
    silhouettes: list[str]
    colours: list[str]
    fabrics: list[str]
    embellishment_styles: list[str]
    embellishment_densities: list[str]
    coverage_preferences: list[str]
    necklines: list[str]
    dupatta_styles: list[str]
    saree_drapes: list[str]
    rgb_primary: tuple[int, int, int]
    rgb_secondary: tuple[int, int, int]


_SPECS: tuple[_SyntheticAssetSpec, ...] = (
    _SyntheticAssetSpec(
        asset_id="lehenga-baraat-dev-001",
        garment_types=["lehenga"],
        ceremonies=["baraat", "reception"],
        silhouettes=["flared_lehenga", "a_line_lehenga"],
        colours=["red", "gold", "maroon"],
        fabrics=["silk", "velvet"],
        embellishment_styles=["zardozi", "sequins"],
        embellishment_densities=["heavy"],
        coverage_preferences=["full_sleeves"],
        necklines=["sweetheart_neck", "curved_scoop"],
        dupatta_styles=["head_drape"],
        saree_drapes=[],
        rgb_primary=(150, 20, 30),
        rgb_secondary=(200, 160, 60),
    ),
    _SyntheticAssetSpec(
        asset_id="saree-nikah-dev-002",
        garment_types=["saree"],
        ceremonies=["nikah", "walima"],
        silhouettes=["classic_saree_drape"],
        colours=["ivory", "gold"],
        fabrics=["silk"],
        embellishment_styles=["gota_patti"],
        embellishment_densities=["balanced"],
        coverage_preferences=["full_sleeves", "high_neckline"],
        necklines=["high_neck", "boat_neck"],
        dupatta_styles=[],
        saree_drapes=["nivi_drape"],
        rgb_primary=(230, 222, 200),
        rgb_secondary=(200, 170, 70),
    ),
    _SyntheticAssetSpec(
        asset_id="gharara-mehndi-dev-003",
        garment_types=["gharara"],
        ceremonies=["mehndi"],
        silhouettes=["gharara_construction"],
        colours=["green", "yellow"],
        fabrics=["cotton_silk"],
        embellishment_styles=["mirror_work"],
        embellishment_densities=["minimal"],
        coverage_preferences=["three_quarter_sleeves"],
        necklines=["v_neck"],
        dupatta_styles=["one_shoulder"],
        saree_drapes=[],
        rgb_primary=(40, 120, 60),
        rgb_secondary=(220, 200, 60),
    ),
    _SyntheticAssetSpec(
        asset_id="sharara-mehndi-dev-004",
        garment_types=["sharara"],
        ceremonies=["mehndi", "reception"],
        silhouettes=["sharara_construction"],
        colours=["peach", "orange"],
        fabrics=["georgette"],
        embellishment_styles=["resham_threadwork"],
        embellishment_densities=["minimal"],
        coverage_preferences=["elbow_sleeves"],
        necklines=["square_neck"],
        dupatta_styles=["both_shoulders"],
        saree_drapes=[],
        rgb_primary=(230, 150, 110),
        rgb_secondary=(210, 100, 40),
    ),
    _SyntheticAssetSpec(
        asset_id="anarkali-walima-dev-005",
        garment_types=["anarkali"],
        ceremonies=["walima", "reception"],
        silhouettes=["floor_length_anarkali"],
        colours=["navy", "silver"],
        fabrics=["net", "organza"],
        embellishment_styles=["crystals", "sequins"],
        embellishment_densities=["balanced"],
        coverage_preferences=["full_sleeves", "full_back"],
        necklines=["classic_crew", "high_neck"],
        dupatta_styles=["front_drape"],
        saree_drapes=[],
        rgb_primary=(20, 30, 70),
        rgb_secondary=(190, 190, 200),
    ),
    _SyntheticAssetSpec(
        asset_id="shalwar-pheras-dev-006",
        garment_types=["shalwar_kameez"],
        ceremonies=["pheras"],
        silhouettes=["straight_kameez", "a_line_kameez"],
        colours=["maroon", "gold"],
        fabrics=["brocade"],
        embellishment_styles=["dabka"],
        embellishment_densities=["heavy"],
        coverage_preferences=["full_sleeves", "head_drape_preferred"],
        necklines=["band_collar", "high_neck"],
        dupatta_styles=["double_dupatta"],
        saree_drapes=[],
        rgb_primary=(120, 20, 40),
        rgb_secondary=(190, 150, 50),
    ),
    # Phase 16B: a synthetic Anand Karaj asset covering satin, a ruby palette, a
    # high neckline, full midriff coverage and a covered head — so the demo
    # pipeline can satisfy the new fail-closed constraints for an Anand Karaj
    # design in development. Production requires an operator-supplied,
    # culturally-reviewed Anand Karaj asset (this synthetic one is never
    # production-eligible).
    _SyntheticAssetSpec(
        asset_id="lehenga-anand-karaj-dev-007",
        garment_types=["lehenga"],
        ceremonies=["anand_karaj"],
        silhouettes=["flared_lehenga", "a_line_lehenga"],
        colours=["ruby", "gold"],
        fabrics=["satin", "organza"],
        embellishment_styles=["zardozi"],
        embellishment_densities=["balanced"],
        coverage_preferences=["full_sleeves", "full_midriff", "full_back", "head_drape_preferred"],
        necklines=["high_neck", "classic_crew"],
        dupatta_styles=["double_dupatta", "head_drape"],
        saree_drapes=[],
        rgb_primary=(150, 20, 40),
        rgb_secondary=(200, 160, 60),
    ),
)


def _assert_synthetic_pack_allowed() -> None:
    if getattr(settings, "APP_ENV", "development") == "production":
        raise SyntheticPackNotAllowed(
            "the development synthetic demo pack must never be built or installed "
            "when APP_ENV=production"
        )


def _render_synthetic_image(spec: _SyntheticAssetSpec) -> bytes:
    """A deterministic, locally-rendered abstract checkerboard image.

    Zero network, zero randomness: the block size is derived from a stable
    hash of the asset id so each placeholder is visually distinct without
    depending on process state."""
    image = Image.new("RGB", (_ASSET_WIDTH, _ASSET_HEIGHT), spec.rgb_primary)
    seed = int(hashlib.sha256(spec.asset_id.encode("utf-8")).hexdigest()[:4], 16)
    block = 48 + (seed % 5) * 16
    for x in range(0, _ASSET_WIDTH, block):
        for y in range(0, _ASSET_HEIGHT, block):
            if (x // block + y // block) % 2 == 0:
                for i in range(x, min(x + block, _ASSET_WIDTH)):
                    for j in range(y, min(y + block, _ASSET_HEIGHT)):
                        image.putpixel((i, j), spec.rgb_secondary)
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=80, lossless=False, method=6)
    return buffer.getvalue()


def build_synthetic_demo_pack() -> tuple[DemoManifest, dict[str, bytes]]:
    """Build the development-only synthetic manifest and its image bytes.

    Returns ``(manifest, {asset_id: webp_bytes})``. Raises
    :class:`SyntheticPackNotAllowed` when ``settings.APP_ENV == "production"``."""
    _assert_synthetic_pack_allowed()

    assets: list[DemoAsset] = []
    images: dict[str, bytes] = {}
    for spec in _SPECS:
        image_bytes = _render_synthetic_image(spec)
        images[spec.asset_id] = image_bytes
        assets.append(
            DemoAsset(
                asset_id=spec.asset_id,
                filename=f"{spec.asset_id}.webp",
                sha256=hashlib.sha256(image_bytes).hexdigest(),
                size_bytes=len(image_bytes),
                width=_ASSET_WIDTH,
                height=_ASSET_HEIGHT,
                alt_text=(
                    f"{_PLACEHOLDER_NOTICE} Represents a {spec.garment_types[0].replace('_', ' ')} "
                    "concept in an abstract colour-block style."
                ),
                garment_types=spec.garment_types,
                ceremonies=spec.ceremonies,
                silhouettes=spec.silhouettes,
                colours=spec.colours,
                fabrics=spec.fabrics,
                embellishment_styles=spec.embellishment_styles,
                embellishment_densities=spec.embellishment_densities,
                coverage_preferences=spec.coverage_preferences,
                necklines=spec.necklines,
                dupatta_styles=spec.dupatta_styles,
                saree_drapes=spec.saree_drapes,
                regional_styles=[],
                provenance_status="synthetic_development_placeholder",
            )
        )

    manifest = DemoManifest(schema_version=2, pack_id=SYNTHETIC_PACK_ID, assets=assets)
    return manifest, images
