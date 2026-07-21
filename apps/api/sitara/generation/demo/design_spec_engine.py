"""Deterministic demo DesignSpec engine (Phase 15 Part B).

Builds a complete, valid :class:`~sitara.generation.design_spec.DesignSpec`
payload directly from an already-validated
:class:`~sitara.generation.context.GenerationContext` — never from a live
provider, never by parsing a rendered prompt, never using process
randomness, the current time, a Design UUID, user/session identity, storage
data or provider keys. The same context always produces the byte-identical
DesignSpec.

Free text is never copied into the output: an optional small allowlisted
keyword extractor (:data:`~sitara.generation.demo.phrases.ALLOWED_STYLE_KEYWORDS`)
may recognise a safe style-adjective hint and use it to bias which curated
phrase variant is selected; unrecognised prose still folds into the stable
context fingerprint used for variant selection but is never reproduced.
"""

import hashlib
import json

from sitara.generation.design_spec import NO_REGIONAL_DIRECTION

from . import phrases

DEMO_SPEC_TEMPLATE_VERSION = "1.0.0"


class DemoGarmentUnsupported(Exception):
    """``source_selections.garment_type`` has no entry in the demo engine's
    controlled phrase-map vocabulary. A safe, categorised failure rather than
    a raw ``KeyError`` — this should only occur if a future questionnaire
    revision introduces a garment value before ``phrases.GARMENT_PHRASES`` is
    updated to match; every other phrase lookup in this module already
    degrades gracefully to a generic default."""


_REQUIRED_CAVEATS = [
    "This is a concept visualisation only and is not a sewing pattern.",
    "It does not guarantee that the garment can be constructed exactly as shown.",
]

_DEFAULT_FABRIC_BY_GARMENT = {
    "lehenga": ["silk", "net"],
    "saree": ["silk"],
    "gharara": ["cotton_silk"],
    "sharara": ["georgette"],
    "anarkali": ["georgette", "net"],
    "shalwar_kameez": ["cotton_silk"],
}

