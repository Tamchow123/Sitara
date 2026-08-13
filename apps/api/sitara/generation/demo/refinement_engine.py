"""Deterministic demo refinement engine (Phase 15 Part B; Phase 23 §8).

Builds a complete, updated DesignSpec payload from an existing validated
source DesignSpec dict and a validated
:class:`~sitara.generation.refinement.RefinementRequest` — never a live
provider call.

Since ADR 0028 a refinement changes the CANONICAL SELECTION its category is
named after, because that is what the deterministic image prompt is built
from; narrative alone provably could not move the prompt. The demo path has to
do the same thing or demo mode would keep demonstrating the defect this phase
fixes — and, worse, would keep returning the same fixture image, since
:func:`~sitara.generation.demo.selector.select_demo_asset` scores against
``source_selections``.

The engine never invents a canonical value. It is HANDED the legal ones by
:func:`~sitara.generation.refinement_selections.answerable_selection_alternatives`,
which computes them from the design's own pinned questionnaire with the same
validator that will judge the result. The phrase vocabularies below are
deliberate supersets of any single questionnaire — ``COLOUR_PHRASES`` alone has
57 entries — so picking from them directly would produce values the design's
questionnaire never offered. They are used only to describe a value already
chosen.

Validation is unchanged and still lives outside this module: every candidate
goes through the same
:data:`~sitara.generation.refinement.REFINEMENT_ALLOWED_PATHS` allowlist,
:func:`~sitara.generation.refinement.diff_design_spec_paths` and questionnaire
re-check as a live provider's output. This module never reimplements that
policy.
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
)
from sitara.generation.selection_semantics import ordered_colour_values

from . import phrases

DEMO_REFINEMENT_TEMPLATE_VERSION = "2.0.0"

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

# How to SAY a canonical value, per canonical field. One entry per field any
# category owns on any DesignSpec version (see
# :mod:`sitara.generation.selection_semantics`), so a chosen value can always be
# described in the same words the prompt builder and the demo brief already use.
# Version 1/2's single ``coverage_preferences`` and version 3's per-area
# ``sleeves`` draw on different vocabularies on purpose — they are different
# questions.
_FIELD_PHRASES: dict[str, dict[str, str]] = {
    "colour_palette": phrases.COLOUR_PHRASES,
    "fabric_colour": phrases.COLOUR_PHRASES,
    "embroidery_colour": phrases.COLOUR_PHRASES,
    "dupatta_colour": phrases.COLOUR_PHRASES,
    "fabrics": phrases.FABRIC_PHRASES,
    "embellishment_styles": phrases.EMBELLISHMENT_PHRASES,
    "embellishment_density": phrases.DENSITY_PHRASES,
    "coverage_preferences": phrases.COVERAGE_PHRASES,
    "sleeves": phrases.SLEEVE_PHRASES,
    "back_coverage": phrases.BACK_COVERAGE_PHRASES,
    "midriff": phrases.MIDRIFF_PHRASES,
    "neckline_style": phrases.NECKLINE_PHRASES,
    "dupatta_style": phrases.DUPATTA_PHRASES,
    "saree_drape": phrases.SAREE_DRAPE_PHRASES,
    "head_covering": phrases.HEAD_COVERING_PHRASES,
    "silhouette": phrases.SILHOUETTE_PHRASES,
}


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


def _machine_value(value) -> str | None:
    """The single machine value a canonical answer carries, if it carries one.

    A multi-select alternative is always a one-item list (that is the shape
    :func:`answerable_selection_alternatives` returns), and a single-choice one
    is a bare string. Anything else has no one value to describe."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        return value[0]
    return None


def _note_machine_value(note: str, field: str) -> str | None:
    """The canonical machine value ``note`` names for ``field``, if it names one.

    Deliberately not :func:`_note_keyword`. That takes a KEYWORD map — needle to
    machine value, like ``_TONE_KEYWORDS`` — and returns the mapped value.
    ``_FIELD_PHRASES`` is the opposite shape, a vocabulary of machine value to
    English phrase, so putting it through ``_note_keyword`` hands back a PHRASE,
    which can then never equal a machine value except by coincidence.

    It does coincide for most colours (``COLOUR_PHRASES["emerald"] ==
    "emerald"``), which is exactly why the mistake looked like it worked: a note
    naming a fabric, a density, a drape or a silhouette silently lost its hint
    and fell through to the deterministic pick, and the one test covering this
    used a colour."""
    lowered = note.lower()
    for machine in _FIELD_PHRASES.get(field, {}):
        if machine in lowered:
            return machine
    return None


def _describe(field: str, value) -> str | None:
    """The chosen value in plain words, or ``None`` when it cannot be named."""
    machine = _machine_value(value)
    if machine is None:
        return None
    return _FIELD_PHRASES.get(field, {}).get(machine)


