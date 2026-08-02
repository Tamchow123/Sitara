"""Deterministic image-prompt builder (Phase 9).

Turns a validated :class:`DesignSpec` into ONE natural-language image prompt
string for the environment-configured FLUX model. Following the Phase 2
evaluation (ADR 0001, ADR 0010) the current default model exposes neither a
genuine negative-prompt input nor documented JSON prompting, so this builder
produces a single positive editorial prompt — no separate negative prompt, no
JSON, no hard-coded model identifier and no provider call.

The function is PURE and DETERMINISTIC: no database access, no environment
reads, no randomness, no timestamps, no network, no provider SDK imports.
Identical validated input always yields identical UTF-8 output, which the
committed golden snapshots guard.

Every DesignSpec narrative string is generated text and enters the prompt only
through named, bounded slots (see :func:`_slot`); the generated-content safety
scan runs before interpolation and again on the finished prompt. The persisted
DesignSpec's ``construction_caveats`` and ``image_alt_text`` are deliberately
NOT rendered, and no provider metadata, token usage, database identifier,
questionnaire label/schema, inspiration metadata or raw questionnaire free text
can appear (the DesignSpec contract carries none of those into this builder).

## Short, positive, canonical-first (8.0.0)

A live 7.0.0 generation ignored four validated selections at once. The cause was
not missing information — every requirement was in the prompt — but length and
phrasing. 7.0.0 prompts ran 2,444-3,993 characters, well past the ~512-token
window the default model's text encoder attends over, so colour, fabric,
embellishment and the closing coverage restatement fell outside it; and coverage
was stated NEGATIVELY in six places, which diffusion text conditioning cannot
represent (``not an open neckline`` conditions on *open neckline*).

8.0.0 therefore:

* budgets toward :data:`IMAGE_PROMPT_TARGET_CHARS` rather than 6,000, so the
  whole prompt sits inside the encoder window;
* states every requirement POSITIVELY — no clause here contains "not", "no",
  "never" or "without";
* names each garment's CONSTRUCTION (one-piece vs two-piece) up front, folding
  in the former garment-integrity cues;
* renders CANONICAL SELECTIONS as the skeleton and a small fixed set of bounded
  narrative slots as supplement, instead of letting model-authored prose
  dominate;
* states coverage ONCE, early, and drops the closing reinforcement (which at
  7.0.0 lengths sat outside the encoder window and conditioned nothing).

Ordering: the fixed catalogue-composition directive leads, then the garment's
identity and construction, then the coverage requirements, then colour, fabric,
embellishment, drape and broad regional influence, then a short photographic
finishing directive. Advisory styling notes, non-visual colour rationale and the
cultural interpretation/safeguard notes are NOT rendered — they pull the
provider toward portraiture or carry nothing an image model can draw. See
ADR 0010.

## Bounded rendering

The DesignSpec schema permits several eight-item narrative lists and 64-character
machine values in every canonical list, so per-slot caps alone cannot guarantee a
global bound. Rendering therefore reserves space for the MANDATORY content first
— the leading composition directive, garment/ceremony/construction, the canonical
silhouette, every coverage requirement, the canonical colour/fabric/embellishment
selections, the canonical dupatta/saree drape and the fixed finishing wording —
and lets generated narrative consume only the remaining budget, shared across
sections in fixed order. When a section's narrative exceeds its budget,
lower-priority generated details are deterministically shortened at a word
boundary or omitted; mandatory content is never removed, and the fully assembled
prompt is never sliced.

Two bounds, deliberately. A schema-valid spec carrying 64-character machine
values in every list has mandatory content alone exceeding the target, so a
single bound would be a promise the schema can break:
:data:`IMAGE_PROMPT_TARGET_CHARS` is what the narrative budget aims at (realistic
specs land there), and :data:`IMAGE_PROMPT_MAX_CHARS` is the hard bound no
schema-valid spec can exceed.
"""

import re
import unicodedata
from dataclasses import dataclass

from .design_spec import (
    COLOUR_MATCH_FABRIC,
    NO_REGIONAL_DIRECTION,
    DesignSpec,
    validate_design_spec,
)
from .input_safety import scan_design_spec, scan_generated_text
from .selection_semantics import (
    COLOUR_ROLE_LABELS,
    colour_role_values,
    coverage_area_values,
    explicit_head_covering_decision,
    head_covering_answer,
    legacy_coverage_values,
    ordered_colour_values,
)

# Bump ONLY with a deliberate snapshot review and manifest update. The snapshot
# regeneration command REFUSES to overwrite committed snapshots unless this
# value changes (see prompt_snapshots.evaluate_regeneration); the persisted
# provenance records this value.
#
# 8.0.0 (prompt builder v8): the prompt is cut to roughly a third of its 7.0.0
# length so it fits the image encoder's attention window, every directive is
# rephrased positively (diffusion conditioning cannot represent negation), each
# garment's one-piece/two-piece construction is named, coverage is stated once
# and early instead of twice, and redundant model-authored prose is no longer
# rendered. Every schema version's output changes.
#
# 8.1.0: the first live 8.0.0 generation held every coverage requirement but
# ignored the colour — `pistachio` rendered as blush pink, no green anywhere —
# and cut a 196-character overall_form mid-sentence, the fragment displacing the
# colour placement from the budget. Minor, not major: the v8 architecture is
# unchanged and this is a wording and robustness fix on top of it. It still
# bumps, because a real DesignVersion already carries 8.0.0 as immutable
# provenance and its stored prompt must keep matching the version that produced
# it.
#
# 8.2.0: both live 8.x runs ignored an explicit ["none"] embellishment selection
# and rendered gold borders and scattered motifs. 8.1.0's wording was
# grammatically positive but semantically asked for an absence, and
# "unworked"/"undecorated" carry the concepts they negate. The unembellished
# clause and finishing directive now describe what the cloth IS.
PROMPT_BUILDER_VERSION = "8.2.0"

