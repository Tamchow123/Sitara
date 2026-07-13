"""Configuration models and loaders for the evaluation.

All YAML inputs (model candidates, briefs, stage configs, reference
manifests) are validated with pydantic before anything is planned or run.

Candidate facts — pricing, capabilities, licence terms — are time-sensitive.
Every candidate carries the dates its pricing and terms were verified, and a
human must re-check current official provider pages immediately before any
live run.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Category = Literal["fast", "balanced", "highest_quality", "reference", "editing"]
InspirationMode = Literal["text_only", "metadata", "reference_image"]
RefinementStrategy = Literal["fresh_regeneration", "image_edit"]
StageName = Literal["screening", "finalist"]

_REPLICATE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ConfigError(Exception):
    """A configuration file failed to load or validate."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Model candidates
# ---------------------------------------------------------------------------


class Capabilities(StrictModel):
    seed: bool = False
    seed_param: str = "seed"
    aspect_ratios: list[str] = Field(default_factory=list)
    resolution_note: str = ""
    negative_prompt: bool = False
    negative_prompt_param: str | None = None
    reference_image: bool = False
    reference_image_param: str | None = None
    max_reference_images: int = 0
    image_editing: bool = False
    image_editing_param: str | None = None
    # True when the editing param takes a list of images (e.g. FLUX.2
    # input_images) rather than a single image (e.g. Kontext input_image).
    image_editing_param_is_list: bool = False
    json_prompting: bool = False
    input_format_note: str = ""

    @model_validator(mode="after")
    def _capability_params_present(self) -> "Capabilities":
        if self.negative_prompt and not self.negative_prompt_param:
            raise ValueError("negative_prompt=true requires negative_prompt_param")
        if self.reference_image and not self.reference_image_param:
            raise ValueError("reference_image=true requires reference_image_param")
        if self.reference_image and self.max_reference_images < 1:
            raise ValueError("reference_image=true requires max_reference_images >= 1")
        if self.image_editing and not self.image_editing_param:
            raise ValueError("image_editing=true requires image_editing_param")
        return self


class Pricing(StrictModel):
    unit: Literal["per_image", "per_megapixel", "per_second", "unknown"]
    usd_per_unit: float | None = None
    expected_cost_per_generation_usd: float = Field(gt=0)
    # Conservative ceiling used for budget reservations. Must dominate any
    # plausible real charge for a single generation at our settings.
    max_cost_per_generation_usd: float = Field(gt=0)
    checked_on: date
    source_url: str

    @model_validator(mode="after")
    def _max_dominates_expected(self) -> "Pricing":
        if self.max_cost_per_generation_usd < self.expected_cost_per_generation_usd:
            raise ValueError(
                "max_cost_per_generation_usd must be >= expected_cost_per_generation_usd"
            )
        return self


class TermsRecord(StrictModel):
    """What official sources say. Facts only — no legal conclusions.

    Any item that could not be verified must say so explicitly and appear in
    `unresolved` so a human reviews it before a live run or production choice.
    """

    model_licence: str
    commercial_use: str
    input_retention: str
    output_ownership: str
    training_use: str
    unresolved: list[str] = Field(default_factory=list)
    sources: list[str] = Field(min_length=1)
    verified_on: date


class ModelCandidate(StrictModel):
    key: str
    name: str
    replicate_id: str
    # Exact version digest when Replicate exposes one; None means the
    # provider-managed "latest" (record what actually ran in provenance).
    version: str | None = None
    categories: list[Category] = Field(min_length=1)
    capabilities: Capabilities
    pricing: Pricing
    terms: TermsRecord
    notes: str = ""

    @field_validator("key")
    @classmethod
    def _key_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(f"candidate key {v!r} must be a lowercase slug")
        return v

    @field_validator("replicate_id")
    @classmethod
    def _replicate_id_shape(cls, v: str) -> str:
        if not _REPLICATE_ID_RE.match(v):
            raise ValueError(f"replicate_id {v!r} must look like 'owner/model'")
        return v

    @model_validator(mode="after")
    def _categories_match_capabilities(self) -> "ModelCandidate":
        if "reference" in self.categories and not self.capabilities.reference_image:
            raise ValueError(
                f"candidate {self.key!r}: category 'reference' requires "
                "capabilities.reference_image=true"
            )
        if "editing" in self.categories and not self.capabilities.image_editing:
            raise ValueError(
                f"candidate {self.key!r}: category 'editing' requires "
                "capabilities.image_editing=true"
            )
        return self


class PlatformTerms(StrictModel):
    summary: str
    commercial_use: str
    input_retention: str
    training_use: str
    unresolved: list[str] = Field(default_factory=list)
    sources: list[str] = Field(min_length=1)
    verified_on: date


class CandidatesConfig(StrictModel):
    # Set true only in *.example.* files that carry unverified placeholder data.
    requires_manual_verification: bool = False
    platform_terms: PlatformTerms
    candidates: list[ModelCandidate] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_keys(self) -> "CandidatesConfig":
        keys = [c.key for c in self.candidates]
        if len(keys) != len(set(keys)):
            raise ValueError("candidate keys must be unique")
        return self

    def by_key(self, key: str) -> ModelCandidate:
        for c in self.candidates:
            if c.key == key:
                return c
        raise ConfigError(f"unknown candidate key {key!r}")


# ---------------------------------------------------------------------------
# Briefs
# ---------------------------------------------------------------------------

