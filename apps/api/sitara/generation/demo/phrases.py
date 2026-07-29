"""Bounded, source-controlled phrase maps for the demo DesignSpec and
refinement engines (Phase 15 Part B).

Every map is keyed by a questionnaire machine value (see
:mod:`sitara.generation.demo.manifest` for the same controlled vocabulary).
The questionnaire remains the sole authority over which combination of
values a user may submit — these maps only supply the deterministic
narrative language the demo engines compose into a DesignSpec. Nothing here
performs validation.

Values from every published questionnaire version live side by side: a design
answered on an older version must keep rendering its own vocabulary, so newer
values are ADDED here and older ones are never removed."""

GARMENT_PHRASES: dict[str, dict[str, str]] = {
    "lehenga": {
        "noun": "lehenga",
        "overall_form": (
            "a fitted choli paired with a full lehenga skirt and a coordinating dupatta"
        ),
        "components": "Fitted choli blouse|Lehenga skirt|Coordinating dupatta",
        "key_proportions": (
            "a close-fitted bodice, a defined waist and a skirt that carries the "
            "visual weight of the outfit"
        ),
    },
    "saree": {
        "noun": "saree",
        "overall_form": (
            "a single length of fabric draped over a fitted blouse, with the drape "
            "itself shaping the silhouette"
        ),
        "components": "Fitted saree blouse|Draped saree length|Pallu",
        "key_proportions": (
            "a fitted blouse balanced against the flowing volume of the drape and pallu"
        ),
    },
    "gharara": {
        "noun": "gharara",
        "overall_form": (
            "a short kurti over a gharara that is fitted through the upper leg and "
            "knee before flaring dramatically below the knee"
        ),
        "components": "Short kurti|Gharara trousers|Dupatta",
        "key_proportions": (
            "a fitted upper leg giving way to a dramatic below-knee flare, kept "
            "distinct from a sharara"
        ),
    },
    "sharara": {
        "noun": "sharara",
        "overall_form": (
            "a kurti over sharara trousers that flare broadly from the waist or "
            "upper leg all the way down"
        ),
        "components": "Kurti|Sharara trousers|Dupatta",
        "key_proportions": (
            "an even, broad flare running the full length of the leg from waist "
            "or upper leg down"
        ),
    },
    "anarkali": {
        "noun": "anarkali",
        "overall_form": (
            "a fitted bodice flowing into a frock-style anarkali silhouette, worn over churidar"
        ),
        "components": "Fitted bodice|Flared anarkali panel|Churidar|Dupatta",
        "key_proportions": (
            "a close upper bodice opening into a continuous flared sweep from the chest down"
        ),
    },
    "shalwar_kameez": {
        "noun": "shalwar kameez",
        "overall_form": "a kameez worn over shalwar trousers, styled with a dupatta",
        "components": "Kameez|Shalwar trousers|Dupatta",
        "key_proportions": (
            "a straightforward, comfortable balance between the kameez length "
            "and the shalwar's ease"
        ),
    },
}

CEREMONY_PHRASES: dict[str, str] = {
    "nikah": "a nikah",
    "mehndi": "a mehndi celebration",
    "baraat": "a baraat",
    "walima": "a walima",
    "pheras": "the pheras",
    "anand_karaj": "an Anand Karaj",
    "reception": "a reception",
}

SILHOUETTE_PHRASES: dict[str, str] = {
    "flared_lehenga": "a voluminous, circular flare through the skirt",
    "a_line_lehenga": "a cleaner flare that falls in a gentle A-line",
    "mermaid_lehenga": "a fitted line through the hip and thigh, flaring below the knee",
    "classic_saree_drape": "a classic drape carrying the whole visual line",
    "lehenga_style_saree": "a pre-styled drape that evokes a lehenga while remaining a saree",
    "gharara_construction": "a fitted upper leg opening into a dramatic below-knee flare",
    "sharara_construction": "trousers flaring broadly from the waist or upper leg down",
    "floor_length_anarkali": "a floor-length frock sweep from a fitted bodice",
    "knee_length_anarkali": "a shorter frock silhouette over churidar",
    "straight_kameez": "a clean, straight-falling kameez",
    "a_line_kameez": "a kameez that opens gently from the waist",
    # Questionnaire v4 replaced the two generic gharara/sharara constructions
    # and added per-garment silhouettes. The two constructions above are kept so
    # a v1/v3-answered design still reads correctly.
    "straight_lehenga": "a narrow, straight-falling skirt with very little flare",
    "panelled_kali_lehenga": "a skirt built from vertical kali panels that swing as they flare",
    "pre_stitched_saree": "a pre-stitched drape that keeps the saree's line without pinning",
    "half_saree": "a half-saree pairing a skirt and blouse with a draped upper length",
    "classic_gharara": "a classic gharara fitted to the knee before its below-knee flare",
    "farshi_gharara": "a farshi gharara whose below-knee flare pools along the floor",
    "slim_modern_gharara": "a slimmer modern gharara with a restrained below-knee flare",
    "classic_sharara": "classic sharara trousers flaring evenly from the upper leg",
    "high_waisted_sharara": "high-waisted sharara trousers flaring from a raised waistline",
    "farshi_sharara": "a farshi sharara whose flare lengthens into a floor-sweeping fall",
    "kalidar_anarkali": "a kalidar anarkali built from many tapering panels",
    "front_open_anarkali": "a front-open anarkali layered over an inner garment",
    "jacket_style_anarkali": "a jacket-style anarkali worn open over its base layer",
    "angrakha_kameez": "an angrakha kameez crossed over and tied at the side",
    "long_line_kameez": "a long-line kameez falling well below the knee",
}