# What the narrative budget aims at. Chosen so the assembled prompt fits inside
# the default image model's text-encoder attention window (~512 tokens), which is
# the whole point of 8.0.0 — content past that window is weakly conditioned or
# dropped, and 7.0.0 put a third of every prompt there.
IMAGE_PROMPT_TARGET_CHARS = 1500

# Hard upper bound on the assembled prompt, guaranteed by construction. Higher
# than the target because a schema-valid DesignSpec may carry a 64-character
# machine value in every canonical list, which the builder must render in full:
# such a spec drops ALL narrative and still fits here. Realistic specs never
# approach it.
IMAGE_PROMPT_MAX_CHARS = 2600

# Conservative reserve (from the narrative budget) for the whitespace that joins
# pieces and sections. There are at most ten sections (18 characters of blank
# line) and at most ten intra-section joins, so the real cost stays under 30;
# reserving much more than that just buys nothing with characters the narrative
# could have used.
_SEPARATOR_RESERVE = 48

# Documented per-slot character caps. Each generated narrative string is first
# normalised and truncated at a word boundary to at most its slot cap; section
# budgeting may then shorten it further. Critical machine selections and coverage
# choices are rendered directly from short machine values and are never subject
# to these caps or to budgeting.
#
# The caps are far tighter than 7.0.0's (300/200) because 8.0.0 inverts the
# balance: canonical selections carry the design and narrative supplements it.
# 240, not 180: the first live 8.0.0 generation produced a 196-character
# overall_form, which a 180 cap cut mid-sentence. The schema allows 400, so an
# ordinary one-sentence description must fit whole rather than be dropped by
# :func:`_truncate_at_sentence`.
_FORM_CAP = 240
_PLACEMENT_CAP = 200
_AREA_CAP = 100
_LIST_ITEM_CAP = 90

# How many items of each canonical list are rendered. Real questionnaire answers
# are far under these; the schema's eight-item ceilings are a hostile-input
# backstop, and naming eight of anything dilutes the prompt. Order is preserved,
# so the kept items are always the most important ones.
_MAX_COLOURS = 4
_MAX_FABRICS = 3
_MAX_EMBELLISHMENT_STYLES = 4

# Fixed, garment-agnostic catalogue-composition directive — the FIRST and
# highest-priority section of every prompt (see ADR 0010). It fixes the framing
# (exactly one adult model, standing, centred, full head-to-foot view with the
# complete garment and any trailing fabric inside the frame), a seamless plain
# neutral studio backdrop, soft even studio lighting, and the garment (not the
# face, jewellery or setting) as the primary subject. It is positive natural
# language only — no negative prompt — and applies across sarees, lehengas,
# shararas/ghararas, anarkalis and kurta-style outfits. Because it is mandatory
# and rendered first, lower-priority garment detail can never displace or
# truncate it. Shortened for 8.0.0 with no requirement removed: the 7.0.0 wording
# spent 615 characters saying this.
_COMPOSITION = (
    "Full-length South Asian bridalwear catalogue photograph of exactly one adult "
    "model standing upright and facing the camera, centred in frame and complete "
    "from the top of the head to both feet, with the whole outfit, the hem, the "
    "dupatta fall and any trailing fabric inside the frame and clear space around "
    "the subject. Seamless plain neutral studio backdrop, soft even studio "
    "lighting, the garment itself as the subject of the photograph."
)

# Fixed positive-only photographic-finishing directive — the LAST, lowest-priority
# section. It carries only the design-integrity safeguards (original/non-branded
# textile and embroidery, natural anatomy, coherent hands, colour-faithful even
# lighting); all framing/backdrop/lighting composition lives in _COMPOSITION, so
# this wording never duplicates it. There is deliberately NO negative prompt and
# NO universal modesty/sleeve/neckline suffix — coverage comes only from the
# DesignSpec so a generic suffix can never contradict validated choices.
_FINISHING = (
    "Render an original, non-branded textile and embroidery design with natural "
    "anatomy and coherent hands, in even light true to the real fabric colour."
)

# Finishing directive for an unembellished garment (embellishment_styles ==
# ["none"]). Same safeguards, but it does NOT ask for embroidery detail — it
# directs attention to the plain textile, colour, texture, drape and garment
# construction instead, so it never contradicts the "none" selection.
_FINISHING_UNEMBELLISHED = (
    "Render an original, non-branded textile design with natural anatomy and "
    "coherent hands, in even light true to the smooth, single-colour cloth and "
    "the way it falls."
)

