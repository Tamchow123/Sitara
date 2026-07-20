"""Generated-output and free-text safety scanning (Phase 8).

A dependency-free leaf module at the package root (not inside ``generation``)
so any app may reuse it without an app-to-app import reversal — the
catalogue app's approval-time defence (Phase 13) is one such caller.
``generation.input_safety`` re-exports this module's public API unchanged for
every existing generation-internal import.

Two jobs:

1. Scan every string in a generated :class:`DesignSpec` and reject imitation
   requests, named-designer/brand references, URLs, prompt/system leakage,
   control characters and sewing-pattern / guaranteed-constructibility claims.
2. Scan a user free-text answer for the designer/brand denylist and obvious
   prompt-override phrasing *before* any provider call (used in Part B).

Matching is robust to trivial evasion: text is Unicode NFKC-normalised,
case-folded, and reduced to whitespace-separated tokens with punctuation
stripped, then phrases are matched on token boundaries so casing or
punctuation changes cannot bypass the denylist.

**The denylist is a safety mechanism, not a cultural taxonomy.** It is
deliberately conservative, non-exhaustive and updateable; it exists only to
block imitation requests and generated designer/brand references, and treats
no name as culturally definitive. Rejections carry a generic category and
NEVER echo the offending text in the exception or in logs.
"""

import re
import unicodedata
from enum import Enum

# ---------------------------------------------------------------------------
# Denylist — conservative, non-exhaustive, updateable. A representative set of
# well-known South Asian (Indian / Pakistani / Bangladeshi) bridalwear
# designers and fashion houses, used ONLY to prevent imitation requests and
# generated brand references. Names are normalised the same way as scanned
# text, so spacing/casing/punctuation variants all match.
# ---------------------------------------------------------------------------
_DESIGNER_BRAND_NAMES = (
    # Indian
    "sabyasachi",
    "sabyasachi mukherjee",
    "manish malhotra",
    "anita dongre",
    "tarun tahiliani",
    "ritu kumar",
    "abu jani sandeep khosla",
    "falguni shane peacock",
    "rohit bal",
    "anamika khanna",
    "shantnu nikhil",
    # Pakistani
    "hassan sheheryar yasin",
    "faraz manan",
    "bunto kazmi",
    "nomi ansari",
    "maria b",
    "sana safinaz",
    "republic by omar farooq",
    # Bangladeshi. Deliberately only distinctive multi-token or uncommon names
    # — common-word brands (e.g. an English dictionary word) are omitted to
    # avoid false positives in ordinary bridalwear prose.
    "bibi russell",
    "aarong",
)

# Imitation phrasing (block regardless of the following name).
_IMITATION_PHRASES = (
    "in the style of",
    "in the manner of",
    "styled after",
    "a la",
    "inspired by the work of",
    "designer inspired",
    "knockoff of",
    "dupe of",
    "replica of",
)

# Prompt / system-instruction leakage (generated output) and prompt-override
# phrasing (user input) — both drawn from this set.
_PROMPT_LEAKAGE_PHRASES = (
    "system prompt",
    "system instruction",
    "you are claude",
    "you are an ai",
    "as an ai language model",
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard the system",
    "disregard previous",
    "override the system",
    "spec template version",
    "untrusted preference data",
    "begin untrusted",
    "end untrusted",
    "assistant:",
    "human:",
)

# Sewing-pattern and guaranteed-constructibility CLAIM phrases. These are only
# rejected when ASSERTED (i.e. NOT negated) — the required construction
# caveats legitimately say "not a sewing pattern" / "does not guarantee
# constructibility".
_SEWING_PATTERN_CLAIMS = ("sewing pattern", "sewing patterns", "cutting pattern")
_CONSTRUCTIBILITY_CLAIMS = (
    "guaranteed to construct",
    "guaranteed constructible",
    "guarantees constructibility",
    "guarantee constructibility",
    "can be constructed exactly",
    "constructed exactly as shown",
    "guaranteed to be made",
)
_NEGATIONS = frozenset(
    {
        "not",
        "no",
        "never",
        "without",
        "isnt",
        "arent",
        "doesnt",
        "dont",
        "cannot",
        "cant",
        "wont",
        "rather",
        "instead",
        "neither",
        "nor",
        "non",
    }
)