REQUIRED_GARMENTS = {
    "lehenga",
    "saree",
    "gharara",
    "sharara",
    "anarkali",
    "shalwar_kameez",
}
REQUIRED_CEREMONIES = {"nikah", "mehndi", "baraat", "walima"}


class RefinementChange(StrictModel):
    """Exactly one constrained attribute change, applied to a brief field."""

    id: str
    description: str
    field: Literal[
        "palette",
        "embellishment_level",
        "sleeves",
        "dupatta",
        "neckline",
        "coverage",
    ]
    from_value: str
    to_value: str

    @field_validator("id")
    @classmethod
    def _id_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(f"refinement id {v!r} must be a lowercase slug")
        return v


class Brief(StrictModel):
    id: str
    garment: str
    ceremony: str | None = None
    region: str | None = None
    palette: str
    fabric: str
    embellishment_level: Literal["minimal", "moderate", "heavy"]
    embellishment_techniques: list[str] = Field(default_factory=list)
    sleeves: str
    neckline: str
    coverage: str
    dupatta: str
    extras: str = ""
    # Curated catalogue-style metadata used only in `metadata` inspiration mode.
    inspiration_metadata: dict[str, str] | None = None
    # Reference manifest IDs used only in `reference_image` inspiration mode.
    reference_ids: list[str] = Field(default_factory=list)
    refinement: RefinementChange | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(f"brief id {v!r} must be a lowercase slug")
        return v

    @model_validator(mode="after")
    def _refinement_applies(self) -> "Brief":
        if self.refinement is not None:
            current = getattr(self, self.refinement.field)
            if current != self.refinement.from_value:
                raise ValueError(
                    f"brief {self.id!r}: refinement from_value "
                    f"{self.refinement.from_value!r} does not match current "
                    f"{self.refinement.field} value {current!r}"
                )
        return self


class BriefsFile(StrictModel):
    briefs: list[Brief] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> "BriefsFile":
        ids = [b.id for b in self.briefs]
        if len(ids) != len(set(ids)):
            raise ValueError("brief ids must be unique")
        return self

    def by_id(self, brief_id: str) -> Brief:
        for b in self.briefs:
            if b.id == brief_id:
                return b
        raise ConfigError(f"unknown brief id {brief_id!r}")


# ---------------------------------------------------------------------------
# Reference manifest
# ---------------------------------------------------------------------------


class ReferenceEntry(StrictModel):
    id: str
    path: str
    # The runner refuses any reference whose rights_status is not exactly
    # "verified".
    rights_status: Literal["verified", "pending", "rejected"]
    source_name: str
    source_url: str = ""
    licence: str
    attribution: str = ""
    may_be_committed: bool = False
    verified_by: str = ""
    verified_on: date | None = None

    @field_validator("id")
    @classmethod
    def _id_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(f"reference id {v!r} must be a lowercase slug")
        return v


class ReferenceManifest(StrictModel):
    references: list[ReferenceEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> "ReferenceManifest":
        ids = [r.id for r in self.references]
        if len(ids) != len(set(ids)):
            raise ValueError("reference ids must be unique")
        return self

    def by_id(self, ref_id: str) -> ReferenceEntry:
        for r in self.references:
            if r.id == ref_id:
                return r
        raise ConfigError(f"unknown reference id {ref_id!r}")


# ---------------------------------------------------------------------------
# Stage configuration
# ---------------------------------------------------------------------------


class RefinementPlan(StrictModel):
    enabled: bool = False
    strategies: list[RefinementStrategy] = Field(default_factory=list)

    @model_validator(mode="after")
    def _strategies_when_enabled(self) -> "RefinementPlan":
        if self.enabled and not self.strategies:
            raise ValueError("refinement.enabled=true requires at least one strategy")
        return self


class StageConfig(StrictModel):
    stage: StageName
    candidates_file: str
    briefs_file: str
    reference_manifest: str | None = None
    models: list[str] = Field(min_length=1)
    brief_ids: list[str] | Literal["all"] = "all"
    seeds: list[int] = Field(min_length=1)
    aspect_ratio: str
    inspiration_modes: list[InspirationMode] = Field(min_length=1)
    # "auto" derives applicable formats from each model's capabilities.
    prompt_formats: list[str] | Literal["auto"] = "auto"
    refinement: RefinementPlan = Field(default_factory=RefinementPlan)
    # Optional per-candidate extra input parameters (e.g. quality settings).
    model_params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    notes: str = ""

    @model_validator(mode="after")
    def _unique_models(self) -> "StageConfig":
        if len(self.models) != len(set(self.models)):
            raise ValueError("stage models must be unique")
        if len(self.seeds) != len(set(self.seeds)):
            raise ValueError("stage seeds must be unique")
        return self


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc


def _validate[T: BaseModel](model: type[T], data: Any, path: Path) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {path}:\n{exc}") from exc


def load_candidates(path: Path) -> CandidatesConfig:
    return _validate(CandidatesConfig, _load_yaml(path), path)


def load_briefs(path: Path) -> BriefsFile:
    return _validate(BriefsFile, _load_yaml(path), path)


def load_reference_manifest(path: Path) -> ReferenceManifest:
    return _validate(ReferenceManifest, _load_yaml(path), path)


def load_stage(path: Path) -> StageConfig:
    return _validate(StageConfig, _load_yaml(path), path)