# How each garment is CONSTRUCTED, keyed only on source_selections.garment_type.
# A small, source-controlled map like the coverage clauses, NOT a broad cultural
# rules engine. New in 8.0.0: naming one-piece versus two-piece construction up
# front gives the model the garment's fundamental shape before any surface
# detail, and it absorbs the former _GARMENT_INTEGRITY_CUES (the gharara/sharara
# distinction and the saree's draped construction) stated POSITIVELY for every
# garment rather than as a negation on some.
_GARMENT_CONSTRUCTION = {
    "lehenga": "a two-piece outfit of a fitted choli blouse with a separate long flared skirt",
    "saree": (
        "a single draped length of fabric with the pallu falling over a separate fitted blouse"
    ),
    "anarkali": "a one-piece floor-length flared kurta worn over narrow trousers",
    "gharara": (
        "a two-piece outfit of a kurti over separate trousers, fitted through the "
        "upper leg and knee and flaring below the knee"
    ),
    "sharara": (
        "a two-piece outfit of a kurti over separate trousers flaring in one "
        "continuous line from the waist to a wide hem"
    ),
    "shalwar_kameez": "a two-piece outfit of a long tunic over separate trousers",
}

# The canonical machine value marking "no embellishment". When embellishment
# styles is exactly this, generated embellishment narrative is omitted and a
# clear unembellished direction is rendered instead.
_NONE_EMBELLISHMENT = "none"

# Deterministic neutralisation of heavy/dense directions in the GENERATED
# embellishment narrative when the canonical density is "minimal", so generated
# text cannot contradict the explicit selection. Applied only to embellishment
# narrative — never to canonical machine selections. Order matters (multi-word
# entries before their single-word components).
_HEAVY_WORD_REPLACEMENTS = (
    (re.compile(r"\brichly[- ]worked\b", re.IGNORECASE), "lightly worked"),
    (re.compile(r"\bheavily\b", re.IGNORECASE), "lightly"),
    (re.compile(r"\bheavy\b", re.IGNORECASE), "restrained"),
    (re.compile(r"\bdensely\b", re.IGNORECASE), "lightly"),
    (re.compile(r"\bdense\b", re.IGNORECASE), "light"),
    (re.compile(r"\bopulent\b", re.IGNORECASE), "understated"),
    (re.compile(r"\blavish\b", re.IGNORECASE), "understated"),
    (re.compile(r"\brichly\b", re.IGNORECASE), "lightly"),
)

_WHITESPACE = re.compile(r"\s+")


class ImagePromptBuildError(Exception):
    """The image prompt could not be built safely.

    Carries only a generic, safe message — never the prompt contents, spec
    narrative or any user data — so it is always safe to surface and log."""


# Trailing list/clause separators left behind by word-boundary truncation at a
# slot cap, which would otherwise render as "... border;." at the end of a piece.
_TRAILING_SEPARATORS = " ;,:-"


@dataclass
class _Piece:
    """One ordered fragment of a section.

    ``mandatory`` pieces are always kept in full. A narrative piece may be
    shortened or omitted to honour the budget, and carries its ``prefix``
    separately ("Motifs: ", "Concentrated at ") so the label is rendered only
    when a usable body survives — an orphaned label states nothing."""

    text: str
    mandatory: bool = False
    prefix: str = ""

    @property
    def length(self) -> int:
        """The rendered length, including the prefix and sentence terminator."""
        return len(self.rendered)

    @property
    def rendered(self) -> str:
        if not self.text:
            return ""
        return _sentence(f"{self.prefix}{self.text}")


def _slot(text: str, cap: int) -> str:
    """Normalise one generated narrative string into a bounded prompt slot.

    Applies Unicode NFKC normalisation, converts CRLF/CR to LF, collapses all
    internal whitespace to single spaces, strips ends, and truncates at a word
    boundary to at most ``cap`` characters. Preserves meaningful words and never
    inserts HTML, Markdown or control characters."""
    normalised = unicodedata.normalize("NFKC", text)
    normalised = normalised.replace("\r\n", "\n").replace("\r", "\n")
    normalised = _WHITESPACE.sub(" ", normalised).strip()
    return _trim_separators(_truncate_at_sentence(normalised, cap))


def _truncate_at_sentence(text: str, limit: int) -> str:
    """Keep whole sentences of ``text`` up to ``limit`` characters.

    TOTAL, and stronger than 7.0.0's word-boundary rule: it never returns a
    partial SENTENCE. Word-boundary truncation was safe against partial tokens
    but not against partial meaning — the first live 8.0.0 generation rendered
    "…draped over the shoulder rather than a fully stitched." because the field
    was 196 characters against a 180 cap, and 196 is an entirely ordinary length
    for a 400-character field. A dangling comparison conditions the provider on
    less than the clean absence of the detail, and it cost 180 characters of
    budget that the colour placement would otherwise have used.

    When no sentence ends within ``limit`` the whole (non-mandatory narrative)
    piece is omitted by returning ``""`` — the builder then drops the empty
    piece. Mandatory canonical machine values are bounded by the schema and are
    never routed through this helper."""
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text
    boundary = max(text.rfind(mark, 0, limit + 1) for mark in ".!?")
    if boundary < 0:
        return ""
    return text[: boundary + 1].rstrip()


def _join_items(items: list[str], limit: int | None = None) -> str:
    """Render a bounded narrative list as one clause, preserving order.

    ``limit`` caps how many items are rendered; the schema permits eight, and
    naming eight of anything dilutes the prompt."""
    selected = items if limit is None else items[:limit]
    rendered = [_slot(item, _LIST_ITEM_CAP) for item in selected]
    return "; ".join(part for part in rendered if part)


def _readable(value: str) -> str:
    """A machine option value as readable words (never a questionnaire label)."""
    return value.replace("_", " ").strip()


