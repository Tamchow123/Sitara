"""Generated-output and free-text safety scanning (Phase 8).

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


class RejectionCategory(str, Enum):
    DESIGNER_OR_BRAND = "designer_or_brand_reference"
    IMITATION_PHRASE = "imitation_phrase"
    URL = "url"
    CONTROL_CHARACTER = "control_character"
    PROMPT_LEAKAGE = "prompt_leakage"
    SEWING_PATTERN_CLAIM = "sewing_pattern_claim"
    CONSTRUCTIBILITY_CLAIM = "constructibility_claim"


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


def _tokens(text: str) -> list[str]:
    """NFKC + case-fold + punctuation→space + collapse, then split to tokens."""
    normalised = unicodedata.normalize("NFKC", text).casefold()
    # Replace any run of non-word (Unicode-aware) characters with a single
    # space, so punctuation/spacing variants collapse to the same tokens.
    normalised = re.sub(r"\W+", " ", normalised, flags=re.UNICODE)
    return normalised.split()


def _phrase_tokens(phrase: str) -> list[str]:
    return _tokens(phrase)


def _contains_phrase(haystack: list[str], needle: list[str]) -> bool:
    """True if ``needle`` occurs as a contiguous token subsequence."""
    if not needle:
        return False
    limit = len(haystack) - len(needle)
    for start in range(0, limit + 1):
        if haystack[start : start + len(needle)] == needle:
            return True
    return False


def _asserts_claim(text: str, claim_phrases: tuple[str, ...]) -> bool:
    """True if a claim phrase is ASSERTED — appears in a sentence that carries
    no negation. The required construction caveats legitimately mention these
    phrases while NEGATING them ("not a sewing pattern", "does not guarantee
    ... constructed exactly as shown"), and the negation can sit far from the
    phrase, so negation is checked at sentence scope rather than a fixed
    window."""
    for sentence in re.split(r"[.!?\n]+", text):
        tokens = _tokens(sentence)
        if not tokens:
            continue
        if any(token in _NEGATIONS for token in tokens):
            continue  # a negated sentence is a disclaimer, not a claim
        if any(_contains_phrase(tokens, _phrase_tokens(p)) for p in claim_phrases):
            return True
    return False


def scan_generated_text(text: str) -> None:
    """Scan one generated string; raise :class:`GeneratedContentRejected`."""
    # Control characters other than a normal line break (\n). \r is normalised
    # away upstream; anything else in category Cc is rejected.
    for char in text:
        if char != "\n" and unicodedata.category(char) == "Cc":
            raise GeneratedContentRejected(RejectionCategory.CONTROL_CHARACTER)

    if _URL_PATTERN.search(text):
        raise GeneratedContentRejected(RejectionCategory.URL)

    tokens = _tokens(text)
    if any(_contains_phrase(tokens, _phrase_tokens(name)) for name in _DESIGNER_BRAND_NAMES):
        raise GeneratedContentRejected(RejectionCategory.DESIGNER_OR_BRAND)
    if any(_contains_phrase(tokens, _phrase_tokens(p)) for p in _IMITATION_PHRASES):
        raise GeneratedContentRejected(RejectionCategory.IMITATION_PHRASE)
    if any(_contains_phrase(tokens, _phrase_tokens(p)) for p in _PROMPT_LEAKAGE_PHRASES):
        raise GeneratedContentRejected(RejectionCategory.PROMPT_LEAKAGE)
    if _asserts_claim(text, _SEWING_PATTERN_CLAIMS):
        raise GeneratedContentRejected(RejectionCategory.SEWING_PATTERN_CLAIM)
    if _asserts_claim(text, _CONSTRUCTIBILITY_CLAIMS):
        raise GeneratedContentRejected(RejectionCategory.CONSTRUCTIBILITY_CLAIM)


def _iter_strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _iter_strings(item)


def scan_design_spec(spec) -> None:
    """Recursively scan every string in a validated DesignSpec.

    ``spec`` is a :class:`~sitara.generation.design_spec.DesignSpec`; its
    ``model_dump()`` is walked so machine values and all narrative strings are
    checked."""
    for text in _iter_strings(spec.model_dump(mode="python")):
        scan_generated_text(text)


def scan_user_text(text: str) -> None:
    """Scan a user free-text answer BEFORE any provider call.

    Rejects the designer/brand denylist, imitation phrasing, obvious
    prompt-override phrasing and URLs. Raises :class:`UnsafeUserTextError`
    (generic category only, never the text)."""
    if _URL_PATTERN.search(text):
        raise UnsafeUserTextError(RejectionCategory.URL)
    tokens = _tokens(text)
    if any(_contains_phrase(tokens, _phrase_tokens(name)) for name in _DESIGNER_BRAND_NAMES):
        raise UnsafeUserTextError(RejectionCategory.DESIGNER_OR_BRAND)
    if any(_contains_phrase(tokens, _phrase_tokens(p)) for p in _IMITATION_PHRASES):
        raise UnsafeUserTextError(RejectionCategory.IMITATION_PHRASE)
    if any(_contains_phrase(tokens, _phrase_tokens(p)) for p in _PROMPT_LEAKAGE_PHRASES):
        raise UnsafeUserTextError(RejectionCategory.PROMPT_LEAKAGE)
