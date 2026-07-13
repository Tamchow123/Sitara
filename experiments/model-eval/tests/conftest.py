"""Shared fixtures. All provider interactions are mocked — these tests must
never reach Replicate, and several of them prove exactly that."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pytest
from PIL import Image

from model_eval.config import (
    Brief,
    BriefsFile,
    CandidatesConfig,
    Capabilities,
    ModelCandidate,
    PlatformTerms,
    Pricing,
    ReferenceEntry,
    ReferenceManifest,
    RefinementChange,
    StageConfig,
    TermsRecord,
)
from model_eval.replicate_client import Prediction, ProviderError
from model_eval.runner import LoadedStage
from model_eval.prompt_matrix import expand

TODAY = date(2026, 7, 13)

EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]


def tiny_png_bytes(colour: tuple[int, int, int] = (200, 30, 60)) -> bytes:
    """A generated 8x8 placeholder — never real bridal photography."""
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), colour).save(buf, format="PNG")
    return buf.getvalue()


def make_terms() -> TermsRecord:
    return TermsRecord(
        model_licence="test licence",
        commercial_use="test",
        input_retention="test",
        output_ownership="test",
        training_use="test",
        sources=["https://example.com/terms"],
        verified_on=TODAY,
    )


def make_pricing(
    expected: float = 0.04,
    maximum: float = 0.1,
    formula_verified: bool = True,
    **extra: Any,
) -> Pricing:
    return Pricing(
        unit=extra.pop("unit", "per_image"),
        usd_per_unit=expected,
        formula_verified=formula_verified,
        expected_cost_per_generation_usd=expected,
        max_cost_per_generation_usd=maximum,
        checked_on=TODAY,
        source_url="https://example.com/pricing",
        **extra,
    )


def make_candidate(key: str, **caps: Any) -> ModelCandidate:
    categories = caps.pop("categories", ["balanced"])
    pricing = caps.pop("pricing", None) or make_pricing()
    return ModelCandidate(
        key=key,
        name=f"Test model {key}",
        replicate_id=f"test-owner/{key}",
        version=None,
        categories=categories,
        capabilities=Capabilities(seed=True, aspect_ratios=["3:4"], **caps),
        pricing=pricing,
        terms=make_terms(),
    )


@pytest.fixture
def plain_candidate() -> ModelCandidate:
    """Text-to-image only: no references, no editing, no JSON, no negatives."""
    return make_candidate("plain")


@pytest.fixture
def reffy_candidate() -> ModelCandidate:
    """Reference-conditioning and editing capable, FLUX.2-style list params."""
    return make_candidate(
        "reffy",
        categories=["balanced", "reference", "editing"],
        reference_image=True,
        reference_image_param="input_images",
        max_reference_images=4,
        image_editing=True,
        image_editing_param="input_images",
        image_editing_param_is_list=True,
        json_prompting=True,
    )


@pytest.fixture
def negative_candidate() -> ModelCandidate:
    return make_candidate(
        "neg",
        negative_prompt=True,
        negative_prompt_param="negative_prompt",
    )


def make_candidates_config(*candidates: ModelCandidate) -> CandidatesConfig:
    return CandidatesConfig(
        platform_terms=PlatformTerms(
            summary="test",
            commercial_use="test",
            input_retention="test",
            training_use="test",
            sources=["https://example.com"],
            verified_on=TODAY,
        ),
        candidates=list(candidates),
    )


def make_brief(brief_id: str = "test-lehenga", **overrides: Any) -> Brief:
    data: dict[str, Any] = dict(
        id=brief_id,
        garment="lehenga",
        ceremony="baraat",
        region="Punjabi",
        palette="deep red and gold",
        fabric="raw silk",
        embellishment_level="heavy",
        embellishment_techniques=["zardozi"],
        sleeves="full sleeves",
        neckline="round neckline",
        coverage="fully covered silhouette",
        dupatta="draped over the head",
    )
    data.update(overrides)
    return Brief(**data)


def make_refinement_brief(brief_id: str = "test-refine") -> Brief:
    return make_brief(
        brief_id,
        palette="ivory and soft gold",
        refinement=RefinementChange(
            id="ivory-to-red",
            field="palette",
            from_value="ivory and soft gold",
            to_value="deep red and gold",
        ),
    )


def make_stage(**overrides: Any) -> StageConfig:
    data: dict[str, Any] = dict(
        stage="screening",
        candidates_file="unused.yaml",
        briefs_file="unused.yaml",
        models=["plain"],
        brief_ids="all",
        seeds=[11],
        aspect_ratio="3:4",
        inspiration_modes=["text_only"],
        prompt_formats="auto",
    )
    data.update(overrides)
    return StageConfig(**data)


def make_bundle(
    stage: StageConfig,
    candidates: CandidatesConfig,
    briefs: BriefsFile,
    manifest: ReferenceManifest | None = None,
    references_dir: Path | None = None,
) -> LoadedStage:
    manifest = manifest or ReferenceManifest(references=[])
    plan = expand(stage, candidates, briefs, manifest, references_dir)
    return LoadedStage(stage, candidates, briefs, manifest, plan, references_dir)


def make_reference_entry(
    ref_id: str,
    path: str,
    rights_status: str = "verified",
) -> ReferenceEntry:
    return ReferenceEntry(
        id=ref_id,
        path=path,
        rights_status=rights_status,  # type: ignore[arg-type]
        source_name="project-owned test placeholder",
        licence="generated placeholder, no rights encumbrance",
        verified_by="tests",
        verified_on=TODAY,
    )


class MockAdapter:
    """Stands in for ReplicateAdapter. Records every invocation; makes no
    network calls. ``on_create`` lets tests assert preconditions (e.g. that
    the budget reservation already exists) at call time."""

    def __init__(
        self,
        on_create: Callable[[str, str | None, dict[str, Any]], None] | None = None,
        fail_with: ProviderError | None = None,
        fail_map: dict[str, ProviderError] | None = None,
    ):
        self.create_calls: list[tuple[str, str | None, dict[str, Any]]] = []
        self.download_calls: list[str] = []
        self.closed = False
        self.on_create = on_create
        self.fail_with = fail_with
        self.fail_map = fail_map or {}

    def create_prediction(self, replicate_id: str, version: str | None, input_params: dict) -> Prediction:
        if self.on_create is not None:
            self.on_create(replicate_id, version, input_params)
        self.create_calls.append((replicate_id, version, input_params))
        if self.fail_with is not None:
            raise self.fail_with
        if replicate_id in self.fail_map:
            raise self.fail_map[replicate_id]
        return Prediction(
            id=f"pred-{len(self.create_calls)}",
            status="succeeded",
            output="https://example.com/output.png",
            error=None,
            model_version="v-mock",
            raw={},
        )

    def get_prediction(self, prediction_id: str) -> Prediction:  # pragma: no cover
        raise AssertionError("mock predictions are terminal at creation")

    def download(self, url: str, dest: Path, *, max_bytes: int, allowed_mime_prefixes=("image/",)):
        if dest.exists():
            raise ProviderError(f"refusing to overwrite {dest}", before_acceptance=False)
        self.download_calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = tiny_png_bytes()
        dest.write_bytes(payload)
        return "image/png", len(payload)

    def close(self) -> None:
        self.closed = True


LIVE_ENV = {"ALLOW_PROVIDER_CALLS": "true", "REPLICATE_API_TOKEN": "test-token-do-not-log"}


@pytest.fixture
def forbidden_factory() -> Callable:
    """An adapter factory that fails the test if it is ever invoked."""

    def factory(env):  # pragma: no cover - reaching this IS the failure
        raise AssertionError("adapter factory must not be invoked in this scenario")

    return factory
