#!/usr/bin/env python3
"""Build the questionnaire option visuals from the project's source photography.

`images/` at the repository root is a BUILD INPUT, never served: it holds the
project's own AI-generated source photography at full size (~202 MB). This
script selects exactly one source image per questionnaire option `visual_key`,
crops it to that question's card aspect, downscales it and re-encodes it as a
metadata-free WebP under `apps/web/public/questionnaire-visuals/`, then records
every output in `src/features/questionnaire/visuals/asset-integrity.json` with
its SHA-256 so `manifest.test.ts` can prove the served bytes are the reviewed
bytes.

    python scripts/build-questionnaire-images.py

Determinism: the mapping below is explicit (never a directory scan), iteration
is sorted, cropping is integer arithmetic, resampling is LANCZOS and the WebP
encoder options are fixed, so a rebuild that changes a hash means a source image
or a mapping entry changed and must be re-reviewed.

Run it with the Pillow pinned in `apps/api/requirements.txt` (currently
11.3.0) — that is the version CI rebuilds with, and CI fails the build if the
result differs from the committed assets by so much as a byte, which is what
actually enforces this reproducibility claim rather than merely asserting it.
(Verified byte-identical on Pillow 11.1.0 and 11.3.0; a future encoder change
would surface as that CI drift failure, not as silent divergence.)

Rights: every source is project-owned photography generated for Sitara. No
third-party or downloaded imagery, and no inspiration-catalogue asset, is ever
used here. These visuals only help a user understand an option — they are never
sent to an AI provider and never influence DesignSpec generation.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from imagekit import digest, render

WEB_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = WEB_ROOT.parent.parent
SOURCE_ROOT = REPO_ROOT / "images"
OUTPUT_ROOT = WEB_ROOT / "public" / "questionnaire-visuals"
INTEGRITY_PATH = (
    WEB_ROOT / "src" / "features" / "questionnaire" / "visuals" / "asset-integrity.json"
)

# Card aspects, keyed by name -> (width, height) in device pixels. Every shape
# is built on a 720 px long edge so one question's cards never arrive at a
# visibly different resolution from another's.
#
# The names are the ratios, not adjectives: "portrait" alone stopped being
# useful once the design asked for three different portrait shapes.
ASPECTS = {
    "landscape-3-2": (720, 480),
    "landscape-5-4": (720, 576),
    "square": (720, 720),
    "portrait-4-5": (576, 720),
    "portrait-3-4": (540, 720),
    "portrait-2-3": (480, 720),
}

# The aspect is a property of the GROUP, never of an individual entry, so every
# card within one question's grid is necessarily the same shape — and because
# ChoiceOptionGrid reads `--choice-card-aspect` straight off the first visual
# in the manifest, THIS TABLE is what sets the card shape on screen. There is
# no second place to change it.
#
# The seven shapes are the handoff's own, one per question, each chosen so the
# frame holds the thing the card exists to explain:
#
#   3:2   ceremony  — a room, a procession, a mandap: these are scene shots,
#                     and a portrait crop of a room throws the room away.
#         fabrics   — a swatch reads best as a wide field of cloth.
#   3:4   garment, dupatta — a whole figure, not quite as narrow as a plate.
#   2:3   culture, silhouette, saree drape — full length, where the silhouette
#                     IS the answer and the hem must stay in frame.
#   1:1   embroidery, richness — a surface, with no orientation of its own.
#   5:4   neckline  — wide enough to hold both shoulders around the neckline.
#   4:5   sleeves, back, midriff, head covering — an upright band of the body
#                     from roughly the head to the hip.
GROUP_ASPECT = {
    "ceremonies": "landscape-3-2",
    "fabrics": "landscape-3-2",
    "garments": "portrait-3-4",
    "dupatta": "portrait-3-4",
    "cultural-styling": "portrait-2-3",
    "silhouettes": "portrait-2-3",
    "saree-drapes": "portrait-2-3",
    "embroidery": "square",
    "embellishment-density": "square",
    "necklines": "landscape-5-4",
    "sleeves": "portrait-4-5",
    "back-coverage": "portrait-4-5",
    "midriff": "portrait-4-5",
    "head-covering": "portrait-4-5",
}

# Vertical crop anchor: 0.0 keeps the top of the frame, 1.0 the bottom. This is
# the ONLY place framing is decided — the served asset is already cut to the
# card's exact aspect, so the browser does no second crop and there is no
# `object-position` to disagree with this number.
#
# Full figures are biased upward so a tall source loses floor rather than head.
FULL_FIGURE_FOCUS = 0.42
CENTRE_FOCUS = 0.5

@dataclass(frozen=True)
class Visual:
    """One questionnaire option visual: which source image, cropped how."""

    group: str
    source: str
    alt: str
    focus: float = FULL_FIGURE_FOCUS

    @property
    def aspect(self) -> str:
        return GROUP_ASPECT[self.group]


def full_figure(group: str, source: str, alt: str, focus: float = FULL_FIGURE_FOCUS) -> Visual:
    return Visual(group=group, source=source, alt=alt, focus=focus)


def detail(group: str, source: str, alt: str, focus: float = CENTRE_FOCUS) -> Visual:
    return Visual(group=group, source=source, alt=alt, focus=focus)


# --- The mapping -----------------------------------------------------------
# Keyed by the questionnaire schema's option `visual_key`. Every key here must
# exist in the schema, and every non-colour option carrying a `visual_key` must
# appear here — that bidirectional contract is enforced by
# apps/api/sitara/questionnaire/tests/test_visual_keys.py, in the package that
# owns the schema. manifest.test.ts separately proves the served bytes still
# match the reviewed hashes.
#
# Cultural accuracy note: gharara and sharara are DIFFERENT constructions and
# their sources were chosen to show that difference. A gharara is fitted to the
# knee with a joined seam there and flares below it; a sharara flares from the
# waist with no knee joint. Sources were visually checked against that
# distinction rather than trusted from their filenames.
VISUALS: dict[str, Visual] = {
    # --- garment_type ---
    "garment_lehenga": full_figure(
        "garments",
        "03-garments/sitara__garments__lehenga__v2.jpg",
        "A bridal lehenga: a fitted blouse with a separate full-length flared skirt and dupatta.",
    ),
    "garment_saree": full_figure(
        "garments",
        "03-garments/sitara__garments__saree__v1.jpg",
        "A bridal saree: a single length of fabric draped over a fitted blouse.",
    ),
    "garment_gharara": full_figure(
        "garments",
        "03-garments/sitara__garments__gharara__v2.jpg",
        "A gharara: a short kameez over trousers fitted to the knee that flare widely below it.",
    ),
    "garment_sharara": full_figure(
        "garments",
        "03-garments/sitara__garments__sharara__v3.jpg",
        "A sharara: a kameez over wide trousers that flare from the waist.",
    ),
    "garment_anarkali": full_figure(
        "garments",
        "03-garments/sitara__garments__anarkali__v1.jpg",
        "An anarkali: a long fitted-bodice frock flaring into a full skirt.",
    ),
    "garment_shalwar_kameez": full_figure(
        "garments",
        "03-garments/sitara__garments__shalwar_kameez__v1.jpg",
        "A shalwar kameez: a long tunic worn over loose gathered trousers.",
    ),
    # --- silhouette: lehenga ---
    "silhouette_a_line_lehenga": full_figure(
        "silhouettes",
        "05-silhouettes/lehenga/sitara__silhouettes_lehenga__a_line_lehenga__v1.jpg",
        "An A-line lehenga skirt widening gently and evenly from the waist to the hem.",
    ),
    "silhouette_flared_lehenga": full_figure(
        "silhouettes",
        "05-silhouettes/lehenga/sitara__silhouettes_lehenga__flared_lehenga__v1.jpg",
        "A full-flare lehenga skirt with dramatic volume at the hem.",
    ),
    "silhouette_mermaid_lehenga": full_figure(
        "silhouettes",
        "05-silhouettes/lehenga/sitara__silhouettes_lehenga__mermaid_lehenga__v1.jpg",
        "A mermaid lehenga skirt shaped close to the hip and thigh, flaring below the knee.",
    ),
    "silhouette_straight_lehenga": full_figure(
        "silhouettes",
        "gaps/Straight-cut lehenga.png",
        "A straight-cut lehenga skirt falling in a narrow column from the waist.",
    ),
    "silhouette_panelled_kali_lehenga": full_figure(
        "silhouettes",
        "gaps/Lehenga — Panelled kali.png",
        "A panelled kali lehenga skirt built from tapering vertical panels.",
    ),
    # --- silhouette: saree ---
    "silhouette_classic_saree_drape": full_figure(
        "silhouettes",
        "05-silhouettes/saree/sitara__silhouettes_saree__classic_saree_drape__v1.jpg",
        "A classic saree drape with pleats at the waist and the pallu over one shoulder.",
    ),
    "silhouette_lehenga_style_saree": full_figure(
        "silhouettes",
        "05-silhouettes/saree/sitara__silhouettes_saree__lehenga_style_saree__v1.jpg",
        "A lehenga-style saree combining a flared skirt with a draped pallu.",
    ),
    "silhouette_pre_stitched_saree": full_figure(
        "silhouettes",
        "gaps/Pre-stitched saree.png",
        "A pre-stitched saree with the pleats and pallu sewn in place ready to wear.",
    ),
    "silhouette_half_saree": full_figure(
        "silhouettes",
        "gaps/Saree — Half saree (langa voni).png",
        "A half saree, or langa voni: a long skirt with a blouse and a separate draped dupatta.",
    ),
    # --- silhouette: gharara (fitted to the knee, flaring below the joint) ---
    "silhouette_classic_gharara": full_figure(
        "silhouettes",
        "05-silhouettes/gharara/sitara__silhouettes_gharara__gharara_construction__v1.jpg",
        "A classic gharara fitted to the knee with a joined seam there and a wide flare below.",
    ),
    "silhouette_farshi_gharara": full_figure(
        "silhouettes",
        "gaps/Gharara — Farshi.png",
        "A farshi gharara whose flare below the knee joint extends into a floor-pooling train.",
    ),
    "silhouette_slim_modern_gharara": full_figure(
        "silhouettes",
        "05-silhouettes/gharara/sitara__silhouettes_gharara__gharara_construction__v2.jpg",
        "A slim modern gharara keeping the knee joint with a narrower flare below it.",
    ),
    # --- silhouette: sharara (flaring from the waist, no knee joint) ---
    "silhouette_classic_sharara": full_figure(
        "silhouettes",
        "gaps/Sharara — Classic flare.png",
        "A classic sharara: wide trousers flaring evenly from the waist, worn with a kameez.",
    ),
    "silhouette_high_waisted_sharara": full_figure(
        "silhouettes",
        "05-silhouettes/sharara/sitara__silhouettes_sharara__sharara_construction__v1.jpg",
        "A high-waisted sharara whose flare begins at a raised waistline under a short kameez.",
    ),
    "silhouette_farshi_sharara": full_figure(
        "silhouettes",
        "gaps/Farshi.png",
        "A farshi sharara: a waist-flared sharara long enough to pool and trail on the floor.",
    ),
    # --- silhouette: anarkali ---
    "silhouette_floor_length_anarkali": full_figure(
        "silhouettes",
        "05-silhouettes/anarkali/sitara__silhouettes_anarkali__floor_length_anarkali__v1.jpg",
        "A floor-length anarkali flaring from a fitted bodice to the floor.",
    ),
    "silhouette_kalidar_anarkali": full_figure(
        "silhouettes",
        "gaps/Anarkali — Kalidar.png",
        "A kalidar anarkali built from many triangular panels giving an even all-round flare.",
    ),
    "silhouette_front_open_anarkali": full_figure(
        "silhouettes",
        "gaps/front-open.png",
        "A front-open anarkali split down the centre front over trousers.",
    ),
    "silhouette_jacket_style_anarkali": full_figure(
        "silhouettes",
        "gaps/jacket-style.png",
        "A jacket-style anarkali worn as an open layer over an inner garment.",
    ),
    # --- silhouette: shalwar kameez ---
    "silhouette_straight_kameez": full_figure(
        "silhouettes",
        "05-silhouettes/shalwar-kameez/sitara__silhouettes_shalwar_kameez__straight_kameez__v1.jpg",
        "A straight-cut kameez falling in a narrow line from shoulder to hem.",
    ),
    "silhouette_a_line_kameez": full_figure(
        "silhouettes",
        "05-silhouettes/shalwar-kameez/sitara__silhouettes_shalwar_kameez__a_line_kameez__v1.jpg",
        "An A-line kameez widening gradually from the shoulders to the hem.",
    ),
    "silhouette_angrakha_kameez": full_figure(
        "silhouettes",
        "02-ceremonies/Angrakha kameez.png",
        "An angrakha kameez with an overlapping wrapped front fastened to one side.",
    ),
    "silhouette_long_line_kameez": full_figure(
        "silhouettes",
        "gaps/Long-line kameez.png",
        "A long-line kameez cut well below the knee over slim trousers.",
    ),
    # --- neckline_style (close detail on a consistent mannequin) ---
    "neckline_classic_crew": detail(
        "necklines",
        "08-necklines/sitara__necklines__crew__v1.jpg",
        "A classic crew neckline sitting round and high at the base of the neck.",
    ),
    "neckline_curved_scoop": detail(
        "necklines",
        "08-necklines/sitara__necklines__scoop__v1.jpg",
        "A curved scoop neckline dipping in a wide U below the collarbone.",
    ),
    "neckline_v_neck": detail(
        "necklines",
        "08-necklines/sitara__necklines__v_neck__v1.jpg",
        "A moderate V-shaped neckline meeting in a point on the chest.",
    ),
    "neckline_deep_v_neck": detail(
        "necklines",
        "08-necklines/sitara__necklines__deep_v__v1.jpg",
        "A deep V-shaped neckline plunging well below the collarbone.",
    ),
    "neckline_boat_neck": detail(
        "necklines",
        "08-necklines/sitara__necklines__boat__v1.jpg",
        "A boat neckline running wide and shallow across the collarbones.",
    ),
    "neckline_square_neck": detail(
        "necklines",
        "08-necklines/sitara__necklines__square__v1.jpg",
        "A square neckline cut straight across with right-angled corners.",
    ),
    "neckline_sweetheart_neck": detail(
        "necklines",
        "08-necklines/sitara__necklines__sweetheart__v1.jpg",
        "A sweetheart neckline curved into two soft lobes like the top of a heart.",
    ),
    "neckline_high_neck": detail(
        "necklines",
        "08-necklines/sitara__necklines__high_neck__v2.jpg",
        "A high neckline rising to sit close around the neck.",
    ),
    "neckline_band_collar": detail(
        "necklines",
        "08-necklines/sitara__necklines__band_mandarin_collar__v1.jpg",
        "An upright band or mandarin collar standing at the neck with a centre opening.",
    ),
    # --- sleeves ---
    "sleeves_sleeveless": full_figure(
        "sleeves",
        "09-sleeves/sitara__sleeves__sleeveless__v1.jpg",
        "A sleeveless bodice leaving the shoulders and arms bare.",
    ),
    "sleeves_cap_sleeve": detail(
        "sleeves",
        "09-sleeves/sitara__sleeves__short_sleeves__v2.jpg",
        "A cap sleeve on a dress form: a short lace sleeve covering the top of the shoulder "
        "and no more.",
    ),
    "sleeves_elbow_sleeve": full_figure(
        "sleeves",
        "09-sleeves/sitara__sleeves__elbow_sleeves__v1.jpg",
        "An elbow-length sleeve ending at the elbow.",
    ),
    "sleeves_three_quarter_sleeve": full_figure(
        "sleeves",
        "09-sleeves/sitara__sleeves__three_quarter_sleeves__v1.jpg",
        "A three-quarter sleeve ending partway down the forearm.",
    ),
    "sleeves_full_sleeve": full_figure(
        "sleeves",
        "09-sleeves/sitara__sleeves__full_sleeve__v1.jpg",
        "A full-length sleeve reaching the wrist.",
    ),
    # --- back_coverage ---
    "back_coverage_open_back": full_figure(
        "back-coverage",
        "10-back-coverage/sitara__back_coverage__open_back__v1.jpg",
        "An open back leaving most of the back uncovered.",
    ),
    "back_coverage_deep_cut_back": full_figure(
        "back-coverage",
        "10-back-coverage/sitara__back_coverage__deep_cut_back__v1.jpg",
        "A deep-cut back dipping low between the shoulder blades.",
    ),
    "back_coverage_modest_back": full_figure(
        "back-coverage",
        "10-back-coverage/sitara__back_coverage__modest_back__v1.jpg",
        "A modest back covered high across the shoulders.",
    ),
    # --- midriff ---
    "midriff_bare_midriff": full_figure(
        "midriff",
        "11-midriff/sitara__midriff__bare__v1.jpg",
        "A bare midriff between a cropped blouse and the skirt waistline.",
    ),
    "midriff_semi_sheer_midriff": detail(
        "midriff",
        "11-midriff/Semi-sheer midriff.png",
        "A semi-sheer midriff panel veiling the waist in translucent fabric.",
    ),
    "midriff_covered_midriff": full_figure(
        "midriff",
        "11-midriff/sitara__midriff__covered__v1.jpg",
        "A fully covered midriff with no gap between blouse and skirt.",
    ),
    # --- head_covering ---
    "head_covering_uncovered": detail(
        "head-covering",
        "12-head-covering/sitara__head_covering__uncovered__v1.jpg",
        "Hair left uncovered with no veil or dupatta over the head.",
    ),
    "head_covering_dupatta_over_head": detail(
        "head-covering",
        "12-head-covering/sitara__head_covering__dupatta_over_head__v1.jpg",
        "An embellished bridal dupatta drawn up over the head and falling either side of "
        "the face.",
    ),
    "head_covering_veil_style": detail(
        "head-covering",
        "12-head-covering/sitara__head_covering__veil_style__v2.jpg",
        "A veil-style head covering falling over the head and shoulders.",
    ),
    "head_covering_hijab": detail(
        "head-covering",
        "12-head-covering/Hijab — full coverage, no hair shown.png",
        "A hijab covering the hair, neck and shoulders completely.",
    ),
    # --- dupatta_style ---
    "dupatta_one_shoulder": full_figure(
        "dupatta",
        "14-dupatta-styling/sitara__dupatta_styling__one_shoulder__v1.jpg",
        "A dupatta draped over one shoulder and left to fall behind.",
    ),
    "dupatta_front_drape": full_figure(
        "dupatta",
        "14-dupatta-styling/sitara__dupatta_styling__front_drape__v1.jpg",
        "A dupatta drawn across the front of the body.",
    ),
    "dupatta_head_drape": full_figure(
        "dupatta",
        "gaps/Dupatta Over the head.png",
        "A soft, unembellished dupatta taken over the head from the crown and wound once "
        "around the neck.",
    ),
    "dupatta_double_dupatta": full_figure(
        "dupatta",
        "14-dupatta-styling/sitara__dupatta_styling__double_dupatta__v1.jpg",
        "Two dupattas worn together, one over the head and one across the body.",
    ),
    "dupatta_trail_dupatta": full_figure(
        "dupatta",
        "gaps/Dupatta Trail.png",
        "A long dupatta trailing behind like a train.",
    ),
    # --- saree_drape ---
    "saree_drape_nivi_drape": full_figure(
        "saree-drapes",
        "13-saree-drapes/sitara__saree_drapes__nivi_drape__v1.jpg",
        "A nivi drape with waist pleats and the pallu over the left shoulder.",
    ),
    "saree_drape_bengali_drape": full_figure(
        "saree-drapes",
        "13-saree-drapes/sitara__saree_drapes__bengali_drape__v1.jpg",
        "A Bengali drape with broad box pleats and the pallu brought over the shoulder.",
    ),
    "saree_drape_seedha_pallu": full_figure(
        "saree-drapes",
        "13-saree-drapes/sitara__saree_drapes__seedha_pallu__v1.jpg",
        "A seedha pallu drape bringing the pallu forward over the right shoulder.",
    ),
    "saree_drape_lehenga_drape": full_figure(
        "saree-drapes",
        "13-saree-drapes/sitara__saree_drapes__pinned_pleats__v1.jpg",
        "A saree drape with the waist pleats pinned flat so the skirt falls smoothly, the "
        "pallu set over the left shoulder.",
    ),
    # --- ceremony ---
    # These are distinct occasions belonging to distinct traditions, not
    # interchangeable "events": a nikah and an anand karaj are the marriage
    # ceremonies of different faiths, and a walima and a reception are the
    # celebrations that follow different ones. Each alt text names the occasion
    # rather than describing a generic bride.
    "ceremony_nikah": detail(
        "ceremonies",
        "02-ceremonies/Nikah.png",
        "A nikah: the marriage contract open on a table before a flower-dressed stage, "
        "with the two families seated on either side.",
    ),
    "ceremony_mehndi": detail(
        "ceremonies",
        "02-ceremonies/mendhi.png",
        "A mehndi: henna being applied to the bride's hand under a marigold-strung canopy "
        "while guests look on.",
    ),
    "ceremony_baraat": detail(
        "ceremonies",
        "02-ceremonies/Baraat.png",
        "A baraat: the groom's procession arriving on horseback at night through drummers, "
        "dancing guests and falling petals.",
    ),
    "ceremony_walima": detail(
        "ceremonies",
        "02-ceremonies/Walima.png",
        "A walima: a chandelier-lit banquet hall of seated guests facing a floral stage.",
    ),
    "ceremony_pheras": detail(
        "ceremonies",
        "02-ceremonies/Pheras.png",
        "The pheras: a marigold-draped garden mandap with the sacred fire lit at its centre.",
    ),
    "ceremony_anand_karaj": detail(
        "ceremonies",
        "gaps/Anand Karaj.png",
        "An anand karaj: the congregation seated on the floor of a gurdwara before the "
        "canopied Guru Granth Sahib, with the couple at the front.",
    ),
    "ceremony_reception": detail(
        "ceremonies",
        "02-ceremonies/Reception.png",
        "A wedding reception: guests dancing on a lit floor between banquet tables.",
    ),
    # --- regional_style ---
    # Regional influence is optional and non-prescriptive (CLAUDE.md section
    # 12), so each alt text says what the photograph SHOWS and attributes it as
    # an influence — never "this is what a Punjabi bride wears". No option
    # claims to represent a whole community's practice.
    "regional_pakistani": full_figure(
        "cultural-styling",
        "04-cultural-styling/sitara__cultural_styling__pakistani__v2.jpg",
        "Bridalwear drawing on Pakistani influences.",
    ),
    "regional_bangladeshi": full_figure(
        "cultural-styling",
        "04-cultural-styling/sitara__cultural_styling__bangladeshi__v2.jpg",
        "Bridalwear drawing on Bangladeshi influences.",
    ),
    "regional_north_indian": full_figure(
        "cultural-styling",
        "04-cultural-styling/sitara__cultural_styling__north_indian__v2.jpg",
        "Bridalwear drawing on North Indian influences.",
    ),
    "regional_south_indian": full_figure(
        "cultural-styling",
        "04-cultural-styling/sitara__cultural_styling__south_indian__v2.jpg",
        "Bridalwear drawing on South Indian influences.",
    ),
    "regional_punjabi": full_figure(
        "cultural-styling",
        "04-cultural-styling/sitara__cultural_styling__punjabi__v2.jpg",
        "Bridalwear drawing on Punjabi influences.",
    ),
    "regional_gujarati": full_figure(
        "cultural-styling",
        "04-cultural-styling/sitara__cultural_styling__gujarati__v2.jpg",
        "Bridalwear drawing on Gujarati influences.",
    ),
    "regional_rajasthani": full_figure(
        "cultural-styling",
        "04-cultural-styling/sitara__cultural_styling__rajasthani__v1.jpg",
        "Bridalwear drawing on Rajasthani influences.",
    ),
    "regional_hyderabadi": full_figure(
        "cultural-styling",
        "04-cultural-styling/sitara__cultural_styling__hyderabadi__v1.jpg",
        "Bridalwear drawing on Hyderabadi influences.",
    ),
    # --- fabrics (the cloth itself, close enough to read weave and weight) ---
    "fabric_silk": detail(
        "fabrics",
        "06-fabrics/sitara__fabrics__silk.jpg",
        "Silk: a smooth fabric with a fluid drape and a soft sheen.",
    ),
    "fabric_raw_silk": detail(
        "fabrics",
        "06-fabrics/sitara__fabrics__raw_silk__v1.jpg",
        "Raw silk: a crisp fabric with a slubbed, irregular texture and a matte surface.",
    ),
    "fabric_satin": detail(
        "fabrics",
        "06-fabrics/sitara__fabrics__satin.jpg",
        "Satin: a weave with a high-gloss face and a dull reverse.",
    ),
    "fabric_velvet": detail(
        "fabrics",
        "06-fabrics/sitara__fabrics__velvet.jpg",
        "Velvet: a dense pile fabric with deep colour and a soft, shifting nap.",
    ),
    "fabric_organza": detail(
        "fabrics",
        "06-fabrics/sitara__fabrics__organza.jpg",
        "Organza: a sheer, crisp fabric that holds its shape and stands away from the body.",
    ),
    "fabric_chiffon": detail(
        "fabrics",
        "06-fabrics/sitara__fabrics__chiffon.jpg",
        "Chiffon: a sheer, weightless fabric that falls in soft ripples.",
    ),
    "fabric_georgette": detail(
        "fabrics",
        "06-fabrics/sitara__fabrics__georgette__v1.jpg",
        "Georgette: a lightly crinkled fabric with a matte finish and a flowing fall.",
    ),
    "fabric_net": detail(
        "fabrics",
        "06-fabrics/sitara__fabrics__net__v1.jpg",
        "Net: an open mesh fabric, often layered to build volume.",
    ),
    "fabric_brocade": detail(
        "fabrics",
        "06-fabrics/sitara__fabrics__brocade.jpg",
        "Brocade: a heavy fabric with the pattern woven into the cloth in raised relief.",
    ),
    "fabric_jamawar": detail(
        "fabrics",
        "06-fabrics/sitara__fabrics__jamawar__v1.jpg",
        "Jamawar: a richly woven fabric patterned with dense traditional motifs.",
    ),
    "fabric_tissue": detail(
        "fabrics",
        "06-fabrics/sitara__fabrics__tissue__v1.jpg",
        "Tissue: a fine, translucent fabric shot through with metallic thread.",
    ),
    "fabric_cotton_silk": detail(
        "fabrics",
        "06-fabrics/sitara__fabrics__cotton_silk__v1.jpg",
        "Cotton silk: a breathable blend with a gentle sheen and a lighter hand than pure silk.",
    ),
    # --- embellishment_styles (the handwork, close enough to read technique) ---
    "embellishment_zardozi": detail(
        "embroidery",
        "07-embroidery/sitara__embroidery__zardozi__v1.jpg",
        "Zardozi: raised metallic embroidery worked in gold and silver thread.",
    ),
    "embellishment_dabka": detail(
        "embroidery",
        "07-embroidery/sitara__embroidery__dabka__v1.jpg",
        "Dabka: coiled metallic wire couched onto the fabric in dense, textured lines.",
    ),
    "embellishment_nakshi": detail(
        "embroidery",
        "07-embroidery/sitara__embroidery__nakshi__v1.jpg",
        "Nakshi: fine figurative needlework building detailed motifs.",
    ),
    "embellishment_gota_patti": detail(
        "embroidery",
        "07-embroidery/sitara__embroidery__gota_patti__v1.jpg",
        "Gota patti: flat metallic ribbon folded and appliqued into shapes and borders.",
    ),
    "embellishment_mirror_work": detail(
        "embroidery",
        "07-embroidery/sitara__embroidery__mirror_work__v3.jpg",
        "Mirror work: small mirrors stitched into the cloth within embroidered frames.",
    ),
    "embellishment_resham_threadwork": detail(
        "embroidery",
        "07-embroidery/sitara__embroidery__resham__v2.jpg",
        "Resham threadwork: silk-thread embroidery in flat, lustrous colour.",
    ),
    "embellishment_chikankari": detail(
        "embroidery",
        "07-embroidery/sitara__embroidery__chikankari__v1.jpg",
        "Chikankari: fine white-on-white shadow embroidery worked in subtle relief.",
    ),
    "embellishment_sequins": detail(
        "embroidery",
        "07-embroidery/sitara__embroidery__sequins__v1.jpg",
        "Sequins: small flat discs stitched across the surface to catch the light.",
    ),
    "embellishment_pearls": detail(
        "embroidery",
        "07-embroidery/sitara__embroidery__pearls__v1.jpg",
        "Pearls: rounded beads stitched in clusters and trails for a soft lustre.",
    ),
    "embellishment_crystals": detail(
        "embroidery",
        "07-embroidery/sitara__embroidery__crystals__v1.jpg",
        "Crystals: faceted stones set into the work for sharp, bright sparkle.",
    ),
    "embellishment_beads": detail(
        "embroidery",
        "07-embroidery/sitara__embroidery__beadwork__v2.jpg",
        "Beadwork: small beads stitched into patterns with a fine granular texture.",
    ),
    "embellishment_applique": detail(
        "embroidery",
        "07-embroidery/sitara__embroidery__applique__v1.jpg",
        "Applique: shaped pieces of fabric laid onto the ground cloth and stitched down.",
    ),
    # --- embellishment_density ---
    # One consistent garment photographed at three levels of work, so the cards
    # compare the DENSITY rather than three different dresses.
    "density_minimal": detail(
        "embellishment-density",
        "gaps/sitara-minimal.png",
        "Minimal embellishment: sparse work leaving most of the fabric plain.",
    ),
    "density_balanced": detail(
        "embellishment-density",
        "gaps/sitara-balanced.png",
        "Balanced embellishment: worked areas set against clear stretches of fabric.",
    ),
    "density_heavy": detail(
        "embellishment-density",
        "gaps/sitara-heavy.png",
        "Heavy embellishment: dense work covering almost the whole surface.",
    ),
}


def main() -> int:
    unknown_groups = sorted({v.group for v in VISUALS.values()} - set(GROUP_ASPECT))
    if unknown_groups:
        print(f"no aspect declared for group(s): {', '.join(unknown_groups)}", file=sys.stderr)
        return 1

    missing = [
        f"{key} -> {visual.source}"
        for key, visual in sorted(VISUALS.items())
        if not (SOURCE_ROOT / visual.source).is_file()
    ]
    if missing:
        print(f"{len(missing)} source image(s) missing under {SOURCE_ROOT}:", file=sys.stderr)
        for entry in missing:
            print(f"  {entry}", file=sys.stderr)
        return 1

    # Render everything BEFORE touching the tree, so the failure this is most
    # likely to hit — an undecodable or missing source — aborts with the
    # committed assets untouched. ~2 MB of encoded bytes is a cheap price for
    # that. Note the limit of the guarantee: the disk phase below is a sequence
    # of independent writes, so an interrupt or a full disk part-way through it
    # CAN leave the tree and asset-integrity.json describing different states.
    # The backstop for that is CI, which rebuilds both pipelines and fails on
    # any diff — not this ordering.
    rendered: list[tuple[str, Visual, bytes]] = [
        (key, visual, render(SOURCE_ROOT / visual.source, ASPECTS[visual.aspect], visual.focus))
        for key, visual in sorted(VISUALS.items())
    ]

    # Every entry succeeded: remove the previous generation (so a deleted mapping
    # entry cannot leave an orphan being served) and write the new one. Only this
    # script's own output shapes are ever touched.
    if OUTPUT_ROOT.is_dir():
        for stale in sorted(OUTPUT_ROOT.rglob("*")):
            if stale.is_file() and stale.suffix in (".webp", ".svg"):
                stale.unlink()

    integrity: dict[str, dict[str, object]] = {}
    total_bytes = 0
    for key, visual, payload in rendered:
        # The output filename is the visual key, so the served path reads as
        # /questionnaire-visuals/<group>/<visual_key>.webp.
        filename = f"{key}.webp"
        destination = OUTPUT_ROOT / visual.group / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        width, height = ASPECTS[visual.aspect]
        total_bytes += len(payload)
        integrity[key] = {
            "path": f"/questionnaire-visuals/{visual.group}/{filename}",
            "width": width,
            "height": height,
            "sha256": digest(payload),
            "alt": visual.alt,
            "source": visual.source,
        }

    # newline="\n" explicitly: without it Python rewrites "\n" to "\r\n" on
    # Windows, so the same inputs would produce different bytes per platform.
    INTEGRITY_PATH.write_text(
        json.dumps(integrity, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {len(integrity)} visuals ({total_bytes / 1024:.0f} KB) to {OUTPUT_ROOT}")
    print(f"wrote {INTEGRITY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