def _describable_alternatives(alternatives: dict[str, tuple]) -> dict[str, list]:
    """``alternatives`` less every value this module cannot put into words.

    Two vocabularies meet here and they are maintained separately: the legal
    values come from the design's own questionnaire, the words from
    :mod:`sitara.generation.demo.phrases`. A value the phrase tables do not
    cover must therefore not be chosen at all — writing it into
    ``source_selections`` while the narrative described something else would
    reintroduce, in demo mode, the very selection-versus-brief mismatch this
    phase exists to remove. Dropping it instead degrades to a narrative-only
    refinement, which is honest.

    A structural test asserts the tables cover every declared option of every
    canonical field in every committed questionnaire version, so this filter is
    a backstop rather than the working answer."""
    describable = {}
    for field, values in alternatives.items():
        usable = [value for value in values if _describe(field, value) is not None]
        if usable:
            describable[field] = usable
    return describable


def _canonical_choice(alternatives: dict[str, tuple], note: str, fingerprint: str):
    """``(field, value)`` chosen deterministically from the LEGAL alternatives,
    or ``(None, None)`` when the category has none this engine can also describe.

    A note keyword still biases the choice exactly as it did before, but now it
    can only select among values the design's own questionnaire offers: an
    unrecognised or illegal hint falls back to the deterministic pick rather
    than forcing a value that would be rejected."""
    describable = _describable_alternatives(alternatives)
    if not describable:
        return None, None
    field = _pick(sorted(describable), fingerprint, "selection-field")
    values = describable[field]
    hinted = _note_machine_value(note, field)
    for value in values:
        if hinted is not None and _machine_value(value) == hinted:
            return field, copy.deepcopy(value)
    return field, copy.deepcopy(_pick(values, fingerprint, f"selection-value:{field}"))


def _edit_colour_story(spec: dict, note: str, fingerprint: str, phrase: str | None) -> dict:
    # Version-independent: v1/v2 keep one palette, v3 names a colour per garment
    # role. Either way the refinement must not "change" a colour to one already
    # chosen.
    if phrase is None:
        used = set(ordered_colour_values(spec["source_selections"]))
        hinted = _note_keyword(note, _COLOUR_KEYWORDS)
        candidates = [k for k in phrases.COLOUR_PHRASES if k not in used]
        colour_key = (
            hinted if hinted and hinted not in used else _pick(candidates, fingerprint, "colour")
        )
        phrase = phrases.COLOUR_PHRASES[colour_key]
    spec = copy.deepcopy(spec)
    spec["colour_story"] = {
        "palette_summary": (
            f"A refined {phrase} palette, adjusted per the requested colour direction."
        ),
        "placement": (f"{phrase.capitalize()} now leads across the main body of the garment."),
        "rationale": (
            f"{phrase.capitalize()} was selected to better match the requested colour direction."
        ),
    }
    return spec


def _edit_fabric(spec: dict, note: str, fingerprint: str, phrase: str | None) -> dict:
    if phrase is None:
        used = {entry["fabric"].lower() for entry in spec["fabrics_and_texture"]}
        hinted = _note_keyword(note, _FABRIC_KEYWORDS)
        candidates = [k for k in phrases.FABRIC_PHRASES if phrases.FABRIC_PHRASES[k] not in used]
        fabric_key = (
            hinted
            if hinted
            else _pick(candidates or list(phrases.FABRIC_PHRASES), fingerprint, "fabric")
        )
        phrase = phrases.FABRIC_PHRASES[fabric_key]
    spec = copy.deepcopy(spec)
    spec["fabrics_and_texture"] = [
        {
            "fabric": phrase.capitalize(),
            "placement": (
                "Used across the garment's main panels, replacing the prior fabric choice."
            ),
            "finish_and_movement": (
                "A finish chosen to hold its shape while still moving naturally."
            ),
        }
    ]
    return spec


def _edit_embellishment(spec: dict, note: str, fingerprint: str, phrase: str | None) -> dict:
    if phrase is None:
        hinted = _note_keyword(note, _TONE_KEYWORDS)
        density_key = hinted or _pick(["minimal", "balanced", "heavy"], fingerprint, "density")
        phrase = phrases.DENSITY_PHRASES[density_key]
    spec = copy.deepcopy(spec)
    spec["embellishment_plan"] = {
        **spec["embellishment_plan"],
        "density": f"The embellishment is now {phrase}, adjusted per the requested change.",
        "restraint_notes": f"The overall density has been revised to be {phrase}.",
    }
    return spec