def _readable_list(values: list[str]) -> str:
    return ", ".join(_readable(value) for value in values if value)


def _sentence(text: str) -> str:
    """Ensure a rendered fragment ends with a single sentence terminator."""
    text = text.strip()
    if not text:
        return ""
    return text if text[-1] in ".!?" else text + "."


def _neutralise_heavy(text: str) -> str:
    """Replace heavy/dense directions with restrained equivalents (minimal
    density). Deterministic, word-boundary, applied to generated narrative
    only."""
    for pattern, replacement in _HEAVY_WORD_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def _trim_separators(text: str) -> str:
    """Drop a dangling list separator left by word-boundary truncation."""
    return text.rstrip(_TRAILING_SEPARATORS)


def _mandatory(text: str) -> _Piece:
    return _Piece(text, mandatory=True)


def _narrative(text: str, prefix: str = "") -> _Piece:
    return _Piece(text, mandatory=False, prefix=prefix)


def _garment(spec: DesignSpec) -> list[_Piece]:
    """The garment's identity, construction and silhouette — the second section.

    Placed immediately after composition because a prompt that renders the wrong
    garment wastes every other requirement in it. The construction clause states
    one-piece versus two-piece before any surface detail.

    ``title`` and ``concept_summary`` are NOT rendered: both are whole-design
    prose restating what the canonical selections and the sections below already
    carry. Nor is ``garment_breakdown.garment_components`` — 8.0.0's construction
    clause names the garment's pieces canonically and the drape section names the
    dupatta, so the model's component list ("Long kameez; Waist-flared sharara
    trousers; Head dupatta") restates both, and at this budget it was winning
    against embellishment placement, which is genuinely additive."""
    ss = spec.source_selections
    garment = _readable(ss.garment_type)
    ceremony = _readable(ss.ceremony)
    opening = f"A South Asian bridal {garment} for a {ceremony} ceremony"
    construction = _GARMENT_CONSTRUCTION.get(ss.garment_type)
    if construction:
        opening = f"{opening}: {construction}"
    return [
        _mandatory(opening),
        _mandatory(f"The silhouette is {_readable(ss.silhouette)}"),
        _narrative(_slot(spec.garment_breakdown.overall_form, _FORM_CAP)),
    ]


# Unambiguous colour wording for each canonical colour value. A small,
# source-controlled map like the garment-construction and coverage clauses, NOT
# a colour theory engine — it only makes the HUE explicit.
#
# The first live 8.0.0 generation asked for `pistachio` and rendered blush pink,
# with no green anywhere. Most of the questionnaire's palette is named after an
# object rather than a colour — pistachio, sage, rust, peacock, oxblood,
# aubergine, champagne, marigold, mint, pearl, amethyst — and an image model
# reads a bare object noun as the object, or ignores it and falls back on the
# overwhelming pink/red/gold prior that "South Asian bridal" carries. Naming the
# hue alongside the name ("pale pistachio green") removes the ambiguity while
# keeping the shade the user actually chose.
#
# The questionnaire remains the authority over WHICH values exist; an
# unrecognised value falls back to its readable form and is never invented.
_COLOUR_DESCRIPTORS = {
    "scarlet": "bright scarlet red",
    "deep_maroon": "deep maroon red",
    "oxblood": "dark oxblood red",
    "rust": "burnt rust orange",
    "blush": "pale blush pink",
    "rose": "rose pink",
    "rani_pink": "vivid rani pink",
    "old_rose": "muted old rose pink",
    "marigold": "bright marigold orange",
    "antique_gold": "antique gold",
    "champagne": "pale champagne gold",
    "ivory": "warm ivory white",
    "emerald": "rich emerald green",
    "mehndi_green": "deep mehndi green",
    "sage": "soft sage green",
    "pistachio": "pale pistachio green",
    "royal_blue": "royal blue",
    "navy": "dark navy blue",
    "peacock": "deep peacock blue-green",
    "powder_blue": "pale powder blue",
    "aubergine": "deep aubergine purple",
    "amethyst": "amethyst purple",
    "lilac": "soft lilac purple",
    "plum_wine": "deep plum wine purple",
    "silver_grey": "silver grey",
    "pearl": "soft pearl white",
    "mint": "pale mint green",
    "peach": "soft peach orange",
}


def _readable_colour(value: str) -> str:
    """A colour selection as prompt wording: a bride-supplied hex is named as a
    literal colour code, a canonical option value as an unambiguous colour
    phrase (falling back to its readable form when unrecognised)."""
    if value.startswith("#"):
        return f"the exact colour code {value}"
    return _COLOUR_DESCRIPTORS.get(value, _readable(value))


def _colour_roles_clause(ss) -> str | None:
    """The version-3 per-role colour requirement, or ``None`` for version 1/2.

    ``match_fabric`` is rendered as the relationship it is, never as a colour."""
    roles = colour_role_values(ss)
    if not roles:
        return None
    parts = []
    for field, value in roles:
        where = COLOUR_ROLE_LABELS[field]
        if value == COLOUR_MATCH_FABRIC:
            parts.append(f"{where} in the same colour as the main fabric")
        else:
            parts.append(f"{where} in {_readable_colour(value)}")
    return "Colours: " + ", ".join(parts)


