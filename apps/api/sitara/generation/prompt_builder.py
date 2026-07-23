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

## Composition- and coverage-first ordering

The prompt LEADS with a fixed catalogue-composition directive (framing, studio
backdrop, even lighting, garment as primary subject) so the highest-priority
instruction is the first content the model reads and can never be displaced or
truncated by lower-priority garment detail. A concrete, garment-neutral coverage
directive follows immediately (rendering the canonical high-neckline / sleeve /
midriff / back / head-covering selections as explicit visual requirements), so
the coverage the provider most often ignores appears high in the prompt; the
critical coverage requirements are briefly restated last. Garment-detail sections
render in a documented priority order between them, and a short
photographic-finishing directive precedes the closing coverage reinforcement.
Advisory styling notes and non-visual colour rationale are NOT rendered (they
pull the provider toward portraiture and can contradict coverage). See ADR 0010.

## Bounded rendering

The DesignSpec schema permits several eight-item narrative lists and eight
fabric entries, so per-slot caps alone cannot guarantee the global bound.
Rendering therefore reserves space for the MANDATORY content first — the leading
composition directive, garment and ceremony, the canonical silhouette, the
garment-integrity cue, the canonical colour/fabric/embellishment selections, all
canonical coverage preferences, the canonical dupatta/saree drape and the fixed
finishing wording — and lets generated narrative consume only the remaining
budget, shared across sections in fixed order. When a section's narrative exceeds
its budget, lower-priority generated details are deterministically shortened at a
word boundary or omitted; the composition directive, canonical selections,
coverage, garment-integrity and finishing content are never removed, and the
fully assembled prompt is never sliced.
"""

import re
import unicodedata
from dataclasses import dataclass

from .design_spec import DesignSpec, validate_design_spec
from .input_safety import scan_design_spec, scan_generated_text

# Bump ONLY with a deliberate snapshot review and manifest update. The snapshot
# regeneration command REFUSES to overwrite committed snapshots unless this
# value changes (see prompt_snapshots.evaluate_regeneration); the persisted
# provenance records this value.
#
# 6.0.0 (Phase 16B): the canonical prompt inputs changed — a dedicated
# ``neckline_style`` (DesignSpec v2) is now rendered as an explicit, mandatory
# visual requirement in the leading coverage directive and the closing
# reinforcement, and the generated neckline narrative is suppressed when a
# canonical neckline is chosen so it can never contradict it. Version-1 specs
# render exactly as before.
PROMPT_BUILDER_VERSION = "6.0.0"

# Hard upper bound on the assembled prompt. Guaranteed by construction: the
# mandatory content is reserved first and generated narrative is budgeted into
# the remainder, so every DesignSpec valid under the Pydantic schema builds to
# at most this many characters without slicing the finished prompt.
IMAGE_PROMPT_MAX_CHARS = 6000

# Conservative reserve (from the narrative budget) for the whitespace that joins
# pieces and sections. The total number of pieces is bounded by the schema, so
# the real separator count stays well under this.
_SEPARATOR_RESERVE = 128

# Documented per-slot character caps. Each generated narrative string is first
# normalised and truncated at a word boundary to at most its slot cap; section
# budgeting may then shorten it further. Critical machine selections and coverage
# choices are rendered directly from short machine values and are never subject
# to these caps or to budgeting.
# ``concept_summary`` is a model-authored whole-design PROSE restatement that
# overlaps every structured section below it, so it is the single most redundant
# slot. Its cap is deliberately tightened (700 -> 400) to bound that redundancy
# on verbose specs; it never affects a canonical machine selection, coverage,
# garment-integrity or the composition directive. The reviewed fixtures are all
# well under 400, so this changes no committed golden snapshot.
_SUMMARY_CAP = 400
_NARRATIVE_CAP = 300
_LIST_ITEM_CAP = 200

# Fixed, garment-agnostic catalogue-composition directive — the FIRST and
# highest-priority section of every prompt (see ADR 0010). It fixes the framing
# (exactly one adult model, standing, centred, full head-to-foot view with the
# complete garment and any trailing fabric inside the frame), a seamless plain
# neutral studio backdrop, soft even studio lighting, and the garment (not the
# face, jewellery or setting) as the primary subject. It is positive natural
# language only — no negative prompt — and applies across sarees, lehengas,
# shararas/ghararas, anarkalis and kurta-style outfits. Because it is mandatory
# and rendered first, lower-priority garment detail can never displace or
# truncate it.
_COMPOSITION = (
    "Full-length South Asian bridalwear catalogue photograph of exactly one "
    "adult model standing upright and primarily facing the camera, centred in "
    "the frame. Place the camera far enough back that the top of the head, both "
    "feet, the complete outfit, the garment hem, the dupatta fall and any train "
    "or trailing fabric stay fully inside the frame, with clear breathing room "
    "around the whole subject. Use a seamless plain neutral studio backdrop and "
    "soft, even, shadow-controlled studio lighting. Keep the garment's "
    "construction, drape, colour and embellishment the primary subject rather "
    "than the face, jewellery or surroundings."
)

# Fixed positive-only photographic-finishing directive — the LAST, lowest-priority
# section. It carries only the design-integrity safeguards (original/non-branded
# textile and embroidery, natural anatomy, coherent hands, colour-faithful even
# lighting); all framing/backdrop/lighting composition now lives in _COMPOSITION,
# so this wording no longer duplicates it. There is deliberately NO negative
# prompt and NO universal modesty/sleeve/neckline suffix — coverage comes only
# from the DesignSpec so a generic suffix can never contradict validated choices.
_FINISHING = (
    "Render an original, non-branded textile and embroidery design with natural "
    "anatomy and coherent, naturally posed hands, keeping the even lighting true "
    "to the real fabric colour and embroidery detail."
)

# Finishing directive for an unembellished garment (embellishment_styles ==
# ["none"]). Same safeguards, but it does NOT ask for embroidery detail — it
# directs attention to the plain textile, colour, texture, drape and garment
# construction instead, so it never contradicts the "none" selection.
_FINISHING_UNEMBELLISHED = (
    "Render an original, non-branded textile design with natural anatomy and "
    "coherent, naturally posed hands, keeping the even lighting true to the real "
    "fabric colour, texture, drape and garment detail."
)

# Very small, source-controlled garment-integrity cues for the categories with
# meaningful confusion risk in Phase 2. Keyed ONLY by source_selections
# garment_type; deliberately not a broad cultural rules engine.
_GARMENT_INTEGRITY_CUES = {
    "gharara": (
        "Show the gharara fitted through the upper leg and knee, with the flare "
        "beginning below the knee."
    ),
    "sharara": (
        "Show the sharara as trousers flaring from the waist or upper leg, "
        "without a gharara knee joint."
    ),
    "saree": (
        "Keep the saree as visibly draped fabric with a pallu over a blouse, not "
        "converted into a stitched gown."
    ),
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


@dataclass
class _Piece:
    """One ordered fragment of a section. ``mandatory`` pieces are always kept in
    full; narrative pieces may be shortened or omitted to honour the budget."""

    text: str
    mandatory: bool = False


def _slot(text: str, cap: int) -> str:
    """Normalise one generated narrative string into a bounded prompt slot.

    Applies Unicode NFKC normalisation, converts CRLF/CR to LF, collapses all
    internal whitespace to single spaces, strips ends, and truncates at a word
    boundary to at most ``cap`` characters. Preserves meaningful words and never
    inserts HTML, Markdown or control characters."""
    normalised = unicodedata.normalize("NFKC", text)
    normalised = normalised.replace("\r\n", "\n").replace("\r", "\n")
    normalised = _WHITESPACE.sub(" ", normalised).strip()
    return _truncate_at_word(normalised, cap)


def _truncate_at_word(text: str, limit: int) -> str:
    """Truncate ``text`` to at most ``limit`` characters at a word boundary.

    TOTAL: never returns a partial token. When the first token alone exceeds
    ``limit`` there is no safe word boundary, so the whole (non-mandatory
    narrative) piece is omitted by returning ``""`` — the builder then drops the
    empty piece. Mandatory canonical machine values are bounded by the schema and
    are never routed through this helper."""
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text
    boundary = text.rfind(" ", 0, limit + 1)
    if boundary <= 0:
        # The first token alone exceeds the limit → omit rather than emit a
        # partial token.
        return ""
    return text[:boundary].rstrip()


def _join_items(items: list[str]) -> str:
    """Render a bounded narrative list as one clause, preserving order."""
    rendered = [_slot(item, _LIST_ITEM_CAP) for item in items]
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


def _mandatory(text: str) -> _Piece:
    return _Piece(_sentence(text), mandatory=True)


def _narrative(text: str) -> _Piece:
    return _Piece(_sentence(text), mandatory=False)


def _garment_and_ceremony(spec: DesignSpec) -> list[_Piece]:
    ss = spec.source_selections
    garment = _readable(ss.garment_type)
    ceremony = _readable(ss.ceremony)
    pieces = [
        _narrative(_slot(spec.title, _NARRATIVE_CAP)),
        _mandatory(f"A South Asian bridal {garment} styled for a {ceremony} ceremony"),
        _narrative(_slot(spec.concept_summary, _SUMMARY_CAP)),
        _narrative(_slot(spec.garment_breakdown.overall_form, _NARRATIVE_CAP)),
    ]
    cue = _GARMENT_INTEGRITY_CUES.get(ss.garment_type)
    if cue:
        pieces.append(_Piece(cue, mandatory=True))
    return pieces


def _silhouette_and_components(spec: DesignSpec) -> list[_Piece]:
    gb = spec.garment_breakdown
    silhouette = _readable(spec.source_selections.silhouette)
    pieces = [
        _mandatory(f"The silhouette is {silhouette}"),
        _narrative(_slot(gb.silhouette, _NARRATIVE_CAP)),
    ]
    components = _join_items(gb.garment_components)
    if components:
        pieces.append(_narrative(f"Its components include {components}"))
    return pieces


def _drape_and_proportions(spec: DesignSpec) -> list[_Piece]:
    gb = spec.garment_breakdown
    return [
        _narrative(_slot(gb.drape_or_layering, _NARRATIVE_CAP)),
        _narrative(_slot(gb.key_proportions, _NARRATIVE_CAP)),
    ]


def _colour(spec: DesignSpec) -> list[_Piece]:
    cs = spec.colour_story
    pieces = []
    colours = _readable_list(spec.source_selections.colour_palette)
    if colours:
        pieces.append(_mandatory(f"The colour palette, in order, is {colours}"))
    pieces.append(_narrative(_slot(cs.palette_summary, _NARRATIVE_CAP)))
    pieces.append(_narrative(_slot(cs.placement, _NARRATIVE_CAP)))
    # colour_story.rationale (WHY the palette was chosen) is deliberately NOT
    # rendered into the image prompt: palette + placement already convey the
    # visual requirement, and the rationale is non-visual prose that only adds
    # length. It remains in the persisted DesignSpec brief.
    return pieces


def _fabrics(spec: DesignSpec) -> list[_Piece]:
    ss = spec.source_selections
    pieces = []
    if ss.fabrics:
        pieces.append(
            _mandatory(f"The selected fabrics, in order, are {_readable_list(ss.fabrics)}")
        )
    for entry in spec.fabrics_and_texture:
        fabric = _slot(entry.fabric, _LIST_ITEM_CAP)
        placement = _slot(entry.placement, _LIST_ITEM_CAP)
        finish = _slot(entry.finish_and_movement, _LIST_ITEM_CAP)
        detail = ". ".join(bit for bit in (placement, finish) if bit)
        pieces.append(_narrative(f"{fabric}: {detail}" if detail else fabric))
    return pieces


def _embellishment(spec: DesignSpec) -> list[_Piece]:
    ss = spec.source_selections
    ep = spec.embellishment_plan
    pieces = []
    # Canonical authority: exactly ["none"] means no embellishment. "none" wins
    # over any persisted density (minimal/balanced/heavy is NOT rendered), and
    # ALL generated embellishment-plan content is omitted — the canonical
    # selection is echoed and ONE clear unembellished instruction is given.
    if ss.embellishment_styles == [_NONE_EMBELLISHMENT]:
        pieces.append(_mandatory("The selected embellishment styles, in order, are none"))
        pieces.append(
            _mandatory(
                "The design carries no surface embellishment; render the fabric "
                "plain and unworked"
            )
        )
        return pieces

    if ss.embellishment_density:
        pieces.append(_mandatory(f"Embellishment density: {_readable(ss.embellishment_density)}"))
    if ss.embellishment_styles:
        pieces.append(
            _mandatory(
                "The selected embellishment styles, in order, are "
                f"{_readable_list(ss.embellishment_styles)}"
            )
        )

    minimal = ss.embellishment_density == "minimal"

    def narrate(text: str) -> str:
        return _neutralise_heavy(text) if minimal else text

    techniques = _join_items(ep.techniques)
    if techniques:
        pieces.append(_narrative(narrate(f"Techniques: {techniques}")))
    pieces.append(_narrative(narrate(_slot(ep.density, _NARRATIVE_CAP))))
    placement = _join_items(ep.placement)
    if placement:
        pieces.append(_narrative(narrate(f"Concentrated at {placement}")))
    motifs = _join_items(ep.motifs)
    if motifs:
        pieces.append(_narrative(narrate(f"Motifs: {motifs}")))
    pieces.append(_narrative(narrate(_slot(ep.restraint_notes, _NARRATIVE_CAP))))
    return pieces


# Concrete, garment-neutral VISUAL clauses for the coverage selections that FLUX
# most often ignores or contradicts (Phase image-composition follow-up: 4.0.0
# fixed framing but the model still rendered an open neckline and a bare head
# despite an explicit high-neck/head-covering DesignSpec). Keyed only on the
# canonical `coverage_preferences` machine values — a small, source-controlled
# set like the garment-integrity cues, NOT a broad rules engine. Only
# coverage-INCREASING selections get a clause; deliberately less-covered choices
# (sleeveless, short/elbow/three-quarter sleeves) get none, so the directive can
# never contradict a validated choice. Insertion order is the render order.
_COVERAGE_CLAUSES = {
    "full_sleeves": "full-length sleeves reaching the wrists, with both arms fully covered",
    "high_neckline": (
        "a fully closed high blouse neckline covering the collarbone and upper chest, "
        "not an open, scooped or sweetheart neckline"
    ),
    "full_midriff": "the midriff kept covered, with no bare skin at the waist",
    "full_back": "a covered back that is not left open",
}
# Short labels for the brief end-of-prompt reinforcement (same keys/order).
_COVERAGE_REINFORCE = {
    "full_sleeves": "full-length sleeves",
    "high_neckline": "a closed high neckline",
    "full_midriff": "a covered midriff",
    "full_back": "a covered back",
}
# Concrete, garment-neutral VISUAL clauses for the dedicated canonical neckline
# (DesignSpec v2 / Phase 16B). Keyed only on the canonical `neckline_style`
# machine value — a small, source-controlled set like the coverage clauses, NOT
# a broad rules engine. When a neckline is chosen it is rendered as an explicit,
# mandatory visual requirement beside the other coverage requirements (and
# briefly restated at the close), and the generated neckline narrative is
# suppressed so it can never contradict the canonical choice. A v1 spec, or a v2
# spec with no neckline preference, renders exactly as before.
_NECKLINE_CLAUSES = {
    "classic_crew": "a classic crew neckline sitting at the base of the neck",
    "curved_scoop": "a softly curved scoop neckline dipping just below the collarbone",
    "v_neck": "a moderate V-shaped neckline",
    "deep_v_neck": "a deep V-shaped neckline plunging below the collarbone",
    "boat_neck": "a wide boat neckline running straight across from shoulder to shoulder",
    "square_neck": "a clean square neckline cut across the chest",
    "sweetheart_neck": "a sweetheart neckline curved like the top of a heart",
    "high_neck": (
        "a fully closed high neckline covering the collarbone and upper chest, "
        "not an open, scooped or sweetheart neckline"
    ),
    "band_collar": "an upright band or mandarin collar standing at the neck",
}
# Short labels for the brief end-of-prompt reinforcement (same keys).
_NECKLINE_REINFORCE = {
    "classic_crew": "a crew neckline",
    "curved_scoop": "a scoop neckline",
    "v_neck": "a V neckline",
    "deep_v_neck": "a deep V neckline",
    "boat_neck": "a boat neckline",
    "square_neck": "a square neckline",
    "sweetheart_neck": "a sweetheart neckline",
    "high_neck": "a closed high neckline",
    "band_collar": "a band collar",
}


def _canonical_neckline(ss) -> str | None:
    """The canonical neckline clause for a v2 spec with a neckline preference,
    or ``None`` for a v1 spec / no preference. Uses ``getattr`` because a
    version-1 SourceSelections has no ``neckline_style`` attribute."""
    value = getattr(ss, "neckline_style", None)
    return _NECKLINE_CLAUSES.get(value) if value else None


def _canonical_neckline_reinforce(ss) -> str | None:
    """The short reinforcement label for a v2 spec's canonical neckline, or
    ``None`` for a v1 spec / no preference. Uses ``getattr`` because a
    version-1 SourceSelections has no ``neckline_style`` attribute."""
    value = getattr(ss, "neckline_style", None)
    return _NECKLINE_REINFORCE.get(value) if value else None


# The user's explicit head-covering coverage preference, and the dupatta styling
# that also means "worn over the head". Either signals that the hair must be
# covered.
_HEAD_COVER_PREF = "head_drape_preferred"
_HEAD_DRAPE_DUPATTA = "head_drape"


def _wants_head_covered(ss) -> bool:
    return (
        _HEAD_COVER_PREF in (ss.coverage_preferences or [])
        or ss.dupatta_style == _HEAD_DRAPE_DUPATTA
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
    """High-priority, garment-neutral coverage directive rendered immediately
    after the composition directive.

    Renders the canonical coverage selections as explicit VISUAL requirements so
    FLUX is far likelier to honour them than when they sit buried in mid-prompt
    prose. It is conditional: it names only selected requirements, never forces
    coverage a less-covered choice did not ask for, and includes the head-covering
    veil clause only when the user actually requested a covered head."""
    ss = spec.source_selections
    prefs = ss.coverage_preferences or []
    # The canonical neckline is rendered FIRST so it sits at the very front of
    # the high-priority coverage directive, beside the other coverage
    # requirements FLUX most often ignores.
    clauses = []
    neckline = _canonical_neckline(ss)
    if neckline:
        clauses.append(neckline)
    clauses += [clause for key, clause in _COVERAGE_CLAUSES.items() if key in prefs]
    if _wants_head_covered(ss):
        clauses.append(
            f"{_head_cover_reference(ss)} pulled up and over the head like a veil, "
            "completely covering the hair with no hair visible"
        )
    if not clauses:
        return []
    return [
        _mandatory(
            "Coverage and modesty requirements that must be clearly visible in the "
            f"render: {'; '.join(clauses)}"
        )
    ]


def _coverage_reinforcement(spec: DesignSpec) -> _Piece | None:
    """A brief positive restatement of the critical coverage requirements placed
    last, so FLUX re-reads them after the detailed garment prose. Deterministic,
    positive, conditional; never a negative prompt."""
    ss = spec.source_selections
    prefs = ss.coverage_preferences or []
    bits = []
    neckline = _canonical_neckline_reinforce(ss)
    if neckline:
        bits.append(neckline)
    bits += [label for key, label in _COVERAGE_REINFORCE.items() if key in prefs]
    if _wants_head_covered(ss):
        bits.append("the head covered with no hair visible")
    if not bits:
        return None
    return _mandatory("Coverage to keep clearly visible in the final image: " + ", ".join(bits))


def _coverage(spec: DesignSpec) -> list[_Piece]:
    ss = spec.source_selections
    cd = spec.coverage_and_drape
    pieces = []
    if ss.coverage_preferences:
        pieces.append(
            _mandatory(f"Coverage preferences: {_readable_list(ss.coverage_preferences)}")
        )
    pieces.append(_narrative(f"Sleeves: {_slot(cd.sleeves, _NARRATIVE_CAP)}"))
    # When a canonical neckline was chosen it is already rendered as a mandatory
    # visual requirement in the leading coverage directive; the model-authored
    # neckline narrative is suppressed here so it can never contradict it. A v1
    # spec (or a v2 spec with no neckline preference) still renders the narrative.
    if _canonical_neckline(ss) is None:
        pieces.append(_narrative(f"Neckline: {_slot(cd.neckline, _NARRATIVE_CAP)}"))
    pieces.append(_narrative(f"Back and midriff: {_slot(cd.back_and_midriff, _NARRATIVE_CAP)}"))
    pieces.append(_narrative(f"Head covering: {_slot(cd.head_covering, _NARRATIVE_CAP)}"))
    return pieces


def _dupatta_or_drape(spec: DesignSpec) -> list[_Piece]:
    ss = spec.source_selections
    cd = spec.coverage_and_drape
    selections = []
    if ss.dupatta_style:
        selections.append(f"dupatta style {_readable(ss.dupatta_style)}")
    if ss.saree_drape:
        selections.append(f"saree drape {_readable(ss.saree_drape)}")
    pieces = []
    if selections:
        pieces.append(_mandatory("Drape: " + ", ".join(selections)))
    pieces.append(_narrative(_slot(cd.dupatta_or_saree_drape, _NARRATIVE_CAP)))
    return pieces


def _cultural_and_styling(spec: DesignSpec) -> list[_Piece]:
    cc = spec.cultural_context
    pieces = []
    if cc.regional_direction is not None:
        pieces.append(
            _narrative(
                "Broad regional influence, offered as guidance rather than a "
                f"universal rule: {_slot(cc.regional_direction, _NARRATIVE_CAP)}"
            )
        )
    interpretation = _join_items(cc.interpretation_notes)
    if interpretation:
        pieces.append(_narrative(f"Interpretation: {interpretation}"))
    safeguards = _join_items(cc.safeguards)
    if safeguards:
        pieces.append(_narrative(f"Safeguards: {safeguards}"))
    # styling_notes are deliberately NOT rendered into the image prompt. They are
    # advisory beauty/styling prose (jewellery at the neckline, maang tikka / head
    # ornaments, hair) that pulls FLUX toward portraiture and can directly
    # contradict the coverage requirements — the live 4.0.0 saree showed exactly
    # that (open neckline + bare head with a choker and head ornament). They stay
    # in the persisted DesignSpec brief; only the image prompt omits them.
    return pieces


# Fixed conceptual ordering, documented as a priority hierarchy in ADR 0010 and
# snapshot-tested. The leading composition directive, the coverage directive, the
# trailing finishing directive and the closing coverage reinforcement are added
# directly in build_image_prompt; these garment-detail builders render between
# them in descending priority: garment type/silhouette, construction/drape,
# coverage/modesty detail, colour and fabric, embellishment, dupatta/veil
# treatment, then broad cultural context (styling notes are not rendered).
_SECTION_BUILDERS = (
    _garment_and_ceremony,
    _silhouette_and_components,
    _drape_and_proportions,
    _coverage,
    _colour,
    _fabrics,
    _embellishment,
    _dupatta_or_drape,
    _cultural_and_styling,
)


def _apply_narrative_budget(sections: list[list[_Piece]], budget: int, natural_total: int) -> None:
    """Shrink narrative pieces IN PLACE so their combined length ≤ ``budget``.

    Each section receives a share of the budget proportional to its natural
    narrative size; within a section, pieces are consumed in priority (reading)
    order — earlier pieces are kept, a piece that does not fit is truncated at a
    word boundary and the remaining lower-priority pieces are omitted. Mandatory
    pieces are never touched."""
    for section in sections:
        section_natural = sum(len(p.text) for p in section if not p.mandatory)
        if section_natural == 0:
            continue
        remaining = budget * section_natural // natural_total
        for piece in section:
            if piece.mandatory:
                continue
            if remaining <= 0:
                piece.text = ""
                continue
            if len(piece.text) <= remaining:
                remaining -= len(piece.text)
            else:
                piece.text = _truncate_at_word(piece.text, remaining)
                remaining = 0


def build_image_prompt(spec: DesignSpec) -> str:
    """Build the deterministic natural-language image prompt for ``spec``.

    Accepts a validated :class:`DesignSpec` (or any DesignSpec-compatible
    payload, revalidated defensively here). Runs the generated-content safety
    scan before interpolation, reserves the mandatory content and budgets the
    generated narrative into the remainder so the result never exceeds
    :data:`IMAGE_PROMPT_MAX_CHARS`, then runs a final safety scan. Raises ONLY
    :class:`ImagePromptBuildError` — never :class:`GeneratedContentRejected` —
    and never echoes the prompt or spec text."""
    if not isinstance(spec, DesignSpec):
        try:
            # Version-dispatched: a raw dict is validated as v1 or v2 by its
            # own schema_version (a DesignSpecV2 instance already passes the
            # isinstance check above, since it subclasses DesignSpec).
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
    # always the first content and can never be displaced or truncated. The
    # concrete coverage directive follows immediately (second), so the coverage
    # requirements FLUX most often ignores appear high in the prompt rather than
    # buried in mid-prompt prose (empty and skipped when nothing coverage-relevant
    # is selected).
    sections = [[_Piece(_COMPOSITION, mandatory=True)], _coverage_directive(spec)]
    sections += [list(builder(spec)) for builder in _SECTION_BUILDERS]
    # Unembellished designs use finishing wording that does not ask for embroidery
    # detail, so the fixed wording never contradicts a "none" choice.
    unembellished = spec.source_selections.embellishment_styles == [_NONE_EMBELLISHMENT]
    finishing = _FINISHING_UNEMBELLISHED if unembellished else _FINISHING
    sections.append([_Piece(finishing, mandatory=True)])
    # A brief coverage reinforcement is the LAST thing FLUX reads, so the critical
    # requirements are restated after the detailed garment prose (skipped when no
    # coverage-critical selection exists).
    reinforcement = _coverage_reinforcement(spec)
    if reinforcement is not None:
        sections.append([reinforcement])

    mandatory_len = sum(len(p.text) for section in sections for p in section if p.mandatory)
    natural_total = sum(len(p.text) for section in sections for p in section if not p.mandatory)
    budget = IMAGE_PROMPT_MAX_CHARS - mandatory_len - _SEPARATOR_RESERVE
    if budget < 0:
        # Unreachable for a schema-valid DesignSpec (mandatory content is bounded
        # well under the limit); a controlled error rather than an overrun.
        raise ImagePromptBuildError("mandatory content exceeds the maximum length")
    if natural_total > budget:
        _apply_narrative_budget(sections, budget, natural_total)

    rendered = []
    for section in sections:
        texts = [piece.text for piece in section if piece.text]
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
