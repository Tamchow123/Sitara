"""Derive the questionnaire v4 fixture from v3.

v4 is an ADDITIVE new version: v1 (active) and v3 keep their own schemas and
their persisted answers are read through them unchanged. Nothing here rewrites
history — running this script only regenerates ``questionnaire_v4.json``.

Two shapes change relative to v3, both from the design handoff:

1. The single ``colour_palette`` (multi_choice, max 4) becomes three
   per-role ``colour_choice`` questions plus a ``custom_colours`` palette.
2. The single ``coverage_preferences`` (multi_choice, max 6), which conflated
   sleeves, back, midriff and head covering into one list, becomes four
   independent ``single_choice`` questions.

Silhouettes expand from 11 to 23, garment-dependent as before.

Run:  python apps/api/sitara/questionnaire/fixtures/build_v4.py
It is deterministic: re-running produces a byte-identical file.
"""

import json
import pathlib

HERE = pathlib.Path(__file__).parent
V3 = HERE / "questionnaire_v3.json"
V4 = HERE / "questionnaire_v4.json"

# --- the handoff's curated palette: 7 groups of 4 --------------------------
# Hex values deliberately live in the FRONTEND visuals manifest, not here: the
# schema carries stable machine ids and a presentation `group` only, exactly as
# v3's colour_palette did. A colour's appearance is presentation; its identity
# is the contract.
COLOUR_GROUPS = [
    (
        "reds_maroons",
        [
            ("scarlet", "Scarlet"),
            ("deep_maroon", "Deep maroon"),
            ("oxblood", "Oxblood"),
            ("rust", "Rust"),
        ],
    ),
    (
        "pinks_roses",
        [
            ("blush", "Blush"),
            ("rose", "Rose"),
            ("rani_pink", "Rani pink"),
            ("old_rose", "Old rose"),
        ],
    ),
    (
        "golds_ivories",
        [
            ("marigold", "Marigold"),
            ("antique_gold", "Antique gold"),
            ("champagne", "Champagne"),
            ("ivory", "Ivory"),
        ],
    ),
    (
        "greens",
        [
            ("emerald", "Emerald"),
            ("mehndi_green", "Mehndi green"),
            ("sage", "Sage"),
            ("pistachio", "Pistachio"),
        ],
    ),
    (
        "blues",
        [
            ("royal_blue", "Royal blue"),
            ("navy", "Navy"),
            ("peacock", "Peacock"),
            ("powder_blue", "Powder blue"),
        ],
    ),
    (
        "purples_wines",
        [
            ("aubergine", "Aubergine"),
            ("amethyst", "Amethyst"),
            ("lilac", "Lilac"),
            ("plum_wine", "Plum wine"),
        ],
    ),
    (
        "silvers_pastels",
        [
            ("silver_grey", "Silver grey"),
            ("pearl", "Pearl"),
            ("mint", "Mint"),
            ("peach", "Peach"),
        ],
    ),
]


def colour_options(extra=()):
    options = list(extra)
    for group, colours in COLOUR_GROUPS:
        for value, label in colours:
            options.append(
                {
                    "value": value,
                    "label": label,
                    "visual_key": f"colour_{value}",
                    "group": group,
                }
            )
    return options