def _colour(spec: DesignSpec) -> list[_Piece]:
    ss = spec.source_selections
    pieces = []
    # Version 3 names a colour per garment role; versions 1 and 2 have one
    # ordered palette. The bride's saved custom_colours palette is deliberately
    # NOT rendered — it is the set a colour question may be answered FROM, not a
    # selection, and listing unused colours would invent requirements.
    roles_clause = _colour_roles_clause(ss)
    if roles_clause:
        pieces.append(_mandatory(roles_clause))
    else:
        # Version 1/2 palettes get the same unambiguous colour wording as the
        # version-3 per-role clause above.
        colours = ", ".join(
            _readable_colour(value) for value in list(ordered_colour_values(ss))[:_MAX_COLOURS]
        )
        if colours:
            pieces.append(_mandatory(f"Colours, in order of importance: {colours}"))
    # colour_story.placement says WHERE each colour sits, which the canonical
    # list cannot. colour_story.palette_summary restates the list itself and
    # colour_story.rationale explains WHY it was chosen; neither is rendered.
    pieces.append(_narrative(_slot(spec.colour_story.placement, _PLACEMENT_CAP)))
    return pieces


def _fabrics(spec: DesignSpec) -> list[_Piece]:
    """The canonical fabric selections.

    ``fabrics_and_texture`` narrative is NOT rendered: the canonical fabric names
    already carry their own texture and drape semantics for an image model. The
    one exception is a spec with no canonical fabrics at all (the schema permits
    an empty list), where the narrative entries' fabric names are the only fabric
    information the design has."""
    ss = spec.source_selections
    if ss.fabrics:
        return [_mandatory(f"Fabrics: {_readable_list(list(ss.fabrics)[:_MAX_FABRICS])}")]
    # Mandatory, not narrative: when it fires this is the design's ONLY fabric
    # statement, so the budget must not be able to drop it — the same reasoning
    # that makes the canonical line above mandatory.
    named = _join_items([entry.fabric for entry in spec.fabrics_and_texture], limit=_MAX_FABRICS)
    return [_mandatory(f"Fabrics: {named}")] if named else []


def _embellishment(spec: DesignSpec) -> list[_Piece]:
    ss = spec.source_selections
    ep = spec.embellishment_plan
    # Canonical authority: exactly ["none"] means no embellishment. "none" wins
    # over any persisted density (minimal/balanced/heavy is NOT rendered), and
    # ALL generated embellishment-plan content is omitted.
    #
    # The instruction DESCRIBES THE MATERIAL rather than the absence of
    # decoration. 8.1.0 said "plain and unworked, with a smooth undecorated
    # surface" — grammatically positive, but semantically a request for an
    # absence, and "unworked"/"undecorated" carry the very concepts they negate.
    # Both live 8.x runs rendered gold borders and scattered motifs against it:
    # "South Asian bridalwear catalogue photograph" is about as strong an
    # ornate-embroidery prior as exists, and weak affirmatives lose to it. Every
    # word here now names something the cloth positively IS.
    if ss.embellishment_styles == [_NONE_EMBELLISHMENT]:
        return [
            _mandatory(
                "The fabric is one continuous expanse of solid colour, flat and "
                "uniform from edge to edge, its beauty coming from the sheen and "
                "fall of the cloth alone"
            )
        ]

    pieces = []
    if ss.embellishment_density:
        pieces.append(_mandatory(f"Embellishment density: {_readable(ss.embellishment_density)}"))
    if ss.embellishment_styles:
        styles = _readable_list(list(ss.embellishment_styles)[:_MAX_EMBELLISHMENT_STYLES])
        pieces.append(_mandatory(f"Embellishment: {styles}"))

    minimal = ss.embellishment_density == "minimal"

    def narrate(text: str) -> str:
        return _neutralise_heavy(text) if minimal else text

    # Only WHERE the embellishment sits and WHAT it depicts are rendered.
    # ``techniques`` restates the canonical styles, and ``density`` /
    # ``restraint_notes`` restate the canonical density line above them.
    placement = _join_items(ep.placement, limit=3)
    if placement:
        pieces.append(_narrative(narrate(placement), prefix="Concentrated at "))
    motifs = _join_items(ep.motifs, limit=3)
    if motifs:
        pieces.append(_narrative(narrate(motifs), prefix="Motifs: "))
    return pieces


# Concrete, garment-neutral VISUAL clauses for the version-1/2
# ``coverage_preferences`` multi-select. Keyed only on canonical machine values —
# a small, source-controlled set like the garment-construction clauses, NOT a
# broad rules engine. Only coverage-INCREASING selections get a clause; there an
# absent value meant nothing had been asked for, so a deliberately less-covered
# choice (sleeveless, short/elbow/three-quarter sleeves) gets none and the
# directive can never contradict a validated choice. Insertion order is the
# render order.
#
# Every clause states what must be PRESENT. 7.0.0 defended these requirements
# with negations ("not an open, scooped or sweetheart neckline", "not left
# open", "no bare skin"); diffusion text conditioning has no representation for
# negation, so those clauses conditioned on exactly what they meant to exclude.
_COVERAGE_CLAUSES = {
    "full_sleeves": "full-length sleeves reaching the wrists, covering both arms",
    "high_neckline": "a closed high neckline covering the collarbone and upper chest",
    "full_midriff": "a midriff fully covered by fabric at the waist",
    "full_back": "a back fully covered by the garment",
}
# Concrete, garment-neutral VISUAL clauses for the dedicated canonical neckline
# (DesignSpec v2 / Phase 16B). Keyed only on the canonical `neckline_style`
# machine value. When a neckline is chosen it is rendered as an explicit,
# mandatory visual requirement beside the other coverage requirements, and the
# generated neckline narrative is suppressed so it can never contradict the
# canonical choice. A v1 spec, or a v2 spec with no neckline preference, renders
# no neckline clause.
_NECKLINE_CLAUSES = {
    "classic_crew": "a crew neckline sitting at the base of the neck",
    "curved_scoop": "a scoop neckline curving just below the collarbone",
    "v_neck": "a moderate V-shaped neckline",
    "deep_v_neck": "a deep V-shaped neckline plunging below the collarbone",
    "boat_neck": "a boat neckline running straight from shoulder to shoulder",
    "square_neck": "a square neckline cut across the chest",
    "sweetheart_neck": "a sweetheart neckline curved like the top of a heart",
    "high_neck": "a closed high neckline covering the collarbone and upper chest",
    "band_collar": "an upright band collar standing at the neck",
}


