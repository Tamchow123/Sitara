"""Version-independent projections of a DesignSpec's canonical selections.

DesignSpec versions 1 and 2 record colour as one ordered ``colour_palette`` and
coverage as one ``coverage_preferences`` multi-select. Version 3 (questionnaire
v4) records colour per GARMENT ROLE and coverage per BODY AREA. Three consumers
— the deterministic image-prompt builder, the deterministic demo DesignSpec
engine and the demo asset selector — need the same handful of questions
answered for every version: which colours were chosen, in what order, and must
the head or the midriff be covered.

This module is that single, explicit adapter. It is a short list of named
accessors over KNOWN versions, never a generic schema-mapping framework (ADR
0009), and it never invents a selection the user did not make: an unanswered
optional question stays unanswered here too.

Every accessor is TOTAL over both a validated ``SourceSelections*`` model
instance and the equivalent plain mapping (the demo engine builds its spec from
the context's ``source_selections`` dict before any model exists), and is pure —
no database, no environment, no randomness.
"""

from collections.abc import Mapping

from .design_spec import COLOUR_MATCH_FABRIC

# The colour questions, in the fixed order they are rendered and echoed. The
# order is a contract: it is the order the prompt builder names the roles and
# the order :func:`ordered_colour_values` deduplicates in.
COLOUR_ROLE_FIELDS: tuple[str, ...] = ("fabric_colour", "embroidery_colour", "dupatta_colour")

# Where each colour question's answer belongs on the garment, in plain words.
# Shared by every consumer that names a role so the image prompt and the demo
# brief can never describe the same selection differently.
COLOUR_ROLE_LABELS: dict[str, str] = {
    "fabric_colour": "the main fabric",
    "embroidery_colour": "the embroidery and surface work",
    "dupatta_colour": "the dupatta",
}

# The coverage questions, in the fixed head-to-hem order they are rendered in.
COVERAGE_AREA_FIELDS: tuple[str, ...] = ("sleeves", "back_coverage", "midriff", "head_covering")

# The version-3 head-covering values that mean the hair must not be visible.
# ``uncovered`` deliberately maps to nothing — it is a real answer, not a
# request for coverage.
COVERED_HEAD_VALUES = frozenset({"dupatta_over_head", "veil_style", "hijab"})

# The version-3 midriff value that means no bare skin at the waist.
COVERED_MIDRIFF_VALUE = "covered_midriff"

# The version-1/2 coverage values with the same meanings.
_LEGACY_HEAD_COVER_VALUE = "head_drape_preferred"
_LEGACY_FULL_MIDRIFF_VALUE = "full_midriff"


def _field(selections, name: str):
    """One field of a SourceSelections model instance or an equivalent mapping."""
    if isinstance(selections, Mapping):
        return selections.get(name)
    return getattr(selections, name, None)


def _has_field(selections, name: str) -> bool:
    if isinstance(selections, Mapping):
        return name in selections
    return hasattr(selections, name)


def is_per_role_colour(selections) -> bool:
    """True when these selections use the version-3 per-role colour contract.

    Decided by the presence of the ``fabric_colour`` field, not by a version
    number, so it is equally correct for a model instance and for the context's
    plain mapping (which carries no ``schema_version``)."""
    return _has_field(selections, "fabric_colour")


def is_per_area_coverage(selections) -> bool:
    """True when these selections use the version-3 per-body-area coverage
    contract (see :func:`is_per_role_colour` for why this is field-based)."""
    return _has_field(selections, "sleeves")


def colour_role_values(selections) -> tuple[tuple[str, str], ...]:
    """The answered ``(role_field, value)`` colour pairs, in role order.

    Empty for a version-1/2 spec, which has no per-role colours. Unanswered
    roles are omitted; ``match_fabric`` is included because it is a real answer
    the prompt builder must render (as a relationship, not a colour)."""
    if not is_per_role_colour(selections):
        return ()
    pairs = []
    for field in COLOUR_ROLE_FIELDS:
        value = _field(selections, field)
        if isinstance(value, str) and value:
            pairs.append((field, value))
    return tuple(pairs)