_URL_PATTERN = re.compile(
    r"(https?://|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|co|in|pk|bd|uk|ai)\b)",
    re.IGNORECASE,
)

# Raw HTML tags and Markdown formatting. DesignSpec narrative is plain prose;
# markup is model-authored formatting we REJECT (letting DesignSpec generation
# use its existing single retry) rather than silently strip and change meaning.
# Deliberately conservative: a bare ``<``/``>`` in prose (e.g. "a < b"), a single
# hyphen/underscore, and ordinary parenthetical text are all accepted — only
# recognisable tags and formatting syntax match.
_MARKUP_PATTERNS = (
    re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*[^<>]*>"),  # <b> </b> <script> <img ...>
    re.compile(r"\*\*"),  # **bold**
    re.compile(r"__+"),  # __bold__ (two or more underscores; single is allowed)
    re.compile(r"~~"),  # ~~strikethrough~~
    re.compile(r"\[[^\]]*\]\([^)]*\)"),  # [label](url) markdown link
    re.compile(r"(?m)^\s{0,3}#{1,6}(?:\s|$)"),  # # heading … ###### heading
    re.compile(r"`"),  # inline / fenced code backticks
)

# Clause boundaries used to keep negation scope tight (see ``_asserts_claim``).
# Punctuation that separates clauses within a sentence...
_CLAUSE_PUNCTUATION = re.compile(r"[,;:]")
# ...and contrastive/additive conjunctions that also start a new clause. Only
# conjunctions that do NOT themselves carry negation are included: "or"/"nor"
# are deliberately excluded because they propagate a preceding negation to the
# following clause ("not a sewing pattern nor a cutting pattern").
_CLAUSE_CONJUNCTIONS = frozenset(
    {
        "and",
        "but",
        "yet",
        "however",
        "though",
        "although",
        "whereas",
        "while",
        "nevertheless",
        "nonetheless",
    }
)


class RejectionCategory(str, Enum):
    DESIGNER_OR_BRAND = "designer_or_brand_reference"
    IMITATION_PHRASE = "imitation_phrase"
    URL = "url"
    CONTROL_CHARACTER = "control_character"
    PROMPT_LEAKAGE = "prompt_leakage"
    SEWING_PATTERN_CLAIM = "sewing_pattern_claim"
    CONSTRUCTIBILITY_CLAIM = "constructibility_claim"
    MARKUP = "markup_or_formatting"
    # An inspiration's audit-only title/attribution (Phase 13) leaked into
    # generated output — those fields are never sent to the provider, so
    # their appearance means the model guessed or fabricated them.
    INSPIRATION_LEAKAGE = "inspiration_leakage"


class GeneratedContentRejected(Exception):
    """Generated content failed a safety check.

    Carries only a generic :class:`RejectionCategory` — never the offending
    text — so it is always safe to surface and log."""

    def __init__(self, category: RejectionCategory):
        self.category = category
        super().__init__(f"generated content rejected: {category.value}")


class UnsafeUserTextError(Exception):
    """A user free-text answer failed a safety check before any provider call.

    Carries only a generic category, never the offending text."""

    def __init__(self, category: RejectionCategory):
        self.category = category
        super().__init__(f"user text rejected: {category.value}")


def strip_format_characters(text: str) -> str:
    """NFKC-normalise, then remove Unicode "format" characters (category
    ``Cf`` — zero-width space/joiner/non-joiner and similar invisible marks)
    outright.

    Every check in this module that matches a denylisted phrase or pattern
    against raw (non-tokenised) text routes through this first — a
    denylisted word or pattern broken up with an invisible character (e.g.
    ``"sew\\u200bing pattern"`` or ``"exam\\u200bple.com"``) must still match
    as if it were contiguous, never silently split into pieces that no
    longer match."""
    normalised = unicodedata.normalize("NFKC", text)
    return "".join(char for char in normalised if unicodedata.category(char) != "Cf")