_MOOD_ADJECTIVES = {
    "minimal": "restrained",
    "bold": "striking",
    "regal": "regal",
    "modern": "contemporary",
    "vintage": "heritage-inspired",
    "pastel": "softly toned",
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _context_fingerprint(
    source_selections: dict, untrusted_texts: list[dict], inspiration_cues: list[dict]
) -> str:
    """A stable digest of everything demo generation may deterministically
    respond to. Untrusted free text is folded in by content (never used
    verbatim) so a different note can select a different curated variant
    without the raw text ever appearing in the output. Curated inspiration
    cues (garment type / visual description / cultural context only — never
    an asset title or attribution) contribute the same way."""
    payload = {
        "source_selections": source_selections,
        "untrusted_text_values": sorted(t.get("value", "") for t in untrusted_texts),
        "inspiration_cues": [
            {
                "garment_type": cue.get("garment_type"),
                "visual_description": cue.get("visual_description"),
                "cultural_context": cue.get("cultural_context"),
            }
            for cue in sorted(inspiration_cues, key=lambda c: c.get("position", 0))
        ],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _style_keyword_hint(untrusted_texts: list[dict]) -> str | None:
    for entry in untrusted_texts:
        text = (entry.get("value") or "").lower()
        for keyword, canonical in phrases.ALLOWED_STYLE_KEYWORDS.items():
            if keyword in text:
                return canonical
    return None


def _variant_index(fingerprint: str, modulus: int, salt: str) -> int:
    digest = hashlib.sha256(f"{fingerprint}:{salt}".encode()).hexdigest()
    return int(digest[:8], 16) % modulus


def _phrase_list(values: list[str], mapping: dict[str, str]) -> list[str]:
    return [mapping[v] for v in values if v in mapping]


def build_demo_design_spec(context) -> dict:
    """Build a complete, valid DesignSpec payload from ``context``.

    ``context`` is a :class:`~sitara.generation.context.GenerationContext`.
    Deterministic: the same context always produces the byte-identical
    result."""
    selections = context.source_selections
    garment_type = selections["garment_type"]
    ceremony = selections["ceremony"]
    silhouette = selections["silhouette"]
    regional_style = selections.get("regional_style")
    colour_palette = selections.get("colour_palette") or []
    fabrics = selections.get("fabrics") or []
    embellishment_styles = selections.get("embellishment_styles") or []
    embellishment_density = selections.get("embellishment_density")
    coverage_preferences = selections.get("coverage_preferences") or []
    dupatta_style = selections.get("dupatta_style")
    saree_drape = selections.get("saree_drape")

    garment = phrases.GARMENT_PHRASES.get(garment_type)
    if garment is None:
        raise DemoGarmentUnsupported(garment_type)
    garment_noun = garment["noun"]
    ceremony_phrase = phrases.CEREMONY_PHRASES.get(ceremony, "the occasion")
    silhouette_phrase = phrases.SILHOUETTE_PHRASES.get(silhouette, "a considered silhouette")

    fingerprint = _context_fingerprint(
        selections, context.untrusted_texts, context.inspiration_cues
    )
    style_hint = _style_keyword_hint(context.untrusted_texts)

    colour_phrases = _phrase_list(colour_palette, phrases.COLOUR_PHRASES) or [
        "a considered palette"
    ]
    lead_colour = colour_phrases[0]
    accent_colours = colour_phrases[1:]

    fabric_values = fabrics or _DEFAULT_FABRIC_BY_GARMENT.get(garment_type, ["silk"])
    fabric_phrases = _phrase_list(fabric_values, phrases.FABRIC_PHRASES) or ["silk"]

    embellishment_phrases = _phrase_list(embellishment_styles, phrases.EMBELLISHMENT_PHRASES)
    is_unembellished = embellishment_styles == ["none"] or not embellishment_phrases
    density_phrase = phrases.DENSITY_PHRASES.get(
        embellishment_density, "kept balanced, present without overwhelming"
    )

    coverage_phrases = _phrase_list(coverage_preferences, phrases.COVERAGE_PHRASES)

    is_saree = garment_type == "saree"
    if is_saree:
        drape_phrase = phrases.SAREE_DRAPE_PHRASES.get(
            saree_drape, "a drape styled to flatter the wearer"
        )
    elif dupatta_style:
        drape_phrase = phrases.DUPATTA_PHRASES.get(
            dupatta_style, "styled to the wearer's preference"
        )
    else:
        drape_phrase = "styled to the wearer's preference"

    has_regional_direction = bool(regional_style) and regional_style != NO_REGIONAL_DIRECTION
    regional_phrase = (
        phrases.REGIONAL_PHRASES.get(regional_style) if has_regional_direction else None
    )

    mood = style_hint or ("regal" if _variant_index(fingerprint, 2, "mood") == 0 else "modern")
    mood_adjective = _MOOD_ADJECTIVES.get(mood, "considered")

    title = f"{lead_colour.capitalize()} {garment_noun} for {ceremony}".replace("_", " ")
    title = title[:120]

    embellishment_summary = (
        "an unembellished, clean finish"
        if is_unembellished
        else f"{embellishment_phrases[0]}, {density_phrase}"
    )
    concept_summary = (
        f"A {mood_adjective} {ceremony_phrase} concept built around a {lead_colour} "
        f"{garment_noun} with {silhouette_phrase}. The garment is {garment['overall_form']}, "
        f"brought together with {fabric_phrases[0]} as the lead fabric and "
        f"{embellishment_summary}. The dupatta or drape is {drape_phrase}, and the overall "
        f"mood stays {mood_adjective} and true to the selected ceremony."
    )
    if len(concept_summary) > 700:
        concept_summary = concept_summary[:697] + "..."
    if len(concept_summary) < 80:
        concept_summary = concept_summary + " " * (80 - len(concept_summary))

    garment_components = [c.strip() for c in garment["components"].split("|")]

    colour_placement = (
        f"{lead_colour.capitalize()} leads across the main body of the {garment_noun}"
        + (
            f", with {', '.join(accent_colours)} carried through as accents."
            if accent_colours
            else "."
        )
    )
    colour_rationale = (
        f"{lead_colour.capitalize()} was chosen to suit {ceremony_phrase} while keeping the look "
        f"{mood_adjective} rather than overstated."
    )

    fabrics_and_texture = [
        {
            "fabric": fabric.capitalize(),
            "placement": f"Used across the {garment_noun}'s main panels.",
            "finish_and_movement": (
                "A finish chosen to hold its shape while still moving naturally."
            ),
        }
        for fabric in fabric_phrases[:8]
    ]

    if is_unembellished:
        embellishment_plan = {
            "techniques": ["A clean, unembellished finish"],
            "density": "Left deliberately bare, with no added embellishment.",
            "placement": ["Throughout, kept intentionally plain"],
            "motifs": ["No motifs — the silhouette and fabric carry the look"],
            "restraint_notes": (
                "Restraint is the point: texture and cut do the work instead of embroidery."
            ),
        }
    else:
        embellishment_plan = {
            "techniques": [p.capitalize() for p in embellishment_phrases[:8]],
            "density": f"The embellishment is {density_phrase}.",
            "placement": ["Bodice or yoke", "Hem or border", "Dupatta edge"],
            "motifs": [
                "Motifs drawn from traditional bridal vocabulary, kept legible rather than crowded"
            ],
            "restraint_notes": (
                f"Open ground is left between motifs so the {mood_adjective} mood is preserved."
            ),
        }

    sleeves_coverage = next((c for c in coverage_phrases if "sleeve" in c), None)
    sleeves_line = (
        f"Sleeve length is {sleeves_coverage}."
        if sleeves_coverage
        else "Sleeve length is left to styling preference."
    )
    neckline_phrase = (
        phrases.COVERAGE_PHRASES["high_neckline"]
        if "high_neckline" in coverage_preferences
        else "a neckline that suits the silhouette"
    )
    back_midriff_phrase = (
        ", ".join(c for c in coverage_phrases if "back" in c or "midriff" in c)
        or "styled to the wearer's comfort"
    )
    head_covering_phrase = (
        phrases.COVERAGE_PHRASES["head_drape_preferred"]
        if "head_drape_preferred" in coverage_preferences
        else "left uncovered unless the drape naturally covers it"
    )

    coverage_and_drape = {
        "sleeves": sleeves_line,
        "neckline": f"The neckline is {neckline_phrase}.",
        "back_and_midriff": f"The back and midriff are {back_midriff_phrase}.",
        "head_covering": f"The head is {head_covering_phrase}.",
        "dupatta_or_saree_drape": (
            f"The {'saree drape' if is_saree else 'dupatta'} is {drape_phrase}."
        ),
    }

    if has_regional_direction:
        interpretation_notes = [
            f"{regional_phrase.capitalize()} is treated as one broad direction, not a fixed rule."
        ]
        safeguards = [
            "No single family, sect or regional tradition is presented as universal or definitive."
        ]
    else:
        interpretation_notes = [
            "No specific regional direction was requested; styling stays broadly bridal."
        ]
        safeguards = [
            "No particular community or region's tradition is presented as the only "
            "correct approach."
        ]

    styling_notes = [
        f"Jewellery in tones that echo the {lead_colour} palette suits this concept.",
        f"A {mood_adjective} hair and makeup approach keeps the overall look cohesive.",
    ]
    if context.inspiration_cues:
        styling_notes.append(
            "Approved inspiration references were considered when shaping the "
            "overall styling direction."
        )

    image_alt_text = (
        f"A {mood_adjective} {lead_colour} {garment_noun} concept with {silhouette_phrase}, "
        f"styled for {ceremony_phrase}."
    )
    if len(image_alt_text) < 40:
        image_alt_text = image_alt_text + " Generated as a deterministic demo concept."
    image_alt_text = image_alt_text[:300]

    return {
        "schema_version": 1,
        "source_selections": dict(selections),
        "title": title,
        "concept_summary": concept_summary,
        "garment_breakdown": {
            "overall_form": garment["overall_form"][:400],
            "garment_components": garment_components,
            "silhouette": silhouette_phrase.capitalize()[:400],
            "drape_or_layering": f"The {'drape' if is_saree else 'dupatta'} is {drape_phrase}."[
                :400
            ],
            "key_proportions": garment["key_proportions"][:400],
        },
        "colour_story": {
            "palette_summary": f"A {lead_colour} palette"
            + (f" lifted by {', '.join(accent_colours)}." if accent_colours else "."),
            "placement": colour_placement,
            "rationale": colour_rationale,
        },
        "fabrics_and_texture": fabrics_and_texture,
        "embellishment_plan": embellishment_plan,
        "coverage_and_drape": coverage_and_drape,
        "cultural_context": {
            "regional_direction": (
                f"{regional_phrase.capitalize()} guides the overall styling direction."
                if has_regional_direction
                else None
            ),
            "interpretation_notes": interpretation_notes,
            "safeguards": safeguards,
        },
        "styling_notes": styling_notes,
        "construction_caveats": list(_REQUIRED_CAVEATS),
        "image_alt_text": image_alt_text,
    }