def _canonical_neckline(ss) -> str | None:
    """The canonical neckline clause for a v2/v3 spec with a neckline preference,
    or ``None`` for a v1 spec / no preference. Uses ``getattr`` because a
    version-1 SourceSelections has no ``neckline_style`` attribute."""
    value = getattr(ss, "neckline_style", None)
    return _NECKLINE_CLAUSES.get(value) if value else None


# Concrete, garment-neutral VISUAL clauses for the version-3 per-body-area
# coverage selections (questionnaire v4). Unlike the version-1/2
# ``coverage_preferences`` multi-select — where an absent value simply meant
# "not requested", so only coverage-INCREASING values could safely be rendered —
# each version-3 area is a single explicit answer. Rendering a less-covered
# answer therefore contradicts nothing; it states what the user actually chose.
# Head covering is handled separately because its wording depends on which
# garment carries the fabric. Insertion order is the render order.
_SLEEVE_CLAUSES = {
    "sleeveless": "a sleeveless bodice with bare shoulders and arms",
    "cap_sleeve": "short cap sleeves covering the top of the shoulder",
    "elbow_sleeve": "elbow-length sleeves",
    "three_quarter_sleeve": "three-quarter sleeves ending between the elbow and the wrist",
    "full_sleeve": "full-length sleeves reaching the wrists, covering both arms",
}
_BACK_CLAUSES = {
    "open_back": "an open back",
    "deep_cut_back": "a deeply cut open back",
    "modest_back": "a back fully covered by the garment",
}
_MIDRIFF_CLAUSES = {
    "bare_midriff": "a bare midriff at the waist",
    "semi_sheer_midriff": "a midriff veiled in sheer fabric",
    "covered_midriff": "a midriff fully covered by fabric at the waist",
}
# Head coverings that enclose the hair. ``uncovered`` is deliberately absent: it
# is a real answer, but an uncovered head is the model's default and stating it
# adds nothing to the visual requirement list.
_HEAD_COVERING_CLAUSES = {
    "veil_style": "a veil worn over the head, enclosing all of the hair",
    "hijab": "a hijab wrapping the head and neck, enclosing all of the hair",
}
_AREA_CLAUSES = {
    "sleeves": _SLEEVE_CLAUSES,
    "back_coverage": _BACK_CLAUSES,
    "midriff": _MIDRIFF_CLAUSES,
}

# The user's explicit head-covering coverage preference, and the dupatta styling
# that also means "worn over the head". Either signals that the hair is enclosed.
_HEAD_DRAPE_DUPATTA = "head_drape"


def _wants_head_covered(ss) -> bool:
    """True when the hair must be enclosed.

    The user's own decision wins whenever they made one (see
    :func:`explicit_head_covering_decision`); otherwise a dupatta worn over the
    head implies a covered head, exactly as before."""
    decision = explicit_head_covering_decision(ss)
    if decision is not None:
        return decision
    return ss.dupatta_style == _HEAD_DRAPE_DUPATTA


def _head_covering_clause(ss) -> str | None:
    """The head-covering visual requirement, or ``None`` when the head is not
    required to be covered. A version-3 answer naming a specific covering (a
    veil, a hijab) renders that covering; everything else renders the
    garment-aware drape wording."""
    if not _wants_head_covered(ss):
        return None
    specific = _HEAD_COVERING_CLAUSES.get(head_covering_answer(ss))
    if specific:
        return specific
    return (
        f"{_head_cover_reference(ss)} drawn up over the head as a veil, "
        "enclosing all of the hair"
    )


def _head_cover_reference(ss) -> str:
    """Garment-neutral name for the fabric that covers the head: the pallu for a
    saree, otherwise the dupatta, otherwise a generic head covering. Never invents
    a dupatta for a saree with ``dupatta_style=None``."""
    if ss.saree_drape:
        return "the saree pallu"
    if ss.dupatta_style:
        return "the dupatta"
    return "the head covering"


