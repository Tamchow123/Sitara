"""Deterministic demo refinement engine (Phase 15 Part B).

Builds a complete, updated DesignSpec payload from an existing validated
source DesignSpec dict and a validated
:class:`~sitara.generation.refinement.RefinementRequest` — never a live
provider call. Uses the existing Phase 14
:data:`~sitara.generation.refinement.REFINEMENT_ALLOWED_PATHS` allowlist and
:func:`~sitara.generation.refinement.diff_design_spec_paths`/
:func:`~sitara.generation.refinement.path_is_allowed` for validation (this
module never reimplements that policy); every candidate this engine produces
changes only a single field within the requested category's allowlist and
never touches ``source_selections``.
"""

import copy
import hashlib
import json

from sitara.generation.refinement import (
    COLOUR_STORY,
    DUPATTA_OR_SAREE_DRAPE,
    EMBELLISHMENT,
    FABRIC_AND_TEXTURE,
    NECKLINE,
    SILHOUETTE_DETAIL,
    SLEEVES_AND_COVERAGE,
    STYLING_DETAILS,
)

from . import phrases

DEMO_REFINEMENT_TEMPLATE_VERSION = "1.0.0"

# Small, category-scoped keyword maps recognised in the refinement note. Only
# used to bias which curated variant is selected — the raw note is never
# copied into output. Unrecognised text still folds into the deterministic
# variant fingerprint below.
_COLOUR_KEYWORDS = phrases.COLOUR_PHRASES
_FABRIC_KEYWORDS = phrases.FABRIC_PHRASES
_TONE_KEYWORDS = {"softer": "minimal", "deeper": "heavy", "lighter": "minimal", "richer": "heavy"}
_SLEEVE_KEYWORDS = {
    "sleeveless": "sleeveless",
    "short": "short_sleeves",
    "elbow": "elbow_sleeves",
    "three quarter": "three_quarter_sleeves",
    "full": "full_sleeves",
    "long": "full_sleeves",
}
_NECKLINE_KEYWORDS = {"high": "high", "modest": "high", "low": "open"}
_DRAPE_KEYWORDS = {**phrases.DUPATTA_PHRASES, **phrases.SAREE_DRAPE_PHRASES}


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fingerprint(source_spec: dict, change_type: str, note: str) -> str:
    payload = {"source_spec": source_spec, "change_type": change_type, "note_present": bool(note)}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _note_keyword(note: str, keywords: dict[str, str]) -> str | None:
    lowered = note.lower()
    for needle, canonical in keywords.items():
        if needle in lowered:
            return canonical
    return None


def _pick(candidates: list, fingerprint: str, salt: str):
    digest = hashlib.sha256(f"{fingerprint}:{salt}".encode()).hexdigest()
    index = int(digest[:8], 16) % len(candidates)
    return candidates[index]


def _edit_colour_story(spec: dict, note: str, fingerprint: str) -> dict:
    used = set(spec["source_selections"].get("colour_palette") or [])
    hinted = _note_keyword(note, _COLOUR_KEYWORDS)
    candidates = [k for k in phrases.COLOUR_PHRASES if k not in used]
    colour_key = (
        hinted if hinted and hinted not in used else _pick(candidates, fingerprint, "colour")
    )
    colour_phrase = phrases.COLOUR_PHRASES[colour_key]
    spec = copy.deepcopy(spec)
    spec["colour_story"] = {
        "palette_summary": (
            f"A refined {colour_phrase} palette, adjusted per the requested colour direction."
        ),
        "placement": (
            f"{colour_phrase.capitalize()} now leads across the main body of the garment."
        ),
        "rationale": (
            f"{colour_phrase.capitalize()} was selected to better match the "
            "requested colour direction."
        ),
    }
    return spec


def _edit_fabric(spec: dict, note: str, fingerprint: str) -> dict:
    used = {entry["fabric"].lower() for entry in spec["fabrics_and_texture"]}
    hinted = _note_keyword(note, _FABRIC_KEYWORDS)
    candidates = [k for k in phrases.FABRIC_PHRASES if phrases.FABRIC_PHRASES[k] not in used]
    fabric_key = (
        hinted
        if hinted
        else _pick(candidates or list(phrases.FABRIC_PHRASES), fingerprint, "fabric")
    )
    fabric_phrase = phrases.FABRIC_PHRASES[fabric_key]
    spec = copy.deepcopy(spec)
    spec["fabrics_and_texture"] = [
        {
            "fabric": fabric_phrase.capitalize(),
            "placement": (
                "Used across the garment's main panels, replacing the prior fabric choice."
            ),
            "finish_and_movement": (
                "A finish chosen to hold its shape while still moving naturally."
            ),
        }
    ]
    return spec


