"""Bounded, source-controlled phrase maps for the demo DesignSpec and
refinement engines (Phase 15 Part B).

Every map is keyed by a questionnaire v1 machine value (see
:mod:`sitara.generation.demo.manifest` for the same controlled vocabulary).
The questionnaire remains the sole authority over which combination of
values a user may submit — these maps only supply the deterministic
narrative language the demo engines compose into a DesignSpec. Nothing here
performs validation."""

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
    "blush": "soft blush",
    "pink": "pink",
    "peach": "peach",
    "orange": "warm orange",
    "yellow": "sunlit yellow",
    "green": "green",
    "emerald": "emerald",
    "teal": "teal",
    "blue": "blue",
    "navy": "navy",
    "purple": "purple",
    "gold": "gold",
    "silver": "silver",
    "champagne": "champagne",
    "beige": "beige",
    "brown": "brown",
    "black": "black",
    "multicolour": "a considered multicolour mix",
}

FABRIC_PHRASES: dict[str, str] = {
    "silk": "silk",
    "raw_silk": "raw silk",
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

DUPATTA_PHRASES: dict[str, str] = {
    "head_drape": "draped over the head",
    "one_shoulder": "carried over one shoulder",
    "both_shoulders": "draped across both shoulders",
    "front_drape": "draped across the front",
    "double_dupatta": "styled as a double dupatta",
    "cape_drape": "styled in a cape-like drape",
    "arm_drape": "resting loosely along the arms",
}

SAREE_DRAPE_PHRASES: dict[str, str] = {
    "nivi_drape": "a nivi drape, pleated at the front with the pallu over the left shoulder",
    "seedha_pallu": "a seedha pallu drape, brought forward over the right shoulder",
    "bengali_drape": "a Bengali-style drape with wide box pleats",
    "open_pallu": "an open, unpleated flowing pallu",
    "pinned_pleats": "crisp, pre-set pleats pinned for a structured look",
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