REGIONAL_PHRASES: dict[str, str] = {
    "pakistani": "a broad Pakistani bridal influence",
    "bangladeshi": "a broad Bangladeshi bridal influence",
    "north_indian": "a broad North Indian bridal influence",
    "south_indian": "a broad South Indian bridal influence",
    "punjabi": "a broad Punjabi bridal influence",
    "gujarati": "a broad Gujarati bridal influence",
    "rajasthani": "a broad Rajasthani bridal influence",
    "hyderabadi": "a broad Hyderabadi bridal influence",
}

COLOUR_PHRASES: dict[str, str] = {
    "ivory": "ivory",
    "white": "white",
    "red": "red",
    "maroon": "deep maroon",
    "ruby": "ruby",
    "burgundy": "burgundy",
    "blush": "soft blush",
    "pink": "pink",
    "peach": "peach",
    "coral": "coral",
    "rose": "rose",
    "dusty_rose": "dusty rose",
    "orange": "warm orange",
    "yellow": "sunlit yellow",
    "gold": "gold",
    "silver": "silver",
    "bronze": "bronze",
    "copper": "copper",
    "green": "green",
    "emerald": "emerald",
    "sage": "sage",
    "mint": "mint",
    "olive": "olive",
    "forest_green": "forest green",
    "teal": "teal",
    "blue": "blue",
    "navy": "navy",
    "turquoise": "turquoise",
    "powder_blue": "powder blue",
    "royal_blue": "royal blue",
    "purple": "purple",
    "lavender": "lavender",
    "lilac": "lilac",
    "plum": "plum",
    "mauve": "mauve",
    "champagne": "champagne",
    "beige": "beige",
    "taupe": "taupe",
    "brown": "brown",
    "black": "black",
    "multicolour": "a considered multicolour mix",
    # Questionnaire v4's grouped colour vocabulary. Added alongside the earlier
    # values rather than replacing them: a v1/v3-answered design still needs its
    # own colours to render.
    "scarlet": "scarlet",
    "deep_maroon": "deep maroon",
    "oxblood": "oxblood",
    "rust": "rust",
    "rani_pink": "rani pink",
    "old_rose": "old rose",
    "marigold": "marigold",
    "antique_gold": "antique gold",
    "mehndi_green": "mehndi green",
    "pistachio": "pistachio",
    "peacock": "peacock blue",
    "aubergine": "aubergine",
    "amethyst": "amethyst",
    "plum_wine": "wine plum",
    "silver_grey": "silver grey",
    "pearl": "pearl",
}

FABRIC_PHRASES: dict[str, str] = {
    "silk": "silk",
    "raw_silk": "raw silk",
    "satin": "satin",
    "velvet": "velvet",
    "organza": "organza",
    "chiffon": "chiffon",
    "georgette": "georgette",
    "net": "net",
    "brocade": "brocade",
    "jamawar": "jamawar",
    "tissue": "tissue",
    "cotton_silk": "cotton silk",
}

EMBELLISHMENT_PHRASES: dict[str, str] = {
    "zardozi": "zardozi metal-thread work",
    "dabka": "dabka coiled-wire detailing",
    "nakshi": "nakshi threadwork",
    "gota_patti": "gota patti appliqué",
    "mirror_work": "mirror work",
    "resham_threadwork": "resham threadwork",
    "chikankari": "chikankari embroidery",
    "sequins": "sequin work",
    "pearls": "pearl detailing",
    "crystals": "crystal embellishment",
    "beads": "beadwork",
    "applique": "appliqué work",
    "none": "a clean, unembellished finish",
}

DENSITY_PHRASES: dict[str, str] = {
    "minimal": "kept minimal and airy",
    "balanced": "kept balanced, present without overwhelming",
    "heavy": "worked richly and heavily",
}

