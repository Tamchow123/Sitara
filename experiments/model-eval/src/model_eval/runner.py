"""Run orchestration: dry runs, gated live runs, resume, and provenance.

Execution order guarantees:

- ``--dry-run`` builds and reports the plan without constructing the provider
  adapter — zero network activity, by construction.
- A live run first checks every gate (ALLOW_PROVIDER_CALLS, token,
  --budget-usd, --confirm-live). If any gate fails, ProviderGateError is
  raised before the adapter factory is ever invoked.
- For each request: verify references -> reserve budget -> call provider ->
  poll -> download & validate output -> write result record -> reconcile.
- Failures provably before provider acceptance release the reservation;
  ambiguous failures conservatively convert it to spend.
- Resume: a request whose result record already exists (succeeded or
  skipped) is not re-sent. Failed attempts are retried and their record
  replaced explicitly.
"""

from __future__ import annotations

import base64
import mimetypes
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

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
from .prompt_matrix import PlannedRequest, RunPlan, expand
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

SKIP_UNVERIFIED_REFERENCE = "reference_rights_not_verified"
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


def load_stage_bundle(config_path: Path) -> LoadedStage:
    stage = load_stage(config_path)
    base = config_path.parent
    candidates = load_candidates((base / stage.candidates_file).resolve())
    briefs = load_briefs((base / stage.briefs_file).resolve())
    if stage.reference_manifest:
        manifest = load_reference_manifest((base / stage.reference_manifest).resolve())
    else:
        manifest = ReferenceManifest(references=[])
    plan = expand(stage, candidates, briefs)
    return LoadedStage(stage, candidates, briefs, manifest, plan)


def plan_summary(bundle: LoadedStage, budget_usd: float | None) -> dict[str, Any]:
    plan = bundle.plan
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
        "skips": [
            {"request_id": r.request_id, "reason": r.skip_reason} for r in plan.skipped
        ],
    }
    return summary


@dataclass
class RunOutcome:
    run_id: str
    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
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
            output_path=output_path,
            output_mime_type=output_mime_type,
            output_sha256=output_sha256,
            pricing_checked_on=str(candidate.pricing.checked_on),
            git_commit=self._git_commit,
        )

    # ------------------------------------------------------------- references

    def _verify_references(self, request: PlannedRequest) -> str | None:
        """Return a skip/fail reason if any reference is unusable."""
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

    def _reference_data_uris(self, request: PlannedRequest, base_dir: Path) -> list[str]:
        uris: list[str] = []
        for ref_id in request.reference_ids:
            entry = self.bundle.manifest.by_id(ref_id)
            path = (base_dir / entry.path).resolve()
            if not path.is_file():
                raise RunnerError(f"reference image file missing: {entry.path}")
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            uris.append(f"data:{mime};base64,{encoded}")
        return uris

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

        # Resume: never re-send anything that already completed or was skipped.
        if self.store.exists(rid):
            existing = self.store.load(rid)
            if existing.status in ("succeeded", "skipped"):
                outcome.resumed.append(rid)
                return
            # A failed record will be retried and explicitly replaced.

        if request.skipped:
            self._save_skip(request, request.skip_reason or "skipped_by_plan", outcome)
            return

        reason = self._verify_references(request)
        if reason:
            self._save_skip(request, reason, outcome)
            return

        input_params = dict(request.input_params)

        if request.reference_ids:
            candidate = self._candidate(request)
            param = candidate.capabilities.reference_image_param
            assert param is not None
            uris = self._reference_data_uris(request, references_dir)
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
            base_uri = self._file_data_uri(self.run_dir / base_record.output_path)
            input_params[edit_param] = (
                [base_uri] if candidate.capabilities.image_editing_param_is_list else base_uri
            )

        # ---- hard budget: conservative reservation BEFORE the provider call.
        try:
            ledger.reserve(rid, request.estimated_max_cost_usd)
        except BudgetExceededError as exc:
            self.log(f"[budget] {exc}")
            outcome.halted_reason = str(exc)
            return

        started_at = utc_now_iso()
        start = time.monotonic()
        try:
            prediction = adapter.create_prediction(
                request.replicate_id, request.model_version, input_params
            )
            prediction = self._await_terminal(adapter, prediction)
        except ProviderError as exc:
            if exc.before_acceptance:
                ledger.release(rid)
                category = "rejected_before_acceptance"
            else:
                ledger.assume_spent(rid, note=str(exc)[:300])
                category = "ambiguous_provider_failure"
            self._save_failure(request, category, str(exc), started_at, start, outcome)
            return

        if prediction.status != "succeeded":
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
            output_rel, mime, digest, size = self._download_output(request, adapter, prediction)
        except (ProviderError, RunnerError) as exc:
            # The generation itself succeeded, so the spend stands.
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
        actual = min(
            candidate.pricing.expected_cost_per_generation_usd,
            request.estimated_max_cost_usd,
        )
        ledger.reconcile(rid, actual)

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
            reconciled_cost_usd=actual,
            output_path=output_rel,
            output_mime_type=mime,
            output_sha256=digest,
        )
        self.store.save(record, allow_replace_failed=True)
        outcome.succeeded.append(rid)
        self.log(f"[ok] {rid} ({latency}s, ~{actual:.4f} USD)")

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
    ) -> tuple[str, str, str, int]:
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
        mime, size = adapter.download(url, dest, max_bytes=MAX_OUTPUT_BYTES)
        digest = sha256_of(dest)
        return str(dest.relative_to(self.run_dir)), mime, digest, size

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
        outcome.failed.append(request.request_id)
        self.log(f"[fail] {request.request_id}: {category}")