def ordered_colour_values(selections) -> tuple[str, ...]:
    """Every chosen colour, in order, deduplicated, most important first.

    Version 1/2: ``colour_palette`` verbatim. Version 3: the answered role
    colours in role order. ``match_fabric`` is excluded — it names a
    relationship to another role's colour, not a colour — and a role answered
    with a bride-supplied hex contributes that hex."""
    if is_per_role_colour(selections):
        values = [value for _field_name, value in colour_role_values(selections)]
    else:
        palette = _field(selections, "colour_palette") or []
        values = [value for value in palette if isinstance(value, str) and value]
    ordered: list[str] = []
    for value in values:
        if value != COLOUR_MATCH_FABRIC and value not in ordered:
            ordered.append(value)
    return tuple(ordered)


def custom_colour_values(selections) -> tuple[str, ...]:
    """The bride's own saved hex palette (version 3 only; empty otherwise).

    This is the palette a colour question may be answered FROM — it is not
    itself a selection, so consumers must not render it as one."""
    values = _field(selections, "custom_colours") or []
    return tuple(value for value in values if isinstance(value, str) and value)


def coverage_area_values(selections) -> tuple[tuple[str, str], ...]:
    """The answered ``(area_field, value)`` coverage pairs, in body order.

    Empty for a version-1/2 spec. Unanswered areas are omitted, so "no
    preference" never becomes an instruction."""
    if not is_per_area_coverage(selections):
        return ()
    pairs = []
    for field in COVERAGE_AREA_FIELDS:
        value = _field(selections, field)
        if isinstance(value, str) and value:
            pairs.append((field, value))
    return tuple(pairs)


def legacy_coverage_values(selections) -> tuple[str, ...]:
    """The version-1/2 ``coverage_preferences`` list (empty for version 3)."""
    if is_per_area_coverage(selections):
        return ()
    values = _field(selections, "coverage_preferences") or []
    return tuple(value for value in values if isinstance(value, str) and value)


def head_covering_answer(selections) -> str | None:
    """The version-3 dedicated head-covering answer, or ``None``.

    ``None`` means either a version-1/2 spec (which has no such question) or a
    version-3 spec where the user expressed no preference — in both cases the
    caller falls back to whatever it inferred before."""
    if not is_per_area_coverage(selections):
        return None
    value = _field(selections, "head_covering")
    return value if isinstance(value, str) and value else None


def explicit_head_covering_decision(selections) -> bool | None:
    """The user's own head-covering decision, or ``None`` if they made none.

    Tri-state on purpose, because the two contracts can express different
    things:

    - Version 3 asks the question directly, so its answer is AUTHORITATIVE in
      **both** directions — an explicit ``uncovered`` is a decision, not an
      absence, and must beat any inference drawn from the dupatta styling
      (exactly as ``neckline_style`` beats the retired ``high_neckline``).
    - Version 1/2's multi-select can only ever express the positive case:
      ``head_drape_preferred`` means covered, and its absence means nothing was
      asked for — ``None``, never ``False``.

    ``None`` leaves the caller's own dupatta-styling inference in charge, which
    is what keeps every version-1/2 design rendering and selecting exactly as
    before. Deliberately NOT folded in here: the two callers disagree about
    which dupatta styles imply a covered head (the demo selector also counts a
    double dupatta), so that stays each caller's decision."""
    answer = head_covering_answer(selections)
    if answer is not None:
        return answer in COVERED_HEAD_VALUES
    if _LEGACY_HEAD_COVER_VALUE in legacy_coverage_values(selections):
        return True
    return None


def covered_midriff_requested(selections) -> bool:
    """True when the user explicitly asked for a covered midriff (both
    contracts). A semi-sheer midriff is deliberately NOT "covered" — it is a
    distinct, less-covered choice."""
    if is_per_area_coverage(selections):
        return _field(selections, "midriff") == COVERED_MIDRIFF_VALUE
    return _LEGACY_FULL_MIDRIFF_VALUE in legacy_coverage_values(selections)