# --- garment-dependent silhouettes (handoff) ------------------------------
# Ids reuse v3's wherever the concept is identical, so a shape means the same
# thing across versions; only genuinely new shapes get new ids.
SILHOUETTES = {
    "lehenga": [
        (
            "a_line_lehenga",
            "A-line",
            "Skims outward gently from the waist in a clean A - light to wear and "
            "universally flattering.",
        ),
        (
            "flared_lehenga",
            "Full flare",
            "A circular skirt with maximum sweep - made for twirling and grand entrances.",
        ),
        (
            "mermaid_lehenga",
            "Mermaid",
            "Fitted through the hips and flaring below the knee - modern and sculptural.",
        ),
        (
            "straight_lehenga",
            "Straight cut",
            "Falls straight and sleek, often with a slit for movement - quietly contemporary.",
        ),
        (
            "panelled_kali_lehenga",
            "Panelled kali",
            "Traditional stitched panels (kalis) give heirloom structure and fullness.",
        ),
    ],
    "saree": [
        (
            "classic_saree_drape",
            "Classic saree",
            "The timeless free-draped saree - pleats at the front, pallu over the shoulder.",
        ),
        (
            "lehenga_style_saree",
            "Lehenga saree",
            "Pre-pleated to fall like a lehenga skirt - the drama of a saree, none of the draping.",
        ),
        (
            "pre_stitched_saree",
            "Pre-stitched",
            "Sewn into place so you simply step in - effortless for long celebrations.",
        ),
        (
            "half_saree",
            "Half saree",
            "The langa-voni: skirt, blouse and a draped voni - a South Indian favourite.",
        ),
    ],
    "gharara": [
        (
            "classic_gharara",
            "Classic",
            "The traditional knee-flare gharara with a short kurti - timeless Lucknowi grace.",
        ),
        (
            "farshi_gharara",
            "Farshi",
            "Floor-trailing and voluminous - the grandest, most regal form of the gharara.",
        ),
        (
            "slim_modern_gharara",
            "Slim modern",
            "A pared-back flare with a longer kameez - a contemporary take.",
        ),
    ],
    "sharara": [
        (
            "classic_sharara",
            "Classic flare",
            "Loose from the waist with a generous sweep - the traditional sharara.",
        ),
        (
            "high_waisted_sharara",
            "High-waisted",
            "Fitted at the natural waist before flaring - elongating and modern.",
        ),
        ("farshi_sharara", "Farshi", "Extra-long, floor-pooling flare in the old courtly style."),
    ],
    "anarkali": [
        (
            "floor_length_anarkali",
            "Floor-length",
            "The flare falls all the way to the floor - a gown-like sweep.",
        ),
        (
            "kalidar_anarkali",
            "Kalidar",
            "Built from many stitched panels for structured, traditional fullness.",
        ),
        (
            "front_open_anarkali",
            "Front-open",
            "Opens over a slim inner layer, like a flowing jacket.",
        ),
        (
            "jacket_style_anarkali",
            "Jacket style",
            "A longer structured jacket layered over the flare - regal and warm.",
        ),
    ],
    "shalwar_kameez": [
        (
            "straight_kameez",
            "Straight",
            "A clean, straight-falling kameez - understated and elegant.",
        ),
        ("a_line_kameez", "A-line", "Flares softly from the bust for gentle movement."),
        ("angrakha_kameez", "Angrakha", "A crossover, wrap-tied bodice in the Mughal court style."),
        ("long_line_kameez", "Long-line", "An ankle-grazing kameez with a stately, columnar fall."),
    ],
}

# --- the four coverage questions that replace coverage_preferences --------
COVERAGE = [
    (
        "sleeves",
        "How much sleeve?",
        "sleeves",
        [
            (
                "sleeveless",
                "Sleeveless",
                "No sleeve - shoulders bare, ideal under a heavy dupatta.",
            ),
            ("cap_sleeve", "Cap sleeve", "A whisper of a sleeve, just covering the shoulder."),
            ("elbow_sleeve", "Elbow", "Ends at the elbow - balanced and comfortable."),
            (
                "three_quarter_sleeve",
                "Three-quarter",
                "Ends mid-forearm, leaving room for bangles and mehndi.",
            ),
            (
                "full_sleeve",
                "Full sleeve",
                "To the wrist - elegant coverage and a canvas for embroidery.",
            ),
        ],
    ),
    (
        "back_coverage",
        "How should the back be cut?",
        "back_coverage",
        [
            ("open_back", "Open back", "A deep open back - dramatic from every angle."),
            ("deep_cut_back", "Deep cut", "A generous scoop, softened with dori ties or latkans."),
            ("modest_back", "Modest", "A high, covered back - comfortable and traditional."),
        ],
    ),
    (
        "midriff",
        "And the midriff?",
        "midriff",
        [
            (
                "bare_midriff",
                "Bare",
                "A classic gap between blouse and skirt - traditional for sarees and lehengas.",
            ),
            (
                "semi_sheer_midriff",
                "Semi-sheer",
                "Veiled with net or organza - coverage with lightness.",
            ),
            ("covered_midriff", "Covered", "Fully covered by a longer bodice or kameez."),
        ],
    ),
    (
        "head_covering",
        "Will you cover your head?",
        "head_covering",
        [
            (
                "uncovered",
                "Uncovered",
                "Hair dressed and open - the dupatta stays on the shoulders.",
            ),
            (
                "dupatta_over_head",
                "Dupatta over head",
                "The dupatta drawn gracefully over the head - traditional for many ceremonies.",
            ),
            (
                "veil_style",
                "Veil style",
                "A long, trailing veil-style dupatta pinned from the crown.",
            ),
            (
                "hijab",
                "Hijab",
                "Full coverage with the hair completely covered - styled to sit with the outfit.",
            ),
        ],
    ),
]