def _tokens(text: str) -> list[str]:
    """Case-fold + punctuation→space + collapse (after
    :func:`strip_format_characters`), then split to tokens.

    Underscores are treated as separators too (``[\\W_]+``): ``\\w`` keeps
    underscore, so without this ``Manish_Malhotra`` would collapse to one
    token and slip past a multi-token denylist entry."""
    normalised = strip_format_characters(text).casefold()
    # Replace any run of non-word (Unicode-aware) characters OR underscores
    # with a single space, so punctuation/underscore/spacing variants all
    # collapse to the same tokens.
    normalised = re.sub(r"[\W_]+", " ", normalised, flags=re.UNICODE)
    return normalised.split()


def contains_markup(text: str) -> bool:
    """True if ``text`` contains a raw HTML tag or Markdown formatting
    syntax, after stripping invisible format characters that could
    otherwise break a pattern's required character adjacency. Public so
    other modules matching raw refinement/free-text input (rather than the
    tokenised phrase checks) reuse this single denylist instead of
    duplicating it."""
    stripped = strip_format_characters(text)
    return any(pattern.search(stripped) for pattern in _MARKUP_PATTERNS)


def contains_url(text: str) -> bool:
    """True if ``text`` contains a URL, after stripping invisible format
    characters. Public for the same reason as :func:`contains_markup`."""
    return bool(_URL_PATTERN.search(strip_format_characters(text)))


def _phrase_tokens(phrase: str) -> list[str]:
    return _tokens(phrase)


def _phrase_starts(haystack: list[str], needle: list[str]) -> list[int]:
    """Every start index at which ``needle`` occurs as a contiguous token
    subsequence of ``haystack`` (empty when it does not occur)."""
    if not needle:
        return []
    limit = len(haystack) - len(needle)
    return [
        start for start in range(0, limit + 1) if haystack[start : start + len(needle)] == needle
    ]


def _contains_phrase(haystack: list[str], needle: list[str]) -> bool:
    """True if ``needle`` occurs as a contiguous token subsequence."""
    return bool(_phrase_starts(haystack, needle))


def contains_phrase(text: str, phrase: str) -> bool:
    """Public, normalisation-aware phrase test (token-boundary matching).

    Used by the DesignSpec semantic validators to check caveat phrasing
    flexibly, sharing this module's NFKC/casefold/punctuation handling."""
    return _contains_phrase(_tokens(text), _phrase_tokens(phrase))


def _clauses(text: str) -> list[list[str]]:
    """Split ``text`` into per-clause token lists.

    Sentences are split on terminal punctuation, then each sentence is split on
    clause punctuation (``, ; :``) and clause conjunctions (``and``, ``but`` …).
    A negation only governs a claim inside the SAME clause, so an unrelated
    earlier negation cannot excuse a later, separately-asserted claim
    ("No embellishment is used, and this is a sewing pattern.")."""
    clauses: list[list[str]] = []
    for sentence in re.split(r"[.!?\n]+", text):
        for part in _CLAUSE_PUNCTUATION.split(sentence):
            current: list[str] = []
            for token in _tokens(part):
                if token in _CLAUSE_CONJUNCTIONS:
                    if current:
                        clauses.append(current)
                    current = []
                else:
                    current.append(token)
            if current:
                clauses.append(current)
    return clauses


def _asserts_claim(text: str, claim_phrases: tuple[str, ...]) -> bool:
    """True if a claim phrase is ASSERTED rather than negated.

    Negation is SCOPE-AWARE and CLAUSE-AWARE: a claim phrase is treated as
    negated only when a negation token directly governs it — i.e. precedes it
    within the SAME clause. A negation cannot reach across a clause boundary
    (punctuation or a conjunction), so:

    - "This is not a sewing pattern."                        → allowed (the
      negation precedes the claim in one clause);
    - "This is a sewing pattern, not merely a mood board."   → rejected (the
      negation is in a different clause and follows the claim);
    - "No embellishment is used, and this is a sewing pattern." → rejected (the
      negation governs only its own clause, not the later claim);
    - "This is not a mood board but is a sewing pattern."    → rejected (the
      negation cannot cross the "but" clause boundary).

    Within a clause the negation may sit any distance before the claim, so a
    legitimate caveat keeps its distance, e.g. "does not guarantee that the
    garment can be constructed exactly as shown"."""
    for tokens in _clauses(text):
        negation_positions = [i for i, token in enumerate(tokens) if token in _NEGATIONS]
        first_negation = negation_positions[0] if negation_positions else None
        for phrase in claim_phrases:
            for start in _phrase_starts(tokens, _phrase_tokens(phrase)):
                # Asserted unless a negation appears before the phrase start
                # inside this same clause.
                if first_negation is None or start < first_negation:
                    return True
    return False