def _coverage_directive(spec: DesignSpec) -> list[_Piece]:
    """The coverage requirements, stated ONCE as explicit visual requirements.

    Rendered high in the prompt — third, after composition and the garment's
    identity — because these are the requirements the provider most often drops.
    8.0.0 removes 7.0.0's closing restatement: at 7.0.0 lengths it sat outside
    the encoder's attention window and conditioned nothing, and inside a
    target-length prompt the single early statement is in-window by construction.

    Conditional throughout: it names only selected requirements, never forces
    coverage a less-covered choice did not ask for, and includes the head
    covering only when the user actually asked for one."""
    ss = spec.source_selections
    prefs = legacy_coverage_values(ss)
    # The canonical neckline is rendered FIRST so it sits at the very front of
    # the coverage requirements.
    clauses = []
    neckline = _canonical_neckline(ss)
    if neckline:
        clauses.append(neckline)
    clauses += [clause for key, clause in _COVERAGE_CLAUSES.items() if key in prefs]
    for field, value in coverage_area_values(ss):
        clause = _AREA_CLAUSES.get(field, {}).get(value)
        if clause:
            clauses.append(clause)
    head_clause = _head_covering_clause(ss)
    if head_clause:
        clauses.append(head_clause)
    if not clauses:
        return []
    return [_mandatory("Show clearly in the render: " + "; ".join(clauses))]


def _coverage_narrative(spec: DesignSpec) -> list[_Piece]:
    """Generated coverage prose for the body areas with NO canonical answer.

    Every canonically answered area is already a mandatory visual requirement in
    the coverage directive, so its model-authored narrative is suppressed here —
    generated prose must never be able to contradict a validated choice. A v1/v2
    spec, which has no per-area questions, keeps whichever slots its selections
    did not already settle.

    A version-1/2 ``coverage_preferences`` value settles its area just as firmly
    as a version-3 answer does, so a slot the directive already states is
    suppressed too. 7.0.0 rendered both — "a closed high neckline covering the
    collarbone" in the directive and "Neckline: A closed high neckline..." in the
    prose — which cost characters twice for one requirement and gave generated
    text a chance to drift from the validated choice.

    ``coverage_and_drape.head_covering`` is dropped for EVERY version. It has no
    useful case: when the user asked for a covered head the directive already
    states the requirement concretely, and when they did not, the slot reads
    "No head covering..." — pure negation, which is exactly what 8.0.0 removes
    from the prompt."""
    ss = spec.source_selections
    cd = spec.coverage_and_drape
    answered = {field for field, _value in coverage_area_values(ss)}
    prefs = legacy_coverage_values(ss)
    pieces = []
    if "sleeves" not in answered and "full_sleeves" not in prefs:
        pieces.append(_narrative(_slot(cd.sleeves, _AREA_CAP), prefix="Sleeves: "))
    if _canonical_neckline(ss) is None and "high_neckline" not in prefs:
        pieces.append(_narrative(_slot(cd.neckline, _AREA_CAP), prefix="Neckline: "))
    # One narrative slot covers both the back and the midriff, so it is
    # suppressed only when BOTH are already settled.
    back_settled = "back_coverage" in answered or "full_back" in prefs
    midriff_settled = "midriff" in answered or "full_midriff" in prefs
    if not (back_settled and midriff_settled):
        pieces.append(
            _narrative(_slot(cd.back_and_midriff, _AREA_CAP), prefix="Back and midriff: ")
        )
    return pieces


def _drape(spec: DesignSpec) -> list[_Piece]:
    """The canonical dupatta/saree drape selections.

    ``coverage_and_drape.dupatta_or_saree_drape`` narrative is not rendered: the
    canonical drape value names the styling, and the composition directive
    already requires the whole dupatta fall to be in frame."""
    ss = spec.source_selections
    selections = []
    if ss.dupatta_style:
        selections.append(f"dupatta worn as a {_readable(ss.dupatta_style)}")
    if ss.saree_drape:
        selections.append(f"saree draped as a {_readable(ss.saree_drape)}")
    return [_mandatory("Drape: " + ", ".join(selections))] if selections else []


def _regional(spec: DesignSpec) -> list[_Piece]:
    """The user's canonical regional selection, framed as influence not a rule.

    The CANONICAL value is rendered, not ``cultural_context.regional_direction``.
    7.0.0 rendered the model's prose elaboration and 8.0.0 first kept it as a
    narrative slot — where, being last in priority, it was dropped by the budget
    for every single reviewed fixture. The user's regional choice reached the
    image prompt in no case at all. A short mandatory clause built from the
    canonical selection always renders and costs a fraction as much.

    ``regional_direction``, ``interpretation_notes`` and ``safeguards`` are
    therefore all unrendered: they are editorial prose about how the direction
    should be read, which carries nothing an image model can draw. They remain
    in the persisted DesignSpec brief. ``styling_notes`` are likewise not
    rendered — advisory beauty/jewellery prose pulls the provider toward
    portraiture and can contradict the coverage requirements.

    The framing stays non-prescriptive (CLAUDE.md §12): "broad" and "influence"
    both say the direction guides rather than governs. 7.0.0 spelt that out as
    "offered as guidance rather than a universal rule", which is meta-language an
    image model cannot use — and 8.0.0's first wording, "not a rule", reintroduced
    the one negation left in the builder."""
    style = spec.source_selections.regional_style
    if style is None or style == NO_REGIONAL_DIRECTION:
        return []
    return [_mandatory(f"Broad regional influence: {_readable(style)}")]