# --- compatibility rules the coverage split has to re-express -------------
# v3 carried two of these against the old multi-select `coverage_preferences`.
# Splitting that list into four independent questions dropped both conditions
# (they named a question v4 no longer has), so they are restated here against
# the new question ids — the phase requires that a bypassed contradictory
# submission is REJECTED by the server, not merely discouraged by the UI.
#
# The distinction that matters culturally: a restriction applies only when the
# DUPATTA ITSELF is doing the covering. A hijab covers the hair independently,
# so a bride wearing one may still style her dupatta any way she likes —
# restricting her to a head drape would be the app inventing a rule no
# community holds.
HEAD_COVERING_RULES = [
    {
        # "The dupatta stays on the shoulders" cannot also frame the face.
        "id": "uncovered_head_excludes_head_drapes",
        "when": {"question_id": "head_covering", "operator": "in", "values": ["uncovered"]},
        "then": {
            "action": "restrict_options",
            "question_id": "dupatta_style",
            "values": ["one_shoulder", "front_drape", "trail_dupatta"],
        },
    },
    {
        # The dupatta is the covering, so it has to be over the head.
        "id": "dupatta_over_head_requires_head_drape",
        "when": {
            "question_id": "head_covering",
            "operator": "in",
            "values": ["dupatta_over_head"],
        },
        "then": {
            "action": "restrict_options",
            "question_id": "dupatta_style",
            "values": ["head_drape", "double_dupatta"],
        },
    },
    {
        # Pinned from the crown and trailing — a trail drape satisfies this one
        # too, which a plain head drape does not.
        "id": "veil_style_requires_crown_pinned_drape",
        "when": {"question_id": "head_covering", "operator": "in", "values": ["veil_style"]},
        "then": {
            "action": "restrict_options",
            "question_id": "dupatta_style",
            "values": ["head_drape", "double_dupatta", "trail_dupatta"],
        },
    },
]

# Mirrors v3's `full_midriff_excludes_deep_v_neck` exactly, including its
# judgement that a sweetheart neckline is NOT a contradiction — only the
# plunging deep V is.
MIDRIFF_NECKLINE_RULE = {
    "id": "covered_midriff_excludes_deep_v_neck",
    "when": {"question_id": "midriff", "operator": "in", "values": ["covered_midriff"]},
    "then": {
        "action": "restrict_options",
        "question_id": "neckline_style",
        "values": [
            "classic_crew",
            "curved_scoop",
            "v_neck",
            "boat_neck",
            "square_neck",
            "sweetheart_neck",
            "high_neck",
            "band_collar",
        ],
    },
}

SAREE_DRAPES = [
    (
        "nivi_drape",
        "Nivi drape",
        "Front-tucked pleats with the pallu over the left shoulder - the timeless classic.",
    ),
    (
        "bengali_drape",
        "Bengali atpoure",
        "Broad box pleats with the pallu wrapped around both shoulders.",
    ),
    (
        "seedha_pallu",
        "Seedha pallu",
        "The pallu brought over the right shoulder to fan across the front.",
    ),
    (
        "lehenga_drape",
        "Lehenga drape",
        "Pleats fanned like a skirt with a fitted wrap - a modern shortcut.",
    ),
]

DUPATTA_STYLES = [
    ("one_shoulder", "One shoulder", "Pinned at one shoulder to fall in a single clean cascade."),
    (
        "front_drape",
        "Seedha pallu",
        "Draped across the front from the right shoulder - displays the work fully.",
    ),
    (
        "head_drape",
        "Over the head",
        "Framing the face from the crown - soft, bridal and traditional.",
    ),
    (
        "double_dupatta",
        "Double dupatta",
        "One framing the head and shoulders, a second trailing - grand and layered.",
    ),
    ("trail_dupatta", "Trail", "Pinned to sweep behind you like a train."),
]


def choice_options(rows, visual_prefix):
    return [
        {
            "value": value,
            "label": label,
            "description": description,
            "visual_key": f"{visual_prefix}_{value}",
        }
        for value, label, description in rows
    ]