COVERAGE_PHRASES: dict[str, str] = {
    "sleeveless": "left sleeveless",
    "short_sleeves": "finished with short sleeves",
    "elbow_sleeves": "finished with elbow-length sleeves",
    "three_quarter_sleeves": "finished with three-quarter sleeves",
    "full_sleeves": "finished with full-length sleeves",
    "high_neckline": "given a modest, higher neckline",
    "full_back": "given full back coverage",
    "full_midriff": "given full midriff coverage",
    "head_drape_preferred": "styled with the head covered",
}

# The dedicated canonical neckline (Phase 16B / DesignSpec v2). Keyed by the
# neckline_style machine value; supplies the deterministic neckline narrative
# the demo engine renders into coverage_and_drape.neckline.
NECKLINE_PHRASES: dict[str, str] = {
    "classic_crew": "a classic crew neckline sitting at the base of the neck",
    "curved_scoop": "a softly curved scoop neckline dipping just below the collarbone",
    "v_neck": "a moderate V-shaped neckline",
    "deep_v_neck": "a deep V-shaped neckline plunging below the collarbone",
    "boat_neck": "a wide boat neckline running straight across from shoulder to shoulder",
    "square_neck": "a clean square neckline cut across the chest",
    "sweetheart_neck": "a sweetheart neckline curved like the top of a heart",
    "high_neck": "a fully closed high neckline covering the collarbone and upper chest",
    "band_collar": "an upright band or mandarin collar standing at the neck",
}

DUPATTA_PHRASES: dict[str, str] = {
    "head_drape": "draped over the head",
    "one_shoulder": "carried over one shoulder",
    "both_shoulders": "draped across both shoulders",
    "front_drape": "draped across the front",
    "double_dupatta": "styled as a double dupatta",
    "cape_drape": "styled in a cape-like drape",
    "arm_drape": "resting loosely along the arms",
    # Questionnaire v4.
    "trail_dupatta": "left to trail behind in a long sweep",
}

SAREE_DRAPE_PHRASES: dict[str, str] = {
    "nivi_drape": "a nivi drape, pleated at the front with the pallu over the left shoulder",
    "seedha_pallu": "a seedha pallu drape, brought forward over the right shoulder",
    "bengali_drape": "a Bengali-style drape with wide box pleats",
    "open_pallu": "an open, unpleated flowing pallu",
    "pinned_pleats": "crisp, pre-set pleats pinned for a structured look",
    # Questionnaire v4.
    "lehenga_drape": "a lehenga-style drape worn over a skirted lower half",
}

# Questionnaire v4 replaced the single coverage_preferences multi-select with one
# question per body area, so each area gets its own phrase map. Every value is a
# real answer — including the less-covered ones — because the user chose it
# explicitly rather than by omission.
SLEEVE_PHRASES: dict[str, str] = {
    "sleeveless": "left sleeveless",
    "cap_sleeve": "finished with short cap sleeves",
    "elbow_sleeve": "finished with elbow-length sleeves",
    "three_quarter_sleeve": "finished with three-quarter sleeves",
    "full_sleeve": "finished with full-length sleeves",
}

BACK_COVERAGE_PHRASES: dict[str, str] = {
    "open_back": "left open at the back",
    "deep_cut_back": "cut deeply open at the back",
    "modest_back": "given full back coverage",
}

MIDRIFF_PHRASES: dict[str, str] = {
    "bare_midriff": "left bare at the waist",
    "semi_sheer_midriff": "veiled in sheer fabric at the waist rather than left bare",
    "covered_midriff": "given full midriff coverage, with no bare skin at the waist",
}

HEAD_COVERING_PHRASES: dict[str, str] = {
    "uncovered": "left uncovered",
    "dupatta_over_head": "kept covered, with the dupatta drawn up and over it",
    "veil_style": "kept covered by a veil worn over the hair",
    "hijab": "kept covered by a hijab over the hair and neck",
}

# A small, deliberately bounded allowlist of style-adjective keywords a demo
# engine may recognise in untrusted free text — never copied into output,
# only used to bias which curated variant is selected. Anything outside this
# set has no special effect beyond folding into the deterministic variant
# fingerprint (see sitara.generation.demo.design_spec_engine).
ALLOWED_STYLE_KEYWORDS: dict[str, str] = {
    "minimal": "minimal",
    "understated": "minimal",
    "bold": "bold",
    "dramatic": "bold",
    "regal": "regal",
    "elegant": "regal",
    "modern": "modern",
    "contemporary": "modern",
    "vintage": "vintage",
    "traditional": "vintage",
    "pastel": "pastel",
    "soft": "pastel",
}