def scan_generated_text(text: str) -> None:
    """Scan one generated string; raise :class:`GeneratedContentRejected`."""
    # Control characters other than a normal line break (\n). \r is normalised
    # away upstream; anything else in category Cc is rejected.
    for char in text:
        if char != "\n" and unicodedata.category(char) == "Cc":
            raise GeneratedContentRejected(RejectionCategory.CONTROL_CHARACTER)

    if contains_url(text):
        raise GeneratedContentRejected(RejectionCategory.URL)

    tokens = _tokens(text)
    # Designer/imitation/leakage checks run BEFORE the markup check so a name
    # written with markdown emphasis (e.g. ``Manish__Malhotra``) is still
    # reported as a designer reference, not generic markup.
    if any(_contains_phrase(tokens, _phrase_tokens(name)) for name in _DESIGNER_BRAND_NAMES):
        raise GeneratedContentRejected(RejectionCategory.DESIGNER_OR_BRAND)
    if any(_contains_phrase(tokens, _phrase_tokens(p)) for p in _IMITATION_PHRASES):
        raise GeneratedContentRejected(RejectionCategory.IMITATION_PHRASE)
    if any(_contains_phrase(tokens, _phrase_tokens(p)) for p in _PROMPT_LEAKAGE_PHRASES):
        raise GeneratedContentRejected(RejectionCategory.PROMPT_LEAKAGE)
    if contains_markup(text):
        raise GeneratedContentRejected(RejectionCategory.MARKUP)
    if _asserts_claim(text, _SEWING_PATTERN_CLAIMS):
        raise GeneratedContentRejected(RejectionCategory.SEWING_PATTERN_CLAIM)
    if _asserts_claim(text, _CONSTRUCTIBILITY_CLAIMS):
        raise GeneratedContentRejected(RejectionCategory.CONSTRUCTIBILITY_CLAIM)


def iter_strings(value: object):
    """Every string nested inside a dict/list/tuple structure, depth-first.

    Public (unlike this module's other internals) so callers needing the
    same flattening — e.g. the Phase 13 inspiration-leakage check — reuse it
    rather than re-implementing it."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from iter_strings(item)


def scan_design_spec(spec) -> None:
    """Recursively scan every string in a validated DesignSpec.

    ``spec`` is a :class:`~sitara.generation.design_spec.DesignSpec`; its
    ``model_dump()`` is walked so machine values and all narrative strings are
    checked."""
    for text in iter_strings(spec.model_dump(mode="python")):
        scan_generated_text(text)


def scan_user_text(text: str) -> None:
    """Scan a user free-text answer BEFORE any provider call.

    Rejects the designer/brand denylist, imitation phrasing, obvious
    prompt-override phrasing and URLs. Raises :class:`UnsafeUserTextError`
    (generic category only, never the text)."""
    if contains_url(text):
        raise UnsafeUserTextError(RejectionCategory.URL)
    tokens = _tokens(text)
    if any(_contains_phrase(tokens, _phrase_tokens(name)) for name in _DESIGNER_BRAND_NAMES):
        raise UnsafeUserTextError(RejectionCategory.DESIGNER_OR_BRAND)
    if any(_contains_phrase(tokens, _phrase_tokens(p)) for p in _IMITATION_PHRASES):
        raise UnsafeUserTextError(RejectionCategory.IMITATION_PHRASE)
    if any(_contains_phrase(tokens, _phrase_tokens(p)) for p in _PROMPT_LEAKAGE_PHRASES):
        raise UnsafeUserTextError(RejectionCategory.PROMPT_LEAKAGE)