def build():
    v3 = json.loads(V3.read_text(encoding="utf-8"))
    schema = v3[0]["fields"]["schema"]

    questions = {}
    for step in schema["steps"]:
        for question in step["questions"]:
            questions[question["id"]] = question

    # --- silhouette: same question, expanded garment-dependent options -----
    silhouette = questions["silhouette"]
    silhouette["options"] = [
        {
            "value": value,
            "label": label,
            "description": description,
            "visual_key": f"silhouette_{value}",
        }
        for garment_shapes in SILHOUETTES.values()
        for value, label, description in garment_shapes
    ]

    # --- the three colour questions + the custom palette -------------------
    colour_step = {
        "id": "colours",
        "title": "Which colours are yours?",
        "questions": [
            {
                "id": "fabric_colour",
                "type": "colour_choice",
                "label": "The fabric",
                "help_text": "The colour the cloth itself is dyed.",
                "required": False,
                "options": colour_options(),
                "constraints": {"allow_custom": True},
            },
            {
                "id": "embroidery_colour",
                "type": "colour_choice",
                "label": "The embroidery",
                "help_text": "The colour the handwork is stitched in.",
                "required": False,
                "options": colour_options(),
                "constraints": {"allow_custom": True},
            },
            {
                "id": "dupatta_colour",
                "type": "colour_choice",
                "label": "The dupatta",
                "help_text": "Match the fabric, or set the dupatta apart in its own colour.",
                "required": False,
                "options": colour_options(
                    extra=[
                        {
                            "value": "match_fabric",
                            "label": "Match the fabric",
                            "description": "The dupatta takes the same colour as the fabric.",
                            "visual_key": "colour_match_fabric",
                            "group": "match",
                        }
                    ]
                ),
                "constraints": {"allow_custom": True},
            },
            {
                "id": "custom_colours",
                "type": "colour_list",
                "label": "Your own colours",
                "help_text": (
                    "Colours you have added yourself. They stay available across "
                    "all three choices above."
                ),
                "required": False,
                "constraints": {"max_items": 8},
            },
        ],
    }

    # --- four coverage questions replacing coverage_preferences ------------
    coverage_questions = [
        {
            "id": question_id,
            "type": "single_choice",
            "label": label,
            "required": False,
            "options": choice_options(rows, visual_prefix),
        }
        for question_id, label, visual_prefix, rows in COVERAGE
    ]

    saree_drape = questions["saree_drape"]
    saree_drape["options"] = choice_options(SAREE_DRAPES, "saree_drape")
    dupatta_style = questions["dupatta_style"]
    dupatta_style["options"] = choice_options(DUPATTA_STYLES, "dupatta")

    steps = [
        questions_step
        for questions_step in (
            {
                "id": "garment_and_ceremony",
                "title": "Your garment and occasion",
                "questions": [questions["ceremony"], questions["garment_type"]],
            },
            {
                "id": "cultural_direction",
                "title": "Cultural styling direction",
                "questions": [questions["regional_style"]],
            },
            {
                "id": "silhouette",
                "title": "Silhouette",
                "questions": [silhouette],
            },
            colour_step,
            {
                "id": "cloth_and_handwork",
                "title": "Cloth and handwork",
                "questions": [
                    questions["fabrics"],
                    questions["embellishment_styles"],
                    questions["embellishment_density"],
                ],
            },
            {
                "id": "coverage",
                "title": "Coverage",
                "questions": [questions["neckline_style"], *coverage_questions],
            },
            {
                "id": "the_drape",
                "title": "The drape",
                "questions": [dupatta_style, saree_drape],
            },
            {
                "id": "final_notes",
                "title": "Anything else?",
                "questions": [questions["final_notes"]],
            },
        )
    ]

    # --- rules -------------------------------------------------------------
    # Every v3 rule that referenced a question v4 removed is dropped; the
    # garment-dependent silhouette restrictions are regenerated from the
    # SILHOUETTES table so the two can never drift.
    rules = [
        rule
        for rule in schema["rules"]
        if rule["then"]["question_id"] not in {"silhouette", "coverage_preferences"}
        and rule["when"]["question_id"] not in {"coverage_preferences", "colour_palette"}
    ]
    for garment, shapes in SILHOUETTES.items():
        rules.append(
            {
                "id": f"{garment}_silhouettes",
                "when": {"question_id": "garment_type", "operator": "equals", "values": [garment]},
                "then": {
                    "action": "restrict_options",
                    "question_id": "silhouette",
                    "values": [value for value, _, _ in shapes],
                },
            }
        )
    # A saree has no dupatta, so its colour question does not apply either.
    rules.append(
        {
            "id": "saree_hides_dupatta_colour",
            "when": {"question_id": "garment_type", "operator": "equals", "values": ["saree"]},
            "then": {"action": "hide", "question_id": "dupatta_colour"},
        }
    )
    rules.extend(HEAD_COVERING_RULES)
    rules.append(MIDRIFF_NECKLINE_RULE)

    schema["steps"] = steps
    schema["rules"] = rules

    return {
        "model": v3[0]["model"],
        "pk": "6b1f0f3e-5c2a-4d8b-9f77-2a4c0d9e51b4",
        "fields": {
            **v3[0]["fields"],
            "version": 4,
            "status": "draft",
            "schema": schema,
        },
    }


def render() -> str:
    """The exact text ``questionnaire_v4.json`` should contain.

    Kept separate from writing the file so a test can assert the committed
    fixture still equals a fresh render — the same freshness guard the OpenAPI
    schema and the DesignSpec schema export already have. Without it,
    CLAUDE.md section 18's "never hand-edit generated files" would rest on
    manual diligence alone for a 1500-line JSON file.
    """
    return json.dumps([build()], indent=2, ensure_ascii=False) + "\n"


def write() -> None:
    V4.write_text(render(), encoding="utf-8")
    record = build()
    steps = record["fields"]["schema"]["steps"]
    print("wrote", V4.name)
    print("  steps:", len(steps), " rules:", len(record["fields"]["schema"]["rules"]))
    for step in steps:
        for question in step["questions"]:
            print(f"    {question['id']}: {len(question.get('options', []))} options")


if __name__ == "__main__":
    write()
