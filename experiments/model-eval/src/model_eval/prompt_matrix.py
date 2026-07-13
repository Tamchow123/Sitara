"""Deterministic expansion of a stage config into planned requests.

The matrix is the cross product of briefs x models x prompt formats x
inspiration modes x seeds, plus refinement experiments. Expansion is pure:
the same inputs always produce the same ordered list of PlannedRequest
objects with the same request IDs, which is what makes interrupted runs
resumable and budgets predictable.

Combinations a MODEL cannot serve (reference images without reference
support, image-edit refinement without editing support, an explicitly
requested format the model lacks) are emitted as SKIPPED planned requests
with a reason — never silently dropped and never sent.

Combinations a BRIEF cannot express are quietly omitted instead, because
they would only duplicate spend, not reveal a model limitation: `metadata`
mode when a brief has no inspiration_metadata (the prompt would be identical
to text_only) and `reference_image` mode when a brief lists no references.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .config import (
    Brief,
    BriefsFile,
    CandidatesConfig,
    ConfigError,
    InspirationMode,
    ModelCandidate,
    StageConfig,
)
from .prompt_formats import (
    FORMAT_EDITORIAL,
    RenderedPrompt,
    apply_refinement,
    formats_for,
    render_edit_instruction,
    render_prompt,
    unsupported_formats,
)

RequestKind = Literal["base", "refinement_fresh", "refinement_edit"]

SKIP_NO_REFERENCE_SUPPORT = "model_lacks_reference_image_support"
SKIP_NO_EDIT_SUPPORT = "model_lacks_image_editing_support"
SKIP_FORMAT_UNSUPPORTED = "prompt_format_not_supported_by_model"


class PlannedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    stage: str
    brief_id: str
    garment: str
    ceremony: str | None
    tags: list[str]
    model_key: str
    replicate_id: str
    model_version: str | None
    prompt_format: str
    inspiration_mode: InspirationMode
    reference_ids: list[str]
    seed: int | None
    kind: RequestKind
    refinement_id: str | None = None
    refinement_strategy: str | None = None
    base_request_id: str | None = None
    prompt_text: str | None
    negative_text: str | None
    json_payload: dict[str, Any] | None
    input_params: dict[str, Any]
    aspect_ratio: str
    estimated_max_cost_usd: float
    skipped: bool = False
    skip_reason: str | None = None


class RunPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    requests: list[PlannedRequest]

    @property
    def runnable(self) -> list[PlannedRequest]:
        return [r for r in self.requests if not r.skipped]

    @property
    def skipped(self) -> list[PlannedRequest]:
        return [r for r in self.requests if r.skipped]

    @property
    def total_max_cost_usd(self) -> float:
        return round(sum(r.estimated_max_cost_usd for r in self.runnable), 6)

    def counts_by(self, attr: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.runnable:
            key = str(getattr(r, attr))
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))


def _request_id(
    stage: str,
    brief_id: str,
    model_key: str,
    fmt: str,
    mode: str,
    seed: int | None,
    kind: str,
    refinement_id: str | None = None,
    strategy: str | None = None,
) -> str:
    parts = [stage, brief_id, model_key, fmt, mode, f"s{seed if seed is not None else 'x'}", kind]
    if refinement_id:
        parts.append(refinement_id)
    if strategy:
        parts.append(strategy)
    return "--".join(parts)


def _base_input_params(
    candidate: ModelCandidate,
    stage: StageConfig,
    rendered: RenderedPrompt,
    seed: int | None,
) -> dict[str, Any]:
    params: dict[str, Any] = dict(rendered.as_provider_input())
    if rendered.negative_text is not None:
        assert candidate.capabilities.negative_prompt_param is not None
        params[candidate.capabilities.negative_prompt_param] = rendered.negative_text
    if candidate.capabilities.seed and seed is not None:
        params[candidate.capabilities.seed_param] = seed
    if stage.aspect_ratio:
        params["aspect_ratio"] = stage.aspect_ratio
    params.update(stage.model_params.get(candidate.key, {}))
    return params


def _planned(
    *,
    stage: StageConfig,
    brief: Brief,
    candidate: ModelCandidate,
    fmt: str,
    mode: InspirationMode,
    seed: int | None,
    kind: RequestKind,
    rendered: RenderedPrompt | None,
    reference_ids: list[str],
    refinement_id: str | None = None,
    refinement_strategy: str | None = None,
    base_request_id: str | None = None,
    skip_reason: str | None = None,
) -> PlannedRequest:
    skipped = skip_reason is not None
    input_params: dict[str, Any] = {}
    if rendered is not None and not skipped:
        input_params = _base_input_params(candidate, stage, rendered, seed)
    return PlannedRequest(
        request_id=_request_id(
            stage.stage, brief.id, candidate.key, fmt, mode, seed, kind,
            refinement_id, refinement_strategy,
        ),
        stage=stage.stage,
        brief_id=brief.id,
        garment=brief.garment,
        ceremony=brief.ceremony,
        tags=list(brief.tags),
        model_key=candidate.key,
        replicate_id=candidate.replicate_id,
        model_version=candidate.version,
        prompt_format=fmt,
        inspiration_mode=mode,
        reference_ids=list(reference_ids),
        seed=seed,
        kind=kind,
        refinement_id=refinement_id,
        refinement_strategy=refinement_strategy,
        base_request_id=base_request_id,
        prompt_text=rendered.text if rendered else None,
        negative_text=rendered.negative_text if rendered else None,
        json_payload=rendered.json_payload if rendered else None,
        input_params=input_params,
        aspect_ratio=stage.aspect_ratio,
        estimated_max_cost_usd=0.0 if skipped else candidate.pricing.max_cost_per_generation_usd,
        skipped=skipped,
        skip_reason=skip_reason,
    )


def expand(stage: StageConfig, candidates: CandidatesConfig, briefs: BriefsFile) -> RunPlan:
    """Expand a stage configuration into an ordered, deterministic plan."""
    selected_briefs: list[Brief]
    if stage.brief_ids == "all":
        selected_briefs = list(briefs.briefs)
    else:
        selected_briefs = [briefs.by_id(bid) for bid in stage.brief_ids]

    selected_models = [candidates.by_key(k) for k in stage.models]

    requests: list[PlannedRequest] = []
    for brief in selected_briefs:
        for candidate in selected_models:
            fmts = formats_for(candidate, stage.prompt_formats)
            # Explicitly requested but unsupported formats become visible skips.
            for bad_fmt in unsupported_formats(candidate, stage.prompt_formats):
                requests.append(
                    _planned(
                        stage=stage, brief=brief, candidate=candidate, fmt=bad_fmt,
                        mode=stage.inspiration_modes[0], seed=stage.seeds[0],
                        kind="base", rendered=None, reference_ids=[],
                        skip_reason=SKIP_FORMAT_UNSUPPORTED,
                    )
                )
            for fmt in fmts:
                for mode in stage.inspiration_modes:
                    for seed in stage.seeds:
                        requests.extend(
                            _expand_cell(stage, brief, candidate, fmt, mode, seed)
                        )
    return RunPlan(stage=stage.stage, requests=requests)


def _expand_cell(
    stage: StageConfig,
    brief: Brief,
    candidate: ModelCandidate,
    fmt: str,
    mode: InspirationMode,
    seed: int | None,
) -> list[PlannedRequest]:
    out: list[PlannedRequest] = []

    # Brief-shape omissions (identical prompt / nothing to attach): quiet.
    if mode == "metadata" and not brief.inspiration_metadata:
        return out
    if mode == "reference_image" and not brief.reference_ids:
        return out

    reference_ids: list[str] = []
    skip_reason: str | None = None
    if mode == "reference_image":
        if not candidate.capabilities.reference_image:
            # The brief has references but this model cannot take them:
            # a visible, recorded skip — never silently ignored.
            skip_reason = SKIP_NO_REFERENCE_SUPPORT
        else:
            reference_ids = brief.reference_ids[: candidate.capabilities.max_reference_images]

    rendered = None if skip_reason else render_prompt(brief, fmt, mode, candidate.capabilities)
    base = _planned(
        stage=stage, brief=brief, candidate=candidate, fmt=fmt, mode=mode,
        seed=seed, kind="base", rendered=rendered, reference_ids=reference_ids,
        skip_reason=skip_reason,
    )
    out.append(base)

    if not (stage.refinement.enabled and brief.refinement is not None):
        return out
    # Refinement experiments run once per (brief, model, seed), anchored to
    # the editorial text_only cell to keep comparisons controlled.
    if fmt != FORMAT_EDITORIAL or mode != "text_only":
        return out

    change = brief.refinement
    for strategy in stage.refinement.strategies:
        if strategy == "fresh_regeneration":
            refined_brief = apply_refinement(brief, change)
            refined_rendered = render_prompt(refined_brief, fmt, mode, candidate.capabilities)
            out.append(
                _planned(
                    stage=stage, brief=brief, candidate=candidate, fmt=fmt,
                    mode=mode, seed=seed, kind="refinement_fresh",
                    rendered=refined_rendered, reference_ids=[],
                    refinement_id=change.id, refinement_strategy=strategy,
                    base_request_id=base.request_id if not base.skipped else None,
                )
            )
        elif strategy == "image_edit":
            if not candidate.capabilities.image_editing:
                out.append(
                    _planned(
                        stage=stage, brief=brief, candidate=candidate, fmt=fmt,
                        mode=mode, seed=seed, kind="refinement_edit",
                        rendered=None, reference_ids=[],
                        refinement_id=change.id, refinement_strategy=strategy,
                        base_request_id=base.request_id,
                        skip_reason=SKIP_NO_EDIT_SUPPORT,
                    )
                )
            else:
                edit_text = render_edit_instruction(brief, change)
                edit_rendered = RenderedPrompt(fmt, edit_text, None, None)
                # The base image is attached by the runner via the model's
                # image_editing_param once the base output exists.
                out.append(
                    _planned(
                        stage=stage, brief=brief, candidate=candidate, fmt=fmt,
                        mode=mode, seed=seed, kind="refinement_edit",
                        rendered=edit_rendered, reference_ids=[],
                        refinement_id=change.id, refinement_strategy=strategy,
                        base_request_id=base.request_id,
                    )
                )
        else:  # pragma: no cover - schema restricts strategies
            raise ConfigError(f"unknown refinement strategy {strategy!r}")
    return out
