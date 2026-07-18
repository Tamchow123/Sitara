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
"""

import re
import unicodedata

from .design_spec import DESIGN_SPEC_SCHEMA_VERSION, DesignSpec
from .input_safety import scan_design_spec, scan_generated_text

# Bump ONLY with a deliberate snapshot review and manifest update (see the
# prompt-builder snapshot tests). The persisted provenance records this value.
PROMPT_BUILDER_VERSION = "1.0.0"

# Hard upper bound on the assembled prompt. The per-slot caps below keep a
# realistic DesignSpec well within this; an unexpected overrun is a controlled
# ImagePromptBuildError rather than a blind slice that could drop the coverage
# or presentation sections.
IMAGE_PROMPT_MAX_CHARS = 6000

# Documented per-slot character caps. Each generated narrative string is
# normalised and truncated at a word boundary to at most its slot cap before
# interpolation. Critical machine selections and coverage choices are rendered
# directly from short machine values and are never subject to these caps.
_SUMMARY_CAP = 700
_NARRATIVE_CAP = 300
_LIST_ITEM_CAP = 200

# Fixed positive-only presentation instructions, following the Phase 2
# evaluated path. These express the safeguards positively (original/non-branded
# design, clean composition, natural anatomy); there is deliberately NO negative
# prompt and NO universal modesty/sleeve/neckline suffix — coverage comes only
# from the DesignSpec so a generic suffix can never contradict validated
# choices.
_PRESENTATION = (
    "Present the concept as a full-length studio fashion photograph with the "
    "entire garment visible from head to hem, set against a clean, uncluttered "
    "studio background. Render an original, non-branded textile and embroidery "
    "design with natural anatomy and coherent, naturally posed hands, lit by "
    "soft, even lighting that shows the true fabric colour and embroidery detail."
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

_WHITESPACE = re.compile(r"\s+")


class ImagePromptBuildError(Exception):
    """The image prompt could not be built safely.

    Carries only a generic, safe message — never the prompt contents, spec
    narrative or any user data — so it is always safe to surface and log."""


def _slot(text: str, cap: int) -> str:
    """Normalise one generated narrative string into a bounded prompt slot.

    Applies Unicode NFKC normalisation, converts CRLF/CR to LF, collapses all
    internal whitespace to single spaces, strips ends, and truncates at a word
    boundary to at most ``cap`` characters. Preserves meaningful words and never
    inserts HTML, Markdown or control characters."""
    normalised = unicodedata.normalize("NFKC", text)
    normalised = normalised.replace("\r\n", "\n").replace("\r", "\n")
    normalised = _WHITESPACE.sub(" ", normalised).strip()
    if len(normalised) <= cap:
        return normalised
    truncated = normalised[:cap]
    boundary = truncated.rfind(" ")
    if boundary > 0:
        truncated = truncated[:boundary]
    return truncated.rstrip()


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


def _garment_and_ceremony(spec: DesignSpec) -> str:
    ss = spec.source_selections
    garment = _readable(ss.garment_type)
    ceremony = _readable(ss.ceremony)
    parts = [
        _sentence(f"{_slot(spec.title, _NARRATIVE_CAP)}"),
        _sentence(f"A South Asian bridal {garment} styled for a {ceremony} ceremony"),
        _sentence(_slot(spec.concept_summary, _SUMMARY_CAP)),
        _sentence(_slot(spec.garment_breakdown.overall_form, _NARRATIVE_CAP)),
    ]
    cue = _GARMENT_INTEGRITY_CUES.get(ss.garment_type)
    if cue:
        parts.append(cue)
    return " ".join(part for part in parts if part)


def _silhouette_and_components(spec: DesignSpec) -> str:
    gb = spec.garment_breakdown
    silhouette = _readable(spec.source_selections.silhouette)
    parts = [
        _sentence(f"The silhouette is {silhouette}"),
        _sentence(_slot(gb.silhouette, _NARRATIVE_CAP)),
    ]
    components = _join_items(gb.garment_components)
    if components:
        parts.append(_sentence(f"Its components include {components}"))
    return " ".join(part for part in parts if part)


def _drape_and_proportions(spec: DesignSpec) -> str:
    gb = spec.garment_breakdown
    return " ".join(
        part
        for part in (
            _sentence(_slot(gb.drape_or_layering, _NARRATIVE_CAP)),
            _sentence(_slot(gb.key_proportions, _NARRATIVE_CAP)),
        )
        if part
    )


def _colour(spec: DesignSpec) -> str:
    cs = spec.colour_story
    colours = _readable_list(spec.source_selections.colour_palette)
    parts = []
    if colours:
        parts.append(_sentence(f"The colour palette, in order, is {colours}"))
    parts.extend(
        (
            _sentence(_slot(cs.palette_summary, _NARRATIVE_CAP)),
            _sentence(_slot(cs.placement, _NARRATIVE_CAP)),
            _sentence(_slot(cs.rationale, _NARRATIVE_CAP)),
        )
    )
    return " ".join(part for part in parts if part)


def _fabrics(spec: DesignSpec) -> str:
    ss = spec.source_selections
    parts = []
    if ss.fabrics:
        parts.append(_sentence(f"The selected fabrics, in order, are {_readable_list(ss.fabrics)}"))
    for entry in spec.fabrics_and_texture:
        fabric = _slot(entry.fabric, _LIST_ITEM_CAP)
        placement = _slot(entry.placement, _LIST_ITEM_CAP)
        finish = _slot(entry.finish_and_movement, _LIST_ITEM_CAP)
        detail = ". ".join(bit for bit in (placement, finish) if bit)
        parts.append(_sentence(f"{fabric}: {detail}" if detail else fabric))
    return " ".join(part for part in parts if part)


def _embellishment(spec: DesignSpec) -> str:
    ss = spec.source_selections
    ep = spec.embellishment_plan
    parts = []
    if ss.embellishment_density:
        parts.append(_sentence(f"Embellishment density: {_readable(ss.embellishment_density)}"))
    if ss.embellishment_styles:
        parts.append(
            _sentence(
                "The selected embellishment styles, in order, are "
                f"{_readable_list(ss.embellishment_styles)}"
            )
        )
    techniques = _join_items(ep.techniques)
    if techniques:
        parts.append(_sentence(f"Techniques: {techniques}"))
    parts.append(_sentence(_slot(ep.density, _NARRATIVE_CAP)))
    placement = _join_items(ep.placement)
    if placement:
        parts.append(_sentence(f"Concentrated at {placement}"))
    motifs = _join_items(ep.motifs)
    if motifs:
        parts.append(_sentence(f"Motifs: {motifs}"))
    parts.append(_sentence(_slot(ep.restraint_notes, _NARRATIVE_CAP)))
    return " ".join(part for part in parts if part)


def _coverage(spec: DesignSpec) -> str:
    ss = spec.source_selections
    cd = spec.coverage_and_drape
    parts = []
    if ss.coverage_preferences:
        parts.append(_sentence(f"Coverage preferences: {_readable_list(ss.coverage_preferences)}"))
    parts.extend(
        (
            _sentence(f"Sleeves: {_slot(cd.sleeves, _NARRATIVE_CAP)}"),
            _sentence(f"Neckline: {_slot(cd.neckline, _NARRATIVE_CAP)}"),
            _sentence(f"Back and midriff: {_slot(cd.back_and_midriff, _NARRATIVE_CAP)}"),
            _sentence(f"Head covering: {_slot(cd.head_covering, _NARRATIVE_CAP)}"),
        )
    )
    return " ".join(part for part in parts if part)


def _dupatta_or_drape(spec: DesignSpec) -> str:
    ss = spec.source_selections
    cd = spec.coverage_and_drape
    selections = []
    if ss.dupatta_style:
        selections.append(f"dupatta style {_readable(ss.dupatta_style)}")
    if ss.saree_drape:
        selections.append(f"saree drape {_readable(ss.saree_drape)}")
    parts = []
    if selections:
        parts.append(_sentence("Drape: " + ", ".join(selections)))
    parts.append(_sentence(_slot(cd.dupatta_or_saree_drape, _NARRATIVE_CAP)))
    return " ".join(part for part in parts if part)


def _cultural_and_styling(spec: DesignSpec) -> str:
    cc = spec.cultural_context
    parts = []
    if cc.regional_direction is not None:
        parts.append(
            _sentence(
                "Broad regional influence, offered as guidance rather than a "
                f"universal rule: {_slot(cc.regional_direction, _NARRATIVE_CAP)}"
            )
        )
    interpretation = _join_items(cc.interpretation_notes)
    if interpretation:
        parts.append(_sentence(f"Interpretation: {interpretation}"))
    safeguards = _join_items(cc.safeguards)
    if safeguards:
        parts.append(_sentence(f"Safeguards: {safeguards}"))
    styling = _join_items(spec.styling_notes)
    if styling:
        parts.append(_sentence(f"Styling cues: {styling}"))
    return " ".join(part for part in parts if part)


# Fixed conceptual ordering — stable and snapshot-tested.
_SECTION_BUILDERS = (
    _garment_and_ceremony,
    _silhouette_and_components,
    _drape_and_proportions,
    _colour,
    _fabrics,
    _embellishment,
    _coverage,
    _dupatta_or_drape,
    _cultural_and_styling,
)


def build_image_prompt(spec: DesignSpec) -> str:
    """Build the deterministic natural-language image prompt for ``spec``.

    Accepts a validated :class:`DesignSpec` (or any DesignSpec-compatible
    payload, revalidated defensively here). Runs the generated-content safety
    scan before interpolation, renders the fixed section order, and runs a final
    safety scan on the finished prompt. Raises :class:`ImagePromptBuildError` on
    any unsafe content or an unexpected length overrun — never echoing the
    prompt or spec text."""
    if not isinstance(spec, DesignSpec):
        try:
            spec = DesignSpec.model_validate(spec)
        except Exception as exc:  # controlled: never surface the payload
            raise ImagePromptBuildError("design spec failed validation") from exc

    # Safety scan over every generated string BEFORE interpolation.
    scan_design_spec(spec)

    sections = [builder(spec) for builder in _SECTION_BUILDERS]
    sections.append(_PRESENTATION)
    prompt = "\n\n".join(section for section in sections if section.strip())

    # Final safety scan on the finished prompt: blocked designer/brand,
    # imitation phrase, URL, prompt leakage, untrusted-section delimiter and
    # control characters are all covered by scan_generated_text.
    try:
        scan_generated_text(prompt)
    except Exception as exc:
        raise ImagePromptBuildError("assembled image prompt failed the safety scan") from exc

    if len(prompt) > IMAGE_PROMPT_MAX_CHARS:
        # Do NOT slice — a truncated prompt could drop coverage or presentation.
        raise ImagePromptBuildError("assembled image prompt exceeded the maximum length")
    return prompt


# Re-exported so the persistence service can require the supported schema
# version without importing two modules.
SUPPORTED_DESIGN_SPEC_SCHEMA_VERSION = DESIGN_SPEC_SCHEMA_VERSION
