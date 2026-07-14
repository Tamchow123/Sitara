"""Run orchestration: dry runs, gated live runs, crash-safe resume, and
provenance.

Execution order guarantees:

- ``--dry-run`` builds and reports the plan without constructing the provider
  adapter — zero network activity, by construction.
- A live run first checks every gate (ALLOW_PROVIDER_CALLS, token,
  --budget-usd, --confirm-live). If any gate fails, ProviderGateError is
  raised before the adapter factory is ever invoked. A finalist config that
  requests reference_image evaluation with no verified usable references
  fails preflight before any provider work.
- Per request: verify references -> reserve budget -> persist an attempt
  record -> submit -> persist the provider prediction id immediately after
  acceptance -> poll -> download & validate output -> write result record ->
  reconcile.
- Crash-safe resume AFTER the prediction id has been persisted: a request
  with a completed result record is never re-sent; a request whose attempt
  record holds an accepted prediction id is resumed by POLLING that
  prediction; an already-downloaded output file is reused; ledger entries
  are reconciled with attempt state (settled entries are not touched again).
  Duplicate prevention around the provider-acceptance boundary itself is
  BEST-EFFORT, not exactly-once: there is an unavoidable window between
  Replicate accepting a request and the prediction id being written locally,
  and Replicate offers no idempotency mechanism this implementation could
  use to close it. A crash inside that window can, on resume, produce one
  duplicate submission (the budget stays safe: the ambiguous first attempt
  was already conservatively accounted or reserved).
- Failures provably before provider acceptance release the reservation;
  ambiguous failures conservatively convert it to spend. Failed requests are
  FINAL for their run (their spend is already accounted), with ONE
  exception: run-level pre-acceptance halts (402 insufficient credit, 401
  authentication) are safely retryable on a rerun with the same run id —
  their reservation was released, nothing was accepted by the provider, and
  no spend occurred — provided the record carries no provider prediction id
  and no submitted attempt record exists. Every other failure category
  requires deleting the failed record and using a new run id.
- Cost accounting is conservative: verified pricing formulas get an
  input-aware calculation (capped at the reservation); unresolved pricing
  reconciles at the FULL reserved amount. See model_eval.costs.
"""

from __future__ import annotations

import base64
import mimetypes
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image

from .budget import BudgetExceededError, BudgetLedger
from .config import (
    BriefsFile,
    CandidatesConfig,
    ConfigError,
    ReferenceManifest,
    StageConfig,
    load_briefs,
    load_candidates,
    load_reference_manifest,
    load_stage,
)
from .costs import final_cost
from .prompt_matrix import (
    SKIP_UNVERIFIED_REFERENCE,
    PlannedRequest,
    RunPlan,
    expand,
)
from .replicate_client import (
    Prediction,
    ProviderError,
    ProviderGateError,
    ReplicateAdapter,
    live_gate_failures,
)
from .result_store import (
    ResultRecord,
    ResultStore,
    current_git_commit,
    sha256_of,
    utc_now_iso,
)

MAX_OUTPUT_BYTES = 50 * 1024 * 1024
POLL_INTERVAL_S = 2.0
POLL_TIMEOUT_S = 600.0

SKIP_MISSING_BASE_IMAGE = "base_image_unavailable_for_edit"

AdapterFactory = Callable[[Mapping[str, str]], ReplicateAdapter]


class RunnerError(Exception):
    pass


@dataclass
class LoadedStage:
    stage: StageConfig
    candidates: CandidatesConfig
    briefs: BriefsFile
    manifest: ReferenceManifest
    plan: RunPlan
    references_dir: Path | None = None


def load_stage_bundle(config_path: Path) -> LoadedStage:
    stage = load_stage(config_path)
    base = config_path.parent
    candidates = load_candidates((base / stage.candidates_file).resolve())
    briefs = load_briefs((base / stage.briefs_file).resolve())
    references_dir: Path | None = None
    if stage.reference_manifest:
        manifest_path = (base / stage.reference_manifest).resolve()
        manifest = load_reference_manifest(manifest_path)
        references_dir = manifest_path.parent
    else:
        manifest = ReferenceManifest(references=[])
    plan = expand(stage, candidates, briefs, manifest, references_dir)
    return LoadedStage(stage, candidates, briefs, manifest, plan, references_dir)