def _edit_sleeves(spec: dict, note: str, fingerprint: str, phrase: str | None) -> dict:
    if phrase is None:
        hinted = _note_keyword(note, _SLEEVE_KEYWORDS)
        key = hinted or _pick(
            [
                "sleeveless",
                "short_sleeves",
                "elbow_sleeves",
                "three_quarter_sleeves",
                "full_sleeves",
            ],
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


def _edit_neckline(spec: dict, note: str, fingerprint: str, phrase: str | None) -> dict:
    if phrase is None:
        hinted = _note_keyword(note, _NECKLINE_KEYWORDS)
        variant = hinted or _pick(["high", "open"], fingerprint, "neckline")
        phrase = "a modest, higher neckline" if variant == "high" else "a more open neckline"
    spec = copy.deepcopy(spec)
    spec["coverage_and_drape"] = {
        **spec["coverage_and_drape"],
        "neckline": f"The neckline has been revised to {phrase}.",
    }
    return spec


def _edit_drape(spec: dict, note: str, fingerprint: str, phrase: str | None) -> dict:
    if phrase is None:
        hinted = _note_keyword(note, _DRAPE_KEYWORDS)
        key = hinted or _pick(list(_DRAPE_KEYWORDS), fingerprint, "drape")
        phrase = _DRAPE_KEYWORDS[key]
    spec = copy.deepcopy(spec)
    spec["coverage_and_drape"] = {
        **spec["coverage_and_drape"],
        "dupatta_or_saree_drape": f"The drape has been revised: it is now {phrase}.",
    }
    return spec


def _edit_silhouette_detail(spec: dict, note: str, fingerprint: str, phrase: str | None) -> dict:
    if phrase is None:
        phrase = phrases.SILHOUETTE_PHRASES[
            _pick(list(phrases.SILHOUETTE_PHRASES), fingerprint, "silhouette")
        ]
    spec = copy.deepcopy(spec)
    spec["garment_breakdown"] = {
        **spec["garment_breakdown"],
        "key_proportions": f"The proportions have been revised toward {phrase}.",
    }
    return spec


# One editor per REQUESTABLE category. ``styling_details`` had one until ADR
# 0028 retired the category; it is gone rather than kept defensively, because
# the client boundary refuses a request naming it and a historical row is only
# ever READ (a refined version already exists for it — it is never re-run
# through this engine).
_EDITORS = {
    COLOUR_STORY: _edit_colour_story,
    FABRIC_AND_TEXTURE: _edit_fabric,
    EMBELLISHMENT: _edit_embellishment,
    SLEEVES_AND_COVERAGE: _edit_sleeves,
    NECKLINE: _edit_neckline,
    DUPATTA_OR_SAREE_DRAPE: _edit_drape,
    SILHOUETTE_DETAIL: _edit_silhouette_detail,
}


def build_demo_refined_spec(
    source_spec: dict, refinement_request, *, selection_alternatives=None
) -> dict:
    """Build a complete, updated DesignSpec payload.

    ``source_spec`` is a validated DesignSpec dict (e.g.
    ``DesignSpec.model_dump(mode="json")``). ``refinement_request`` is a
    validated :class:`~sitara.generation.refinement.RefinementRequest`.
    ``selection_alternatives`` is the mapping of canonical field to LEGAL
    replacement values computed from the design's own pinned questionnaire; it
    is a keyword argument with a default so the pure-fixture tests that predate
    ADR 0028 still exercise the narrative path, but every production caller
    supplies it.

    Deterministic: the same inputs always produce the byte-identical result.
    When a canonical alternative exists the refinement changes it — which is
    what makes the image prompt, and therefore the selected demo asset, able to
    move at all — and the narrative is written from that same chosen value, so
    the brief and the selection can never describe different garments. With no
    legal alternative the narrative still changes on its own, which is honest
    about what the questionnaire allows rather than inventing an option.

    Guarantees a genuine change within the requested category's allowlist: if
    the first deterministic candidate happens to equal the source, a stable
    alternate variant is selected instead."""
    change_type = refinement_request.change_type
    note = refinement_request.note or ""
    editor = _EDITORS[change_type]
    fingerprint = _fingerprint(source_spec, change_type, note)

    def _build(salt: str) -> dict:
        field, value = _canonical_choice(selection_alternatives or {}, note, fingerprint + salt)
        spec = source_spec
        phrase = None
        if field is not None:
            spec = copy.deepcopy(spec)
            spec["source_selections"][field] = value
            # Non-None by construction: `_canonical_choice` only ever returns a
            # value this module can describe, so a chosen selection and the
            # sentence about it can never come from different vocabularies.
            phrase = _describe(field, value)
        return editor(spec, note, fingerprint + salt, phrase)

    candidate = _build("")
    if candidate == source_spec:
        candidate = _build(":alternate")
    return candidate
