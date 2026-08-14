"""Strict, versioned refinement request contract — one constrained edit per
request (Phase 14).

"One edit" is a property of a REQUEST, not of a design: since ADR 0029 a design
gets `MAX_REFINEMENTS` of them, chained. This module owns the shape of a single
request and knows nothing about how many a design may make.

A user may request exactly one constrained edit to an existing, validated
DesignSpec: one allowlisted ``change_type`` plus a short optional free-text
``note``. This module owns three things:

1. The strict Pydantic v2 :class:`RefinementRequest` contract and its
   normalisation/canonicalisation/hashing — the same pattern
   :mod:`sitara.generation.inspiration_context` established for a versioned,
   hashed, provider-adjacent snapshot.
2. The source-controlled mapping from each ``change_type`` to the exact
   DesignSpec paths that category may change (:data:`REFINEMENT_ALLOWED_PATHS`)
   and the fields no refinement may EVER change
   (:data:`REFINEMENT_IMMUTABLE_ROOTS`).
3. A deterministic, pure DesignSpec diff (:func:`diff_design_spec_paths`) and
   path-membership test (:func:`path_is_allowed`) that Part B's exact-diff
   validation is built from.

Deliberately absent: arbitrary client field paths, JSON Patch, wildcard paths
in the allowlist itself (each path is a concrete dotted/indexed string; a
whole-list root such as ``"fabrics_and_texture"`` matches every element of
that list, but that breadth is an explicit, reviewed, per-category choice —
never a client-supplied pattern), provider parameters, model names, seeds,
image URLs or storage keys.
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints
from pydantic import ValidationError as PydanticValidationError

from .input_safety import (
    UnsafeUserTextError,
    contains_markup,
    contains_phrase,
    scan_user_text,
    strip_format_characters,
)
from .inspiration_context import canonical_text
from .selection_semantics import (
    COLOUR_SELECTION_FIELDS,
    COVERAGE_SELECTION_FIELDS,
    DRAPE_SELECTION_FIELDS,
    EMBELLISHMENT_SELECTION_FIELDS,
    FABRIC_SELECTION_FIELDS,
    IMMUTABLE_SELECTION_FIELDS,
    NECKLINE_SELECTION_FIELDS,
    SILHOUETTE_SELECTION_FIELDS,
)

# Versions the persisted REQUEST SHAPE. Bump only with a documented migration
# strategy for existing persisted DesignVersion.refinement_request rows.
REFINEMENT_REQUEST_SCHEMA_VERSION = 1

REFINEMENT_NOTE_MAX_LENGTH = 300

_MODEL_CONFIG = ConfigDict(
    extra="forbid",
    str_strip_whitespace=True,
    validate_assignment=True,
)

# --- Allowlisted refinement categories -------------------------------------

COLOUR_STORY = "colour_story"
FABRIC_AND_TEXTURE = "fabric_and_texture"
EMBELLISHMENT = "embellishment"
SLEEVES_AND_COVERAGE = "sleeves_and_coverage"
NECKLINE = "neckline"
DUPATTA_OR_SAREE_DRAPE = "dupatta_or_saree_drape"
SILHOUETTE_DETAIL = "silhouette_detail"
STYLING_DETAILS = "styling_details"

# The categories a client may request TODAY.
REFINEMENT_CHANGE_TYPES = (
    COLOUR_STORY,
    FABRIC_AND_TEXTURE,
    EMBELLISHMENT,
    SLEEVES_AND_COVERAGE,
    NECKLINE,
    DUPATTA_OR_SAREE_DRAPE,
    SILHOUETTE_DETAIL,
)

# Retired by Phase 23 (ADR 0028). ``styling_details`` names no canonical
# selection, and every narrative path it could change — styling_notes, the
# cultural interpretation notes, the title, the summary, the alt text — is
# deliberately unrendered by prompt builder 8.0.0 for reasons that still hold
# ("advisory beauty/jewellery prose pulls the provider toward portraiture and
# can contradict the coverage requirements"). A control whose effect is
# invisible in the thing the user is looking at is worse than an absent one, so
# it is removed rather than left offering a promise it cannot keep.
#
# It stays NAMED here because a persisted DesignVersion.refinement_request row
# may carry it, and a historical row is audit data that is read, never rewritten
# (CLAUDE.md §14 applied to a different table). So the request MODEL below
# accepts it and the client-input boundary does not.
RETIRED_REFINEMENT_CHANGE_TYPES = (STYLING_DETAILS,)

# Every value that may appear in a PERSISTED refinement request, current or
# historical. Derived from the two tuples above (never a third hand-maintained
# list) so they can never drift apart.
PERSISTED_REFINEMENT_CHANGE_TYPES = REFINEMENT_CHANGE_TYPES + RETIRED_REFINEMENT_CHANGE_TYPES

_ChangeType = Literal[*PERSISTED_REFINEMENT_CHANGE_TYPES]

# The exact DesignSpec paths each category may change, as concrete dotted
# strings (never a wildcard pattern). A root with no dot (e.g.
# "fabrics_and_texture") matches that WHOLE field, including every list
# element and nested attribute; a dotted leaf (e.g. "coverage_and_drape.
# neckline") matches only that exact nested scalar/list, never its siblings.
# Refined after inspecting the current DesignSpec (generation/design_spec.py):
# "compatible garment texture/component descriptions" map onto the concrete
# fields that actually carry that narrative (fabrics_and_texture entries,
# garment_breakdown.garment_components) rather than a broader object.
REFINEMENT_ALLOWED_PATHS: dict[str, frozenset[str]] = {
    COLOUR_STORY: frozenset(
        {
            "title",
            "concept_summary",
            "colour_story",
            "fabrics_and_texture",
            "styling_notes",
            "image_alt_text",
        }
    ),
    FABRIC_AND_TEXTURE: frozenset(
        {
            "title",
            "concept_summary",
            "fabrics_and_texture",
            "colour_story",
            "styling_notes",
            "construction_caveats",
            "image_alt_text",
        }
    ),
    EMBELLISHMENT: frozenset(
        {
            "title",
            "concept_summary",
            "embellishment_plan",
            "fabrics_and_texture",
            "styling_notes",
            "construction_caveats",
            "image_alt_text",
        }
    ),
    SLEEVES_AND_COVERAGE: frozenset(
        {
            "title",
            "concept_summary",
            "coverage_and_drape.sleeves",
            "coverage_and_drape.back_and_midriff",
            "garment_breakdown.garment_components",
            "styling_notes",
            "construction_caveats",
            "image_alt_text",
        }
    ),
    NECKLINE: frozenset(
        {
            "title",
            "concept_summary",
            "coverage_and_drape.neckline",
            "garment_breakdown.garment_components",
            "embellishment_plan.placement",
            "construction_caveats",
            "image_alt_text",
        }
    ),
    DUPATTA_OR_SAREE_DRAPE: frozenset(
        {
            "title",
            "concept_summary",
            "coverage_and_drape.head_covering",
            "coverage_and_drape.dupatta_or_saree_drape",
            "garment_breakdown.drape_or_layering",
            "styling_notes",
            "construction_caveats",
            "image_alt_text",
        }
    ),
    SILHOUETTE_DETAIL: frozenset(
        {
            "title",
            "concept_summary",
            "garment_breakdown",
            "fabrics_and_texture",
            "construction_caveats",
            "image_alt_text",
        }
    ),
}

# Never changeable by ANY refinement category, regardless of the allowlist
# above — checked unconditionally by Part B's exact-diff validation.
#
# Phase 23 (ADR 0028) narrowed this from {"schema_version", "source_selections"}.
# Freezing the whole of ``source_selections`` was right on ADR 0015's terms — a
# spec's selections are the user's own answers echoed back, and a model must
# never quietly rewrite what someone said — but it answered the wrong question.
# A refinement IS the user changing one answer, deliberately, through a named
# category, on a screen that exists for that purpose; refusing to record the
# change discarded their answer rather than protecting it. What replaces the
# blanket freeze is narrower and stronger: only the category's OWN canonical
# field may move (:func:`refinement_allowed_paths`), the fields below may never
# move at all, and the new value must be a legal answer to the design's own
# pinned questionnaire (:mod:`sitara.generation.refinement_selections`).
REFINEMENT_IMMUTABLE_ROOTS = frozenset({"schema_version"})

# Canonical selections no category may change, checked explicitly rather than
# left to the allowlist's silence — a future allowlist entry must not be able to
# grant one of these by accident. Owned by selection_semantics, which is where
# every other statement about what a canonical selection MEANS lives.
REFINEMENT_IMMUTABLE_SELECTION_FIELDS = IMMUTABLE_SELECTION_FIELDS

# The DesignSpec root holding the canonical selections.
_SELECTIONS_ROOT = "source_selections"

# Which canonical selection group each category owns. The category-to-group
# decision is this module's; what each group contains on a given DesignSpec
# version is selection_semantics'. A category absent here owns nothing.
_CANONICAL_FIELDS_BY_CATEGORY: dict[str, dict[int, tuple[str, ...]]] = {
    COLOUR_STORY: COLOUR_SELECTION_FIELDS,
    FABRIC_AND_TEXTURE: FABRIC_SELECTION_FIELDS,
    EMBELLISHMENT: EMBELLISHMENT_SELECTION_FIELDS,
    SLEEVES_AND_COVERAGE: COVERAGE_SELECTION_FIELDS,
    NECKLINE: NECKLINE_SELECTION_FIELDS,
    DUPATTA_OR_SAREE_DRAPE: DRAPE_SELECTION_FIELDS,
    SILHOUETTE_DETAIL: SILHOUETTE_SELECTION_FIELDS,
}


def canonical_refinement_fields(change_type: str, schema_version: object) -> tuple[str, ...]:
    """The ``source_selections`` fields ``change_type`` may change on a spec of
    ``schema_version``.

    Empty means the category has nothing canonical to change on this version —
    an unknown or retired category, or the real case this dispatch exists for: a
    version-1 spec carries no ``neckline_style`` attribute at all. Total over
    arbitrary input, so an unsupported persisted version yields ``()`` rather
    than a ``KeyError`` or an ``AttributeError``.

    The version must be a strict ``int``, exactly as
    :func:`~sitara.generation.design_spec.design_spec_model_for_version`
    requires: ``bool`` is an ``int`` subclass and ``1.0 == 1``, so a stored
    ``true`` or ``1.0`` would otherwise silently resolve to version 1."""
    by_version = _CANONICAL_FIELDS_BY_CATEGORY.get(change_type)
    if by_version is None or isinstance(schema_version, bool) or type(schema_version) is not int:
        return ()
    return tuple(by_version.get(schema_version, ()))


def refinement_allowed_paths(change_type: str, schema_version: object) -> frozenset[str]:
    """Every DesignSpec path ``change_type`` may change on a spec of
    ``schema_version``: its narrative allowlist plus this version's canonical
    selection paths.

    The canonical paths are ADDED to the narrative ones, never substituted — the
    refined narrative should still agree with the refined selection."""
    narrative = REFINEMENT_ALLOWED_PATHS.get(change_type, frozenset())
    canonical = {
        f"{_SELECTIONS_ROOT}.{field}"
        for field in canonical_refinement_fields(change_type, schema_version)
    }
    return narrative | canonical


def changed_selection_field(path: str) -> str | None:
    """The ``source_selections`` field a changed path names, or ``None`` when the
    path is not a selection path at all.

    ``source_selections.fabrics[0].x`` and ``source_selections.fabrics`` both
    name ``fabrics``. A bare ``source_selections`` — which a well-formed diff of
    two valid specs cannot produce, since both carry every declared key — names
    the empty string, so the caller treats it as a change it cannot attribute
    and refuses it."""
    if path == _SELECTIONS_ROOT:
        return ""
    prefix = _SELECTIONS_ROOT + "."
    if not path.startswith(prefix):
        return None
    return path[len(prefix) :].split(".", 1)[0].split("[", 1)[0]


def references_suppressed_for_request(refinement_request: object) -> bool:
    """True when a refinement of this category must be sent NO reference images.

    ``colour_story``, and only ``colour_story`` (ADR 0028, §7). A reference
    photograph is a far stronger colour signal to the image model than a text
    clause, so "make it green" against a red reference keeps losing to the
    reference — the reported defect this phase exists to fix. Every other
    category keeps its references, because for them the customer's own
    photographs are the anchor that keeps a refinement recognisable as the same
    concept.

    Takes the persisted request MAPPING rather than an attempt row, so the fact
    lives here beside the other per-category tables without this module gaining
    an ORM dependency. Total over arbitrary input: anything that is not a
    refinement request at all — an initial generation's absent one included —
    answers ``False``, so this can only ever REMOVE a reference URL and never
    add one."""
    if not isinstance(refinement_request, Mapping):
        return False
    return refinement_request.get("change_type") == COLOUR_STORY


class RefinementRequestInvalid(Exception):
    """The client-submitted refinement request is malformed or out of
    contract. Generic, safe message; never echoes the payload or note."""

    code = "refinement_invalid"

    def __init__(self):
        super().__init__("the refinement request is invalid")


class RefinementNoteUnsafe(Exception):
    """The optional note failed the pre-provider safety scan.

    Reported under the same generic client-facing code as any other invalid
    refinement request — the note is untrusted user input rejected before any
    provider call, never a distinct disclosed category, and never echoed."""

    code = "refinement_invalid"

    def __init__(self):
        super().__init__("refinement note unavailable")


class RefinementRequest(BaseModel):
    """One validated, canonical refinement request."""

    model_config = _MODEL_CONFIG

    schema_version: Literal[1]
    change_type: _ChangeType
    note: Annotated[str, StringConstraints(max_length=REFINEMENT_NOTE_MAX_LENGTH)] = ""


# --- Note safety (pre-provider, generic, never echoes the text) ------------
#
# HTML/Markdown detection reuses sitara.content_safety.contains_markup (the
# single source of truth for that denylist, also used by scan_generated_text)
# rather than a second, independently-maintained pattern list.

_MEASUREMENT_UNIT_PATTERN = re.compile(
    r"\b\d+(\.\d+)?\s*(cm|centimet(?:er|re)s?|mm|millimet(?:er|re)s?|inch(?:es)?"
    r"|ft|feet|foot|yards?|meters?|metres?)\b",
    re.IGNORECASE,
)
# Includes the Unicode prime/double-prime/triple-prime marks (U+2032/2033/2034)
# commonly used for feet/inches alongside the ASCII quote characters — NFKC
# normalisation does not fold these to ASCII, so both forms are matched.
_MEASUREMENT_MARK_PATTERN = re.compile(r"\d+(\.\d+)?\s*[\"'′″‴]")

_SEWING_OR_PATTERN_PHRASES = (
    "sewing pattern",
    "sewing patterns",
    "cutting pattern",
    "cutting patterns",
    "sewing instructions",
    "pattern making",
    "pattern-making",
    "construction instructions",
    "seam allowance",
    "stitch line",
    "stitching line",
    "dart placement",
    "pattern piece",
    "pattern pieces",
    "cutting line",
    "hem allowance",
    "grainline",
    "bodice block",
    "sloper",
    "toile",
    "muslin mockup",
    "notch marking",
)


def _assert_note_safe(note: str) -> None:
    """Reject designer/brand references, imitation language, URLs, prompt/
    system leakage (via :func:`scan_user_text`), HTML/Markdown (via
    :func:`contains_markup`), disallowed control characters, measurements
    and sewing/pattern-making instructions. Raises
    :class:`RefinementNoteUnsafe`; never logs or echoes ``note``.

    Every raw-regex check below matches against ``stripped`` — ``note`` with
    Unicode format characters removed (:func:`strip_format_characters`) — so
    an invisible character inserted between two characters a pattern expects
    adjacent (e.g. a digit and its unit) cannot defeat the check. The
    phrase-based checks (:func:`contains_phrase`, :func:`scan_user_text`)
    already apply the same stripping internally via their tokeniser."""
    for char in note:
        if char != "\n" and unicodedata.category(char) == "Cc":
            raise RefinementNoteUnsafe()
    stripped = strip_format_characters(note)
    if contains_markup(stripped):
        raise RefinementNoteUnsafe()
    if _MEASUREMENT_UNIT_PATTERN.search(stripped) or _MEASUREMENT_MARK_PATTERN.search(stripped):
        raise RefinementNoteUnsafe()
    if any(contains_phrase(note, phrase) for phrase in _SEWING_OR_PATTERN_PHRASES):
        raise RefinementNoteUnsafe()
    try:
        scan_user_text(note)
    except UnsafeUserTextError:
        raise RefinementNoteUnsafe() from None


def normalise_refinement_request(payload: object) -> RefinementRequest:
    """Validate and normalise one client-submitted refinement request.

    ``payload`` must be a JSON object with only ``schema_version``,
    ``change_type`` and an optional ``note``. The note is Unicode
    NFKC-normalised, CRLF/CR folded to LF, internal whitespace collapsed and
    outer whitespace stripped before length and safety checks. Raises
    :class:`RefinementRequestInvalid` for any malformed/out-of-contract
    payload (never a raw Pydantic error) and :class:`RefinementNoteUnsafe`
    for an unsafe note — both generic, never echoing client input.

    This is the CLIENT-INPUT boundary, so a retired category is refused here
    even though :class:`RefinementRequest` still accepts one: a new request
    naming ``styling_details`` is a controlled invalid request, while the
    historical row that named it stays readable."""
    if not isinstance(payload, dict):
        raise RefinementRequestInvalid()
    allowed_keys = {"schema_version", "change_type", "note"}
    if set(payload) - allowed_keys:
        raise RefinementRequestInvalid()
    if payload.get("change_type") in RETIRED_REFINEMENT_CHANGE_TYPES:
        raise RefinementRequestInvalid()

    note_raw = payload.get("note", "")
    if not isinstance(note_raw, str):
        raise RefinementRequestInvalid()
    note = canonical_text(note_raw)
    if len(note) > REFINEMENT_NOTE_MAX_LENGTH:
        raise RefinementRequestInvalid()
    if note:
        _assert_note_safe(note)

    try:
        return RefinementRequest.model_validate(
            {
                "schema_version": payload.get("schema_version"),
                "change_type": payload.get("change_type"),
                "note": note,
            }
        )
    except PydanticValidationError:
        raise RefinementRequestInvalid() from None


def refinement_request_canonical_json(request: RefinementRequest) -> str:
    """One deterministic canonical JSON representation: UTF-8, sorted keys,
    fixed compact separators, no timestamps, no user/session identity, no
    machine-dependent data."""
    return json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def refinement_request_sha256(request: RefinementRequest) -> str:
    """SHA-256 hex digest of the canonical JSON representation."""
    return hashlib.sha256(refinement_request_canonical_json(request).encode("utf-8")).hexdigest()


# --- DesignSpec edit surface: deterministic diff + allowlist membership ----


def diff_design_spec_paths(original: dict, refined: dict) -> frozenset[str]:
    """The deterministic set of changed DesignSpec paths between two plain
    dicts (typically ``DesignSpec.model_dump(mode="json")`` before/after).

    Dict fields use dotted notation (``coverage_and_drape.neckline``); list
    fields use bracketed indices for an element-level change
    (``fabrics_and_texture[0].finish_and_movement``), or the bare list root
    when the list's LENGTH differs (adding/removing an entry is reported at
    the list root, never a per-index guess). Equal values contribute no path.
    """
    return frozenset(_diff_paths(original, refined, ""))


def _diff_paths(old: object, new: object, prefix: str) -> set[str]:
    changed: set[str] = set()
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            child_prefix = f"{prefix}.{key}" if prefix else key
            if key not in old or key not in new:
                changed.add(child_prefix)
                continue
            changed |= _diff_paths(old[key], new[key], child_prefix)
    elif isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            changed.add(prefix)
        else:
            for index, (old_item, new_item) in enumerate(zip(old, new, strict=True)):
                changed |= _diff_paths(old_item, new_item, f"{prefix}[{index}]")
    else:
        if old != new:
            changed.add(prefix)
    return changed


def path_is_allowed(path: str, allowed_roots: frozenset[str]) -> bool:
    """True when ``path`` (as produced by :func:`diff_design_spec_paths`) is
    covered by one of ``allowed_roots`` — an exact match, a list-index match
    under a whole-list root (``fabrics_and_texture`` covers
    ``fabrics_and_texture[0]...``), or a nested match under a dict root."""
    for root in allowed_roots:
        if path == root or path.startswith(root + "[") or path.startswith(root + "."):
            return True
    return False