# Fixed conceptual ordering, documented as a priority hierarchy in ADR 0010 and
# snapshot-tested. The leading composition directive and the trailing finishing
# directive are added directly in build_image_prompt; these builders render
# between them in descending priority: the garment's identity and construction,
# the coverage requirements and the coverage narrative that completes them,
# colour, fabric, embellishment, drape, then broad regional influence.
#
# The coverage narrative sits with the directive rather than after embellishment
# (ADR 0010's long-standing "coverage outranks decoration"). It matters at 8.0.0
# budgets in a way it never did at 6,000 characters: ranked below embellishment,
# a version-1 spec's neckline narrative — the ONLY neckline information such a
# spec has, since it carries no canonical neckline — was dropped in favour of
# embellishment motifs.
_SECTION_BUILDERS = (
    _garment,
    _coverage_directive,
    _coverage_narrative,
    _colour,
    _fabrics,
    _embellishment,
    _drape,
    _regional,
)


def _apply_narrative_budget(sections: list[list[_Piece]], budget: int) -> None:
    """Shrink narrative pieces IN PLACE so their combined length ≤ ``budget``.

    A single greedy pass in PRIORITY (reading) order across every section:
    each narrative piece that still fits is kept whole, and the budget is
    consumed as it goes. Mandatory pieces are never touched.

    7.0.0 split the budget proportionally between sections, which is the right
    shape when almost everything fits. At 8.0.0's budget it is the wrong one —
    it hands every section a slice too thin to render anything, starving the
    most valuable slots (the garment's overall form, where the colours sit)
    along with the least. Priority order spends the budget on the highest-value
    narrative first and drops the tail, which is what the fixed section order
    already encodes.

    Selection is ALL-OR-NOTHING per piece: a piece that does not fit is dropped
    whole, prefix included, never cut down to fill the remainder. Budget-level
    truncation is what produced 8.0.0's worst early output — dangling fragments
    like "fitted to the knee before a." and a bare "Motifs:" label — and a
    half-sentence conditions the provider on less than the clean absence of the
    detail. The per-slot caps already bound each piece, so all-or-nothing costs
    little; a dropped piece frees its whole length, so a later, shorter piece
    that still fits is kept rather than wasting the remainder."""
    remaining = budget
    for section in sections:
        for piece in section:
            if piece.mandatory:
                continue
            if piece.length <= remaining:
                remaining -= piece.length
            else:
                piece.text = ""


def build_image_prompt(spec: DesignSpec) -> str:
    """Build the deterministic natural-language image prompt for ``spec``.

    Accepts a validated :class:`DesignSpec` (or any DesignSpec-compatible
    payload, revalidated defensively here). Runs the generated-content safety
    scan before interpolation, reserves the mandatory content and budgets the
    generated narrative into the remainder so the result aims at
    :data:`IMAGE_PROMPT_TARGET_CHARS` and never exceeds
    :data:`IMAGE_PROMPT_MAX_CHARS`, then runs a final safety scan. Raises ONLY
    :class:`ImagePromptBuildError` — never :class:`GeneratedContentRejected` —
    and never echoes the prompt or spec text."""
    if not isinstance(spec, DesignSpec):
        try:
            # Version-dispatched: a raw dict is validated as v1, v2 or v3 by its
            # own schema_version (a DesignSpecV2/V3 instance already passes the
            # isinstance check above, since both subclass DesignSpec).
            spec = validate_design_spec(spec)
        except Exception as exc:  # controlled: never surface the payload
            raise ImagePromptBuildError("design spec failed validation") from exc

    # Safety scan over every generated string BEFORE interpolation, wrapped so
    # only ImagePromptBuildError escapes (the rejected text is never surfaced).
    try:
        scan_design_spec(spec)
    except Exception as exc:
        raise ImagePromptBuildError("design spec failed the safety scan") from exc

    # Composition leads (highest priority, mandatory, rendered first) so it is
    # always the first content and can never be displaced or truncated.
    sections = [[_Piece(_COMPOSITION, mandatory=True)]]
    sections += [list(builder(spec)) for builder in _SECTION_BUILDERS]
    # Unembellished designs use finishing wording that does not ask for embroidery
    # detail, so the fixed wording never contradicts a "none" choice.
    unembellished = spec.source_selections.embellishment_styles == [_NONE_EMBELLISHMENT]
    sections.append(
        [_Piece(_FINISHING_UNEMBELLISHED if unembellished else _FINISHING, mandatory=True)]
    )

    mandatory_len = sum(p.length for section in sections for p in section if p.mandatory)
    natural_total = sum(p.length for section in sections for p in section if not p.mandatory)
    # Budget against the TARGET, not the hard bound. A spec whose canonical
    # machine values alone overshoot the target yields a negative budget, which
    # simply drops all narrative — the hard bound still holds by construction.
    budget = max(IMAGE_PROMPT_TARGET_CHARS - mandatory_len - _SEPARATOR_RESERVE, 0)
    if natural_total > budget:
        _apply_narrative_budget(sections, budget)

    rendered = []
    for section in sections:
        texts = [piece.rendered for piece in section if piece.rendered]
        if texts:
            rendered.append(" ".join(texts))
    prompt = "\n\n".join(rendered)

    # Final safety scan on the finished prompt: blocked designer/brand,
    # imitation phrase, URL, prompt leakage, untrusted-section delimiter and
    # control characters are all covered by scan_generated_text.
    try:
        scan_generated_text(prompt)
    except Exception as exc:
        raise ImagePromptBuildError("assembled image prompt failed the safety scan") from exc

    if len(prompt) > IMAGE_PROMPT_MAX_CHARS:
        # Unreachable given the budget above; never slice a completed prompt.
        raise ImagePromptBuildError("assembled image prompt exceeded the maximum length")
    return prompt