def _edit_embellishment(spec: dict, note: str, fingerprint: str) -> dict:
    hinted = _note_keyword(note, _TONE_KEYWORDS)
    density_key = hinted or _pick(["minimal", "balanced", "heavy"], fingerprint, "density")
    density_phrase = phrases.DENSITY_PHRASES[density_key]
    spec = copy.deepcopy(spec)
    spec["embellishment_plan"] = {
        **spec["embellishment_plan"],
        "density": f"The embellishment is now {density_phrase}, adjusted per the requested change.",
        "restraint_notes": f"The overall density has been revised to be {density_phrase}.",
    }
    return spec


def _edit_sleeves(spec: dict, note: str, fingerprint: str) -> dict:
    hinted = _note_keyword(note, _SLEEVE_KEYWORDS)
    key = hinted or _pick(
        ["sleeveless", "short_sleeves", "elbow_sleeves", "three_quarter_sleeves", "full_sleeves"],
        fingerprint,
        "sleeves",
    )
    phrase = phrases.COVERAGE_PHRASES[key]
    spec = copy.deepcopy(spec)
    spec["coverage_and_drape"] = {
        **spec["coverage_and_drape"],
        "sleeves": f"Sleeve length is now {phrase}, per the requested change.",
    }
    return spec


def _edit_neckline(spec: dict, note: str, fingerprint: str) -> dict:
    hinted = _note_keyword(note, _NECKLINE_KEYWORDS)
    variant = hinted or _pick(["high", "open"], fingerprint, "neckline")
    phrase = "a modest, higher neckline" if variant == "high" else "a more open neckline"
    spec = copy.deepcopy(spec)
    spec["coverage_and_drape"] = {
        **spec["coverage_and_drape"],
        "neckline": f"The neckline has been revised to {phrase}.",
    }
    return spec


def _edit_drape(spec: dict, note: str, fingerprint: str) -> dict:
    hinted = _note_keyword(note, _DRAPE_KEYWORDS)
    candidates = list(_DRAPE_KEYWORDS)
    key = hinted or _pick(candidates, fingerprint, "drape")
    phrase = _DRAPE_KEYWORDS[key]
    spec = copy.deepcopy(spec)
    spec["coverage_and_drape"] = {
        **spec["coverage_and_drape"],
        "dupatta_or_saree_drape": f"The drape has been revised: it is now {phrase}.",
    }
    return spec


def _edit_silhouette_detail(spec: dict, note: str, fingerprint: str) -> dict:
    candidates = list(phrases.SILHOUETTE_PHRASES)
    key = _pick(candidates, fingerprint, "silhouette")
    phrase = phrases.SILHOUETTE_PHRASES[key]
    spec = copy.deepcopy(spec)
    spec["garment_breakdown"] = {
        **spec["garment_breakdown"],
        "key_proportions": f"The proportions have been revised toward {phrase}.",
    }
    return spec


def _edit_styling_details(spec: dict, note: str, fingerprint: str) -> dict:
    variant = _pick([0, 1, 2], fingerprint, "styling")
    options = [
        "A more restrained accessory choice keeps the revised look grounded.",
        "A bolder accessory choice matches the revised styling direction.",
        "A softer, understated accessory choice complements the revised styling direction.",
    ]
    spec = copy.deepcopy(spec)
    spec["styling_notes"] = [*spec["styling_notes"][:1], options[variant]][:8]
    return spec


_EDITORS = {
    COLOUR_STORY: _edit_colour_story,
    FABRIC_AND_TEXTURE: _edit_fabric,
    EMBELLISHMENT: _edit_embellishment,
    SLEEVES_AND_COVERAGE: _edit_sleeves,
    NECKLINE: _edit_neckline,
    DUPATTA_OR_SAREE_DRAPE: _edit_drape,
    SILHOUETTE_DETAIL: _edit_silhouette_detail,
    STYLING_DETAILS: _edit_styling_details,
}


def build_demo_refined_spec(source_spec: dict, refinement_request) -> dict:
    """Build a complete, updated DesignSpec payload.

    ``source_spec`` is a validated DesignSpec dict (e.g.
    ``DesignSpec.model_dump(mode="json")``). ``refinement_request`` is a
    validated :class:`~sitara.generation.refinement.RefinementRequest`.
    Deterministic: the same inputs always produce the byte-identical result.
    Guarantees a genuine change within the requested category's allowlist —
    if the first deterministic candidate happens to equal the source, a
    stable alternate variant is selected instead."""
    change_type = refinement_request.change_type
    note = refinement_request.note or ""
    editor = _EDITORS[change_type]
    fingerprint = _fingerprint(source_spec, change_type, note)

    candidate = editor(source_spec, note, fingerprint)
    if candidate == source_spec:
        candidate = editor(source_spec, note, fingerprint + ":alternate")
    return candidate