def plan_summary(bundle: LoadedStage, budget_usd: float | None) -> dict[str, Any]:
    plan = bundle.plan
    warnings: list[str] = []
    if "reference_image" in bundle.stage.inspiration_modes:
        runnable_refs = sum(
            1 for r in plan.runnable if r.inspiration_mode == "reference_image"
        )
        if runnable_refs == 0:
            warnings.append(
                "reference_image mode is requested but 0 runnable reference "
                "requests exist (no verified, usable references in the "
                "manifest). A live finalist run will FAIL preflight until "
                "references are verified; see references/README.md."
            )
    summary: dict[str, Any] = {
        "stage": plan.stage,
        "planned_requests": len(plan.runnable),
        "skipped_requests": len(plan.skipped),
        "models": plan.counts_by("model_key"),
        "prompt_formats": plan.counts_by("prompt_format"),
        "inspiration_modes": plan.counts_by("inspiration_mode"),
        "request_kinds": plan.counts_by("kind"),
        "conservative_max_spend_usd": plan.total_max_cost_usd,
        "budget_usd": budget_usd,
        "within_budget": (budget_usd is None or plan.total_max_cost_usd <= budget_usd),
        "preflight_warnings": warnings,
        "skips": [
            {"request_id": r.request_id, "reason": r.skip_reason} for r in plan.skipped
        ],
    }
    return summary


SKIP_MODEL_DISABLED = "model_disabled_after_provider_rejection"

# Deterministic request rejections: the same invalid model configuration
# would fail identically on every brief, so the model is disabled for the
# rest of the run after its first such rejection.
DETERMINISTIC_REJECTION_CODES = frozenset({400, 404, 422})

# Failure categories that are safe to retry within the SAME run: both are
# run-level halts rejected conclusively BEFORE provider acceptance, so the
# reservation was released and no provider-side work or spend exists. The
# halt message tells the user to fix credit/token and rerun with the same
# run id — this set is what makes that instruction true.
RETRYABLE_FAILURE_CATEGORIES = frozenset(
    {"provider_insufficient_credit", "provider_authentication_failed"}
)


@dataclass
class RunOutcome:
    run_id: str
    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
    previously_failed: list[str] = field(default_factory=list)
    disabled_models: dict[str, str] = field(default_factory=dict)
    halted_reason: str | None = None


