"""Deterministic, model-aware prompt rendering.

One prompt structure does not fit every FLUX-family model, so each candidate
is rendered in the formats its capabilities actually support:

- ``editorial``          natural-language paragraph, positive-only wording.
- ``sectioned``          labelled section-based text prompt, positive-only.
- ``json``               structured JSON payload — only for models whose
                         official documentation supports/recommends it.
- ``editorial_negative`` editorial text plus a separate controlled exclusion
                         list — only for models with real negative-prompt
                         support. Exclusion phrases are never appended to the
                         positive prompt of other models.

Positive-only presentation wording (used everywhere) describes what we DO
want instead of listing what we don't — and it deliberately stays OUT of the
brief's territory: coverage, sleeves and neckline come only from the
individual brief (a universal modesty suffix would contradict briefs that
specify their own coverage), fabric/embroidery character comes from the
brief (a universal "plain fabric" phrase would contradict heavy zardozi),
and hands are described conditionally so the wording never forces hands
into frame.

Rendering is a pure function of (brief, format, inspiration mode) and is
covered by determinism tests. The exact rendered output is stored in every
result record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Brief, Capabilities, InspirationMode, ModelCandidate, RefinementChange

FORMAT_EDITORIAL = "editorial"
FORMAT_SECTIONED = "sectioned"
FORMAT_JSON = "json"
FORMAT_EDITORIAL_NEGATIVE = "editorial_negative"

ALL_FORMATS = (
    FORMAT_EDITORIAL,
    FORMAT_SECTIONED,
    FORMAT_JSON,
    FORMAT_EDITORIAL_NEGATIVE,
)

# Positive-only presentation vocabulary. Order is fixed and meaningful.
# Constraint: nothing here may specify coverage, sleeves, neckline,
# embellishment density or fabric plainness — those belong to the brief.
PRESENTATION_POSITIVE: tuple[str, ...] = (
    "full-length studio fashion photograph with the entire garment visible from head to hem",
    "clean uncluttered studio background",
    "an original, non-branded textile and embroidery design",
    "any visible hands rendered naturally with coherent fingers",
    "soft even lighting that shows true fabric colour and embroidery detail",
)

# Controlled exclusion list, used ONLY via a dedicated negative-prompt
# parameter on models that genuinely support one.
CONTROLLED_EXCLUSIONS: tuple[str, ...] = (
    "text",
    "logos",
    "watermarks",
    "brand marks",
    "designer labels",
    "extra limbs",
    "distorted hands",
)


class PromptFormatError(Exception):
    """A format was requested that the candidate model does not support."""


@dataclass(frozen=True)
class RenderedPrompt:
    format: str
    text: str | None
    negative_text: str | None
    json_payload: dict[str, Any] | None

    def as_provider_input(self) -> dict[str, Any]:
        """Prompt-related provider input fields (parameter names for the
        negative prompt are supplied by the caller from capabilities)."""
        if self.json_payload is not None:
            import json

            return {"prompt": json.dumps(self.json_payload, sort_keys=True)}
        assert self.text is not None
        return {"prompt": self.text}


def formats_for(candidate: ModelCandidate, requested: list[str] | str) -> list[str]:
    """Resolve the formats to run for a candidate.

    ``"auto"`` yields every format the model supports. An explicit list is
    filtered to supported formats; unsupported explicit requests are dropped
    by the planner as skips (never silently sent).
    """
    supported = [FORMAT_EDITORIAL, FORMAT_SECTIONED]
    if candidate.capabilities.json_prompting:
        supported.append(FORMAT_JSON)
    if candidate.capabilities.negative_prompt:
        supported.append(FORMAT_EDITORIAL_NEGATIVE)
    if requested == "auto":
        return supported
    return [f for f in requested if f in supported]


def unsupported_formats(candidate: ModelCandidate, requested: list[str] | str) -> list[str]:
    if requested == "auto":
        return []
    return [f for f in requested if f not in formats_for(candidate, "auto")]


# ---------------------------------------------------------------------------
# Brief -> descriptive fragments (fixed order)
# ---------------------------------------------------------------------------


def _garment_label(brief: Brief) -> str:
    return brief.garment.replace("_", " ")


def _ceremony_fragment(brief: Brief) -> str:
    parts: list[str] = []
    if brief.ceremony:
        parts.append(f"styled for a {brief.ceremony} ceremony")
    if brief.region:
        parts.append(f"in the {brief.region} tradition")
    return ", ".join(parts)


def _embellishment_fragment(brief: Brief) -> str:
    level = {
        "minimal": "minimal, restrained embellishment",
        "moderate": "moderate embellishment",
        "heavy": "heavy, densely worked embellishment",
    }[brief.embellishment_level]
    if brief.embellishment_techniques:
        techniques = ", ".join(brief.embellishment_techniques)
        return f"{level} using {techniques}"
    return level


def _inspiration_block(brief: Brief) -> str:
    """Curated catalogue metadata, rendered deterministically (sorted keys).

    Used only in ``metadata`` inspiration mode. Contains no image data.
    """
    if not brief.inspiration_metadata:
        return ""
    items = ", ".join(f"{k}: {v}" for k, v in sorted(brief.inspiration_metadata.items()))
    return f"Inspiration cues from curated catalogue metadata — {items}."


def _sections(brief: Brief, mode: InspirationMode) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = [
        ("Garment", f"South Asian bridal {_garment_label(brief)}"),
        ("Ceremony", _ceremony_fragment(brief) or "bridal occasion"),
        ("Colour palette", brief.palette),
        ("Fabric", brief.fabric),
        ("Embellishment", _embellishment_fragment(brief)),
        ("Sleeves", brief.sleeves),
        ("Neckline", brief.neckline),
        ("Coverage", brief.coverage),
        ("Dupatta", brief.dupatta),
    ]
    if brief.extras:
        sections.append(("Details", brief.extras))
    if mode == "metadata":
        block = _inspiration_block(brief)
        if block:
            sections.append(("Inspiration", block))
    sections.append(("Presentation", "; ".join(PRESENTATION_POSITIVE)))
    return sections


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_editorial(brief: Brief, mode: InspirationMode) -> str:
    ceremony = _ceremony_fragment(brief)
    sentences = [
        (
            f"A South Asian bridal {_garment_label(brief)}"
            + (f", {ceremony}" if ceremony else "")
            + f", in {brief.palette}, crafted from {brief.fabric}."
        ),
        f"It features {_embellishment_fragment(brief)}.",
        (
            f"The design has {brief.sleeves}, a {brief.neckline}, and {brief.coverage}."
        ),
        f"The dupatta is {brief.dupatta}.",
    ]
    if brief.extras:
        sentences.append(brief.extras if brief.extras.endswith(".") else brief.extras + ".")
    if mode == "metadata":
        block = _inspiration_block(brief)
        if block:
            sentences.append(block)
    sentences.append(". ".join(PRESENTATION_POSITIVE).capitalize() + ".")
    return " ".join(sentences)


def _render_sectioned(brief: Brief, mode: InspirationMode) -> str:
    return "\n".join(f"{label}: {value}" for label, value in _sections(brief, mode))


def _render_json(brief: Brief, mode: InspirationMode) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "scene": "full-length studio fashion photograph",
        "subject": {
            "description": "bride wearing the described garment",
            "hands": "any visible hands rendered naturally with coherent fingers",
        },
        "garment": {
            "type": f"South Asian bridal {_garment_label(brief)}",
            "ceremony": _ceremony_fragment(brief) or "bridal occasion",
            "palette": brief.palette,
            "fabric": brief.fabric,
            "embellishment": _embellishment_fragment(brief),
            "sleeves": brief.sleeves,
            "neckline": brief.neckline,
            "coverage": brief.coverage,
            "dupatta": brief.dupatta,
        },
        "background": "clean uncluttered studio background",
        "lighting": "soft even lighting showing true fabric colour and embroidery detail",
        "branding": "an original, non-branded textile and embroidery design",
        "framing": "entire garment visible from head to hem",
    }
    if brief.extras:
        payload["garment"]["details"] = brief.extras
    if mode == "metadata" and brief.inspiration_metadata:
        payload["inspiration_metadata"] = dict(sorted(brief.inspiration_metadata.items()))
    return payload


def render_prompt(
    brief: Brief,
    fmt: str,
    mode: InspirationMode,
    capabilities: Capabilities,
) -> RenderedPrompt:
    """Render a brief in the given format.

    ``reference_image`` mode intentionally renders the same text as
    ``text_only``: the controlled variable is the attached reference image,
    not the wording.
    """
    text_mode: InspirationMode = "text_only" if mode == "reference_image" else mode
    if fmt == FORMAT_EDITORIAL:
        return RenderedPrompt(fmt, _render_editorial(brief, text_mode), None, None)
    if fmt == FORMAT_SECTIONED:
        return RenderedPrompt(fmt, _render_sectioned(brief, text_mode), None, None)
    if fmt == FORMAT_JSON:
        if not capabilities.json_prompting:
            raise PromptFormatError("json format requested for a model without JSON prompting")
        return RenderedPrompt(fmt, None, None, _render_json(brief, text_mode))
    if fmt == FORMAT_EDITORIAL_NEGATIVE:
        if not capabilities.negative_prompt:
            raise PromptFormatError(
                "editorial_negative format requested for a model without negative-prompt support"
            )
        return RenderedPrompt(
            fmt,
            _render_editorial(brief, text_mode),
            ", ".join(CONTROLLED_EXCLUSIONS),
            None,
        )
    raise PromptFormatError(f"unknown prompt format {fmt!r}")


# ---------------------------------------------------------------------------
# Refinement
# ---------------------------------------------------------------------------


def apply_refinement(brief: Brief, change: RefinementChange) -> Brief:
    """Return a copy of the brief with exactly the one constrained change."""
    current = getattr(brief, change.field)
    if current != change.from_value:
        raise ValueError(
            f"refinement {change.id!r}: brief field {change.field!r} is "
            f"{current!r}, expected {change.from_value!r}"
        )
    return brief.model_copy(update={change.field: change.to_value, "refinement": None})


def render_edit_instruction(brief: Brief, change: RefinementChange) -> str:
    """Instruction prompt for image-editing / conditioned-refinement models.

    Wording is derived from the change's field/from/to (via the derived
    description) so it can never contradict the actual change applied."""
    return (
        f"Edit this image of a South Asian bridal {_garment_label(brief)}: "
        f"change {change.description}. "
        "Preserve every unspecified detail exactly as it is: the same pose, the same "
        "composition and framing, the same face, the same fabric texture, the same "
        "embellishment placement, the same background and lighting."
    )