class Runner:
    def __init__(
        self,
        bundle: LoadedStage,
        run_id: str,
        outputs_dir: Path,
        *,
        adapter_factory: AdapterFactory,
        env: Mapping[str, str],
        log: Callable[[str], None] = print,
        poll_interval_s: float = POLL_INTERVAL_S,
        poll_timeout_s: float = POLL_TIMEOUT_S,
    ):
        self.bundle = bundle
        self.run_id = run_id
        self.run_dir = outputs_dir / "runs" / run_id
        self.store = ResultStore(self.run_dir)
        self.adapter_factory = adapter_factory
        self.env = env
        self.log = log
        self.poll_interval_s = poll_interval_s
        self.poll_timeout_s = poll_timeout_s
        self._git_commit = current_git_commit(outputs_dir)
        # model_key -> short provider-error summary; set after a
        # deterministic rejection so the same broken configuration is never
        # resubmitted across the rest of the matrix.
        self._disabled_models: dict[str, str] = {}

    # ------------------------------------------------------------------ util

    def _ordered_requests(self) -> list[PlannedRequest]:
        """Bases and fresh refinements first, then edits (which need a base
        image on disk). Order within each group follows the deterministic
        plan order."""
        plan = self.bundle.plan
        non_edit = [r for r in plan.requests if r.kind != "refinement_edit"]
        edits = [r for r in plan.requests if r.kind == "refinement_edit"]
        return non_edit + edits

    def _candidate(self, request: PlannedRequest):
        return self.bundle.candidates.by_key(request.model_key)

    def _record_from_request(
        self,
        request: PlannedRequest,
        *,
        status: str,
        provider_prediction_id: str | None = None,
        model_version: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        latency_seconds: float | None = None,
        error_category: str | None = None,
        error_message: str | None = None,
        reconciled_cost_usd: float | None = None,
        cost_basis: str | None = None,
        output_path: str | None = None,
        output_mime_type: str | None = None,
        output_sha256: str | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> ResultRecord:
        candidate = self._candidate(request)
        return ResultRecord(
            run_id=self.run_id,
            stage=request.stage,
            request_id=request.request_id,
            brief_id=request.brief_id,
            garment=request.garment,
            ceremony=request.ceremony,
            tags=request.tags,
            model_key=request.model_key,
            replicate_id=request.replicate_id,
            model_version=model_version or request.model_version,
            provider_prediction_id=provider_prediction_id,
            prompt_format=request.prompt_format,
            prompt_text=request.prompt_text,
            negative_text=request.negative_text,
            json_payload=request.json_payload,
            inspiration_mode=request.inspiration_mode,
            reference_ids=request.reference_ids,
            kind=request.kind,
            refinement_id=request.refinement_id,
            refinement_strategy=request.refinement_strategy,
            base_request_id=request.base_request_id,
            seed=request.seed,
            input_params=request.input_params,
            aspect_ratio=request.aspect_ratio,
            width=width,
            height=height,
            started_at=started_at,
            completed_at=completed_at,
            latency_seconds=latency_seconds,
            status=status,  # type: ignore[arg-type]
            error_category=error_category,
            error_message=error_message,
            estimated_max_cost_usd=request.estimated_max_cost_usd,
            reconciled_cost_usd=reconciled_cost_usd,
            cost_basis=cost_basis,
            output_path=output_path,
            output_mime_type=output_mime_type,
            output_sha256=output_sha256,
            pricing_checked_on=str(candidate.pricing.checked_on),
            git_commit=self._git_commit,
        )

    # ------------------------------------------------------------- references

    def _verify_references(self, request: PlannedRequest) -> str | None:
        """Runtime double-check of the plan-time validation (defence in
        depth): return a skip reason if any reference is unusable."""
        for ref_id in request.reference_ids:
            try:
                entry = self.bundle.manifest.by_id(ref_id)
            except ConfigError:
                return f"{SKIP_UNVERIFIED_REFERENCE}: unknown reference {ref_id!r}"
            if entry.rights_status != "verified":
                return (
                    f"{SKIP_UNVERIFIED_REFERENCE}: reference {ref_id!r} has "
                    f"rights_status={entry.rights_status!r}"
                )
        return None

    @staticmethod
    def _image_megapixels(path: Path) -> float:
        with Image.open(path) as img:
            w, h = img.size
        return (w * h) / 1_000_000

    def _reference_data_uris(
        self, request: PlannedRequest, base_dir: Path
    ) -> tuple[list[str], float]:
        uris: list[str] = []
        megapixels = 0.0
        for ref_id in request.reference_ids:
            entry = self.bundle.manifest.by_id(ref_id)
            path = (base_dir / entry.path).resolve()
            if not path.is_file():
                raise RunnerError(f"reference image file missing: {entry.path}")
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            uris.append(f"data:{mime};base64,{encoded}")
            megapixels += self._image_megapixels(path)
        return uris, megapixels

    def _file_data_uri(self, path: Path) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

    # ------------------------------------------------------------------- run

    def execute(
        self,
        *,
        dry_run: bool,
        confirm_live: bool,
        budget_usd: float | None,
        references_dir: Path,
    ) -> RunOutcome:
        outcome = RunOutcome(run_id=self.run_id)

        if dry_run:
            # No adapter, no ledger, no network — reporting only.
            return outcome

        failures = live_gate_failures(self.env, confirm_live=confirm_live, budget_usd=budget_usd)
        if failures:
            raise ProviderGateError(
                "live run refused; unmet requirements: " + "; ".join(failures)
            )
        assert budget_usd is not None

        # Preflight: a finalist run whose config requests reference_image
        # evaluation must actually have usable references.
        if (
            self.bundle.stage.stage == "finalist"
            and "reference_image" in self.bundle.stage.inspiration_modes
        ):
            runnable_refs = [
                r for r in self.bundle.plan.runnable
                if r.inspiration_mode == "reference_image"
            ]
            if not runnable_refs:
                raise RunnerError(
                    "preflight failed: this finalist config requests "
                    "reference_image evaluation but no verified, usable "
                    "references are available (see references/README.md). "
                    "Verify references in references/manifest.yaml, or remove "
                    "reference_image from inspiration_modes."
                )

        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.store.write_json(
            "plan.json",
            plan_summary(self.bundle, budget_usd),
        )

        adapter = self.adapter_factory(self.env)
        try:
            with BudgetLedger.open(self.run_dir / "budget_ledger.json", budget_usd) as ledger:
                for request in self._ordered_requests():
                    self._execute_one(request, adapter, ledger, references_dir, outcome)
                    if outcome.halted_reason:
                        break
                # The summary is written only when the loop finishes (or
                # halts) cleanly — its absence marks an interrupted run,
                # which the review commands refuse by default.
                self.store.write_json(
                    "run_summary.json",
                    {
                        "run_id": self.run_id,
                        "succeeded": len(outcome.succeeded),
                        "failed": len(outcome.failed),
                        "skipped": len(outcome.skipped),
                        "resumed": len(outcome.resumed),
                        "previously_failed": len(outcome.previously_failed),
                        "disabled_models": outcome.disabled_models,
                        "halted_reason": outcome.halted_reason,
                        "completed_at": utc_now_iso(),
                    },
                )
        finally:
            adapter.close()
        return outcome

    def _execute_one(
        self,
        request: PlannedRequest,
        adapter: ReplicateAdapter,
        ledger: BudgetLedger,
        references_dir: Path,
        outcome: RunOutcome,
    ) -> None:
        rid = request.request_id

        # Resume: completed/skipped results are never re-sent. Failed results
        # are FINAL for this run — their spend is already accounted — except
        # for pre-acceptance run-level halts (402/401), which are safely
        # retryable under strict conditions (see _is_safely_retryable).
        if self.store.exists(rid):
            existing = self.store.load(rid)
            if existing.status in ("succeeded", "skipped"):
                outcome.resumed.append(rid)
                return
            if not self._is_safely_retryable(existing, ledger):
                outcome.previously_failed.append(rid)
                self.log(
                    f"[resume] {rid}: previously failed "
                    f"({existing.error_category}); not retried "
                    "(delete its result record and use a new run id to retry)"
                )
                return
            self.log(
                f"[retry] {rid}: retrying after {existing.error_category} — "
                "the request was rejected before acceptance and its "
                "reservation was released"
            )
            # Fall through to normal execution; a success replaces the failed
            # record, and a repeated halt replaces it and halts again.

        if request.skipped:
            self._save_skip(request, request.skip_reason or "skipped_by_plan", outcome)
            return

        # Circuit breaker: a model that produced a deterministic provider
        # rejection is disabled — its remaining requests become visible
        # skips instead of repeated invalid submissions.
        if request.model_key in self._disabled_models:
            self._save_skip(
                request,
                f"{SKIP_MODEL_DISABLED}: {self._disabled_models[request.model_key]}",
                outcome,
            )
            return

        reason = self._verify_references(request)
        if reason:
            self._save_skip(request, reason, outcome)
            return

        input_params = dict(request.input_params)
        input_megapixels = 0.0

        if request.reference_ids:
            candidate = self._candidate(request)
            param = candidate.capabilities.reference_image_param
            assert param is not None
            uris, input_megapixels = self._reference_data_uris(request, references_dir)
            input_params[param] = uris if candidate.capabilities.max_reference_images > 1 else uris[0]

        if request.kind == "refinement_edit":
            base_record = (
                self.store.load(request.base_request_id)
                if request.base_request_id and self.store.exists(request.base_request_id)
                else None
            )
            if base_record is None or base_record.status != "succeeded" or not base_record.output_path:
                self._save_skip(request, SKIP_MISSING_BASE_IMAGE, outcome)
                return
            candidate = self._candidate(request)
            edit_param = candidate.capabilities.image_editing_param
            assert edit_param is not None
            base_image = self.run_dir / base_record.output_path
            base_uri = self._file_data_uri(base_image)
            input_params[edit_param] = (
                [base_uri] if candidate.capabilities.image_editing_param_is_list else base_uri
            )
            input_megapixels += self._image_megapixels(base_image)

        # ---- budget: conservative reservation BEFORE the provider call.
        # Resume-aware: an entry already settled by a previous crashed run
        # (reconciled/assumed_spent) is left untouched.
        budget_settled = ledger.status_of(rid) in ("reconciled", "assumed_spent")
        if not budget_settled:
            try:
                ledger.ensure_reserved(rid, request.estimated_max_cost_usd)
            except BudgetExceededError as exc:
                self.log(f"[budget] {exc}")
                outcome.halted_reason = str(exc)
                return

        attempt = self.store.load_attempt(rid) or {}
        prediction_id: str | None = attempt.get("prediction_id")

        started_at = utc_now_iso()
        start = time.monotonic()
        try:
            if prediction_id:
                # Crash-safe resume: the provider already accepted this
                # request. Poll the existing prediction; never resubmit.
                self.log(f"[resume] {rid}: polling accepted prediction {prediction_id}")
                prediction = adapter.get_prediction(prediction_id)
            else:
                # Attempt record persisted BEFORE submission...
                self.store.save_attempt(
                    rid,
                    {"request_id": rid, "state": "reserved", "created_at": started_at},
                )
                # Acceptance boundary: between the provider accepting this
                # request and the save_attempt below persisting its id there
                # is an unavoidable window. A crash inside it leaves an
                # attempt record with no prediction id, and resume will
                # submit again — duplicate prevention here is BEST-EFFORT,
                # not exactly-once (Replicate exposes no idempotency key for
                # prediction creation). The budget remains conservative
                # either way.
                prediction = adapter.create_prediction(
                    request.replicate_id, request.model_version, input_params
                )
                # Persist the prediction id as soon as we hold it, closing
                # the boundary window for every later crash point.
                self.store.save_attempt(
                    rid,
                    {
                        "request_id": rid,
                        "state": "submitted",
                        "prediction_id": prediction.id,
                        "submitted_at": utc_now_iso(),
                    },
                )
            prediction = self._await_terminal(adapter, prediction)
        except ProviderError as exc:
            if not budget_settled:
                if exc.before_acceptance and prediction_id is None:
                    ledger.release(rid)
                else:
                    ledger.assume_spent(rid, note=str(exc)[:300])
            self._handle_provider_error(request, exc, started_at, start, outcome)
            return

        if prediction.status != "succeeded":
            if not budget_settled:
                ledger.assume_spent(rid, note=f"terminal status {prediction.status}")
            self._save_failure(
                request,
                f"prediction_{prediction.status}",
                str(prediction.error or "no error detail"),
                started_at,
                start,
                outcome,
                provider_prediction_id=prediction.id,
                model_version=prediction.model_version,
            )
            return

        try:
            output_rel, mime, digest, width, height = self._download_output(
                request, adapter, prediction
            )
        except (ProviderError, RunnerError) as exc:
            # The generation itself succeeded, so the spend stands.
            if not budget_settled:
                ledger.assume_spent(rid, note=f"output download failed: {str(exc)[:200]}")
            self._save_failure(
                request,
                "output_download_failed",
                str(exc),
                started_at,
                start,
                outcome,
                provider_prediction_id=prediction.id,
                model_version=prediction.model_version,
            )
            return

        candidate = self._candidate(request)
        output_megapixels = (width * height) / 1_000_000 if width and height else 1.0
        accounted, basis = final_cost(
            candidate.pricing,
            reserved_usd=request.estimated_max_cost_usd,
            output_megapixels=output_megapixels,
            input_megapixels=input_megapixels,
        )
        if not budget_settled:
            ledger.reconcile(rid, accounted)
        else:
            # Crash happened after reconciliation: trust the settled ledger.
            entry = ledger.entry(rid)
            if entry is not None and entry.final_usd is not None:
                accounted = entry.final_usd

        completed_at = utc_now_iso()
        latency = round(time.monotonic() - start, 3)
        record = self._record_from_request(
            request,
            status="succeeded",
            provider_prediction_id=prediction.id,
            model_version=prediction.model_version or request.model_version,
            started_at=started_at,
            completed_at=completed_at,
            latency_seconds=latency,
            reconciled_cost_usd=accounted,
            cost_basis=basis,
            output_path=output_rel,
            output_mime_type=mime,
            output_sha256=digest,
            width=width,
            height=height,
        )
        # allow_replace_failed: a successful retry of a pre-acceptance halt
        # supersedes its failed record (succeeded records stay protected).
        self.store.save(record, allow_replace_failed=True)
        self.store.clear_attempt(rid)
        outcome.succeeded.append(rid)
        self.log(f"[ok] {rid} ({latency}s, accounted {accounted:.4f} USD [{basis}])")

    def _is_safely_retryable(self, existing: ResultRecord, ledger: BudgetLedger) -> bool:
        """A failed result may be retried in the same run ONLY when the
        original failure was a pre-acceptance run-level halt (402/401), the
        provider never accepted anything (no prediction id on the record and
        no submitted attempt record), and its ledger entry is released —
        i.e. retrying cannot duplicate provider-side work or spend."""
        if existing.error_category not in RETRYABLE_FAILURE_CATEGORIES:
            return False
        if existing.provider_prediction_id:
            return False
        attempt = self.store.load_attempt(existing.request_id)
        if attempt and attempt.get("prediction_id"):
            return False
        return ledger.status_of(existing.request_id) == "released"

    def _handle_provider_error(
        self,
        request: PlannedRequest,
        exc: ProviderError,
        started_at: str,
        start: float,
        outcome: RunOutcome,
    ) -> None:
        """Classify a provider failure, record it, and decide whether the run
        halts (402/401) or the model is circuit-broken (deterministic 4xx)."""
        detail_parts = [p for p in (exc.provider_title, exc.provider_detail) if p]
        detail = " — ".join(detail_parts) if detail_parts else str(exc)
        if exc.status_code is not None:
            detail = f"[{exc.status_code}] {detail}"

        if exc.status_code == 402:
            category = "provider_insufficient_credit"
            outcome.halted_reason = (
                "provider reported insufficient credit (402). Top up at "
                "https://replicate.com/account/billing, wait a few minutes, "
                "then rerun with the SAME run id — completed requests are "
                "never re-sent or re-charged."
            )
        elif exc.status_code == 401:
            category = "provider_authentication_failed"
            outcome.halted_reason = (
                "provider rejected the API token (401). Check "
                "REPLICATE_API_TOKEN and rerun with the same run id."
            )
        elif exc.before_acceptance:
            category = "rejected_before_acceptance"
        else:
            category = "ambiguous_provider_failure"

        self._save_failure(request, category, detail, started_at, start, outcome)

        if (
            outcome.halted_reason is None
            and exc.before_acceptance
            and exc.status_code in DETERMINISTIC_REJECTION_CODES
            and request.model_key not in self._disabled_models
        ):
            short = detail[:200]
            self._disabled_models[request.model_key] = short
            outcome.disabled_models[request.model_key] = short
            self.log(
                f"[circuit] {request.model_key} disabled for the rest of the "
                f"run after a deterministic provider rejection: {short}"
            )
        if outcome.halted_reason:
            self.log(f"[halt] {outcome.halted_reason}")

    def _await_terminal(self, adapter: ReplicateAdapter, prediction: Prediction) -> Prediction:
        deadline = time.monotonic() + self.poll_timeout_s
        current = prediction
        while current.status not in ("succeeded", "failed", "canceled"):
            if time.monotonic() > deadline:
                raise ProviderError(
                    f"prediction {current.id} did not finish within "
                    f"{self.poll_timeout_s}s",
                    before_acceptance=False,
                )
            time.sleep(self.poll_interval_s)
            current = adapter.get_prediction(current.id)
        return current

    def _download_output(
        self,
        request: PlannedRequest,
        adapter: ReplicateAdapter,
        prediction: Prediction,
    ) -> tuple[str, str, str, int, int]:
        output = prediction.output
        url: str | None = None
        if isinstance(output, str):
            url = output
        elif isinstance(output, list) and output and isinstance(output[0], str):
            url = output[0]
        if not url or not url.startswith("https://"):
            raise RunnerError(
                f"prediction {prediction.id} returned no usable https output URL"
            )
        extension = Path(url.split("?")[0]).suffix or ".png"
        dest = self.store.image_path(request.request_id, extension)
        if dest.exists():
            # Crash-safe resume: the output was already downloaded before the
            # crash; reuse it rather than re-downloading or failing.
            self.log(f"[resume] {request.request_id}: reusing downloaded output")
            mime = mimetypes.guess_type(dest.name)[0] or "image/png"
        else:
            mime, _size = adapter.download(url, dest, max_bytes=MAX_OUTPUT_BYTES)
        digest = sha256_of(dest)
        with Image.open(dest) as img:
            width, height = img.size
        return str(dest.relative_to(self.run_dir)), mime, digest, width, height

    # ---------------------------------------------------------------- records

    def _save_skip(self, request: PlannedRequest, reason: str, outcome: RunOutcome) -> None:
        record = self._record_from_request(
            request,
            status="skipped",
            error_category="skipped",
            error_message=reason,
        )
        if not self.store.exists(request.request_id):
            self.store.save(record)
        outcome.skipped.append(request.request_id)
        self.log(f"[skip] {request.request_id}: {reason}")

    def _save_failure(
        self,
        request: PlannedRequest,
        category: str,
        message: str,
        started_at: str,
        start_monotonic: float,
        outcome: RunOutcome,
        *,
        provider_prediction_id: str | None = None,
        model_version: str | None = None,
    ) -> None:
        record = self._record_from_request(
            request,
            status="failed",
            provider_prediction_id=provider_prediction_id,
            model_version=model_version,
            started_at=started_at,
            completed_at=utc_now_iso(),
            latency_seconds=round(time.monotonic() - start_monotonic, 3),
            error_category=category,
            error_message=message[:1000],
        )
        self.store.save(record, allow_replace_failed=True)
        self.store.clear_attempt(request.request_id)
        outcome.failed.append(request.request_id)
        self.log(f"[fail] {request.request_id}: {category}")
