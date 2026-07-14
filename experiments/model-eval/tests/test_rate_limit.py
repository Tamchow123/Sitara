"""Replicate 429 rate-limit handling.

A 429 from prediction CREATION is a confirmed pre-acceptance rejection (no
prediction exists, nothing can be charged): it is retried in place with the
provider's hint or bounded backoff, the reservation stays put, and exhausted
retries release the reservation and halt the run (retryable with the same
run id). A 429 while POLLING an accepted prediction retries polling only —
never a resubmission, never a release. Sleeps are injected so no test
actually waits."""

import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from conftest import (
    LIVE_ENV,
    MockAdapter,
    make_brief,
    make_bundle,
    make_candidates_config,
    make_stage,
)
from model_eval.config import BriefsFile
from model_eval.replicate_client import Prediction, ProviderError, ReplicateAdapter
from model_eval.runner import (
    RATE_LIMIT_MAX_RETRIES,
    RATE_LIMIT_MAX_TOTAL_WAIT_S,
    Runner,
)

THROTTLE_DETAIL = (
    "Request was throttled. Your rate limit for creating predictions is "
    "reduced to 6 requests per minute with a burst of 1 requests while you "
    "have less than $5.0 in credit. Your rate limit resets in ~5s."
)


def throttle_error(retry_after_s: float | None) -> ProviderError:
    return ProviderError(
        f"provider throttled request (429): {THROTTLE_DETAIL}",
        before_acceptance=True,
        status_code=429,
        provider_title="Request was throttled",
        provider_detail=THROTTLE_DETAIL,
        retry_after_s=retry_after_s,
    )


def polling_throttle_error(retry_after_s: float | None) -> ProviderError:
    return ProviderError(
        "provider throttled polling (429): throttled",
        before_acceptance=False,
        status_code=429,
        provider_title="Request was throttled",
        provider_detail=THROTTLE_DETAIL,
        retry_after_s=retry_after_s,
    )


class ThrottleNTimes(MockAdapter):
    """First ``n`` creation attempts are throttled; the next succeeds."""

    def __init__(self, n: int, retry_after_s: float | None = 5.0, **kwargs):
        super().__init__(**kwargs)
        self.n = n
        self.retry_after_s = retry_after_s

    def create_prediction(self, replicate_id, version, input_params):
        if len(self.create_calls) < self.n:
            self.create_calls.append((replicate_id, version, input_params))
            raise throttle_error(self.retry_after_s)
        return super().create_prediction(replicate_id, version, input_params)


class AlwaysThrottled(MockAdapter):
    def __init__(self, retry_after_s: float | None = 5.0, **kwargs):
        super().__init__(**kwargs)
        self.retry_after_s = retry_after_s

    def create_prediction(self, replicate_id, version, input_params):
        self.create_calls.append((replicate_id, version, input_params))
        raise throttle_error(self.retry_after_s)


class SucceedThenAlwaysThrottled(MockAdapter):
    def create_prediction(self, replicate_id, version, input_params):
        if self.create_calls:
            self.create_calls.append((replicate_id, version, input_params))
            raise throttle_error(5.0)
        return super().create_prediction(replicate_id, version, input_params)


def bundle_of(candidate, brief_count=1):
    return make_bundle(
        make_stage(prompt_formats=["editorial"]),
        make_candidates_config(candidate),
        BriefsFile(briefs=[make_brief(f"brief-{i}") for i in range(1, brief_count + 1)]),
    )


def rl_runner(tmp_path, bundle, adapter, sleeps, logs=None, run_id="rl-run"):
    return Runner(
        bundle,
        run_id,
        tmp_path / "outputs",
        adapter_factory=lambda _env: adapter,
        env=LIVE_ENV,
        log=(logs.append if logs is not None else (lambda m: None)),
        poll_interval_s=0.0,
        poll_timeout_s=5.0,
        sleep=sleeps.append,
    )


def run(runner, tmp_path):
    return runner.execute(
        dry_run=False, confirm_live=True, budget_usd=10.0, references_dir=tmp_path
    )


def ledger_of(runner):
    return json.loads((runner.run_dir / "budget_ledger.json").read_text(encoding="utf-8"))


class TestAdapterClassification:
    def _create_429(self, body: str, headers: dict | None = None, token="test-token"):
        response_headers = {"content-type": "application/json", **(headers or {})}
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(429, content=body.encode(), headers=response_headers)
            )
        )
        adapter = ReplicateAdapter(token, client=client)
        with pytest.raises(ProviderError) as excinfo:
            adapter.create_prediction("owner/model", None, {"prompt": "x"})
        return excinfo.value

    def test_creation_429_is_conclusively_pre_acceptance(self):
        exc = self._create_429(json.dumps({"title": "Request was throttled", "detail": THROTTLE_DETAIL, "status": 429}))
        assert exc.status_code == 429
        assert exc.before_acceptance is True
        assert "throttled" in (exc.provider_title or "").lower()

    def test_retry_after_seconds_header_is_preferred(self):
        exc = self._create_429(
            json.dumps({"title": "t", "detail": THROTTLE_DETAIL, "status": 429}),
            headers={"retry-after": "7"},
        )
        assert exc.retry_after_s == pytest.approx(7.0)

    def test_retry_after_http_date_is_supported(self):
        when = datetime.now(timezone.utc) + timedelta(seconds=30)
        exc = self._create_429(
            json.dumps({"title": "t", "detail": "no hint here", "status": 429}),
            headers={"retry-after": format_datetime(when, usegmt=True)},
        )
        assert exc.retry_after_s is not None
        assert 0.0 <= exc.retry_after_s <= 31.0

    def test_resets_hint_is_used_when_header_absent(self):
        exc = self._create_429(json.dumps({"title": "t", "detail": THROTTLE_DETAIL, "status": 429}))
        assert exc.retry_after_s == pytest.approx(5.0)

    def test_malformed_header_and_hint_fall_back_to_none(self):
        exc = self._create_429(
            json.dumps({"title": "t", "detail": "try again eventually", "status": 429}),
            headers={"retry-after": "soonish"},
        )
        assert exc.retry_after_s is None

    def test_polling_429_is_not_pre_acceptance(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    429,
                    content=json.dumps({"title": "t", "detail": THROTTLE_DETAIL, "status": 429}).encode(),
                    headers={"content-type": "application/json"},
                )
            )
        )
        adapter = ReplicateAdapter("test-token", client=client)
        with pytest.raises(ProviderError) as excinfo:
            adapter.get_prediction("pred-123")
        assert excinfo.value.status_code == 429
        assert excinfo.value.before_acceptance is False
        assert excinfo.value.retry_after_s == pytest.approx(5.0)

    def test_throttle_fields_are_token_redacted(self):
        token = "r8_rate_limit_token"
        body = json.dumps({"title": "t", "detail": f"limited; auth was Bearer {token}", "status": 429})
        exc = self._create_429(body, token=token)
        assert token not in str(exc)
        assert token not in (exc.provider_detail or "")


class TestCreationRetries:
    def test_one_throttle_then_success(self, tmp_path, plain_candidate):
        adapter = ThrottleNTimes(1, retry_after_s=5.0)
        sleeps: list[float] = []
        logs: list[str] = []
        runner = rl_runner(tmp_path, bundle_of(plain_candidate), adapter, sleeps, logs)
        outcome = run(runner, tmp_path)

        assert len(outcome.succeeded) == 1 and outcome.failed == []
        # Two creation attempts, exactly ONE accepted prediction.
        assert len(adapter.create_calls) == 2
        record = runner.store.load(outcome.succeeded[0])
        assert record.provider_prediction_id == "pred-2"
        # Retry-After honoured plus safety margin; retry metadata recorded.
        rate_waits = [s for s in sleeps if s > 0]
        assert rate_waits == [pytest.approx(6.0)]
        assert record.provider_create_retries == 1
        assert record.rate_limit_wait_seconds == pytest.approx(6.0)
        assert any("[rate-limit]" in line and "retrying in 6.0s" in line for line in logs)
        assert all("r8_" not in line for line in logs)
        # The reservation survived the retry and reconciled normally.
        entries = ledger_of(runner)["entries"]
        assert next(iter(entries.values()))["status"] == "reconciled"

    def test_multiple_throttles_then_success(self, tmp_path, plain_candidate):
        adapter = ThrottleNTimes(3, retry_after_s=2.0)
        sleeps: list[float] = []
        runner = rl_runner(tmp_path, bundle_of(plain_candidate), adapter, sleeps)
        outcome = run(runner, tmp_path)
        assert len(outcome.succeeded) == 1
        assert len(adapter.create_calls) == 4
        record = runner.store.load(outcome.succeeded[0])
        assert record.provider_create_retries == 3
        assert record.rate_limit_wait_seconds == pytest.approx(9.0)  # 3 x (2+1)

    def test_backoff_fallback_when_no_hint_exists(self, tmp_path, plain_candidate):
        adapter = ThrottleNTimes(2, retry_after_s=None)
        sleeps: list[float] = []
        runner = rl_runner(tmp_path, bundle_of(plain_candidate), adapter, sleeps)
        run(runner, tmp_path)
        rate_waits = [s for s in sleeps if s > 0]
        # Bounded exponential backoff (2, 4) plus the 1s safety margin.
        assert rate_waits == [pytest.approx(3.0), pytest.approx(5.0)]

    def test_no_prediction_id_is_recorded_until_creation_succeeds(self, tmp_path, plain_candidate):
        attempts_seen: list[dict | None] = []
        runner_ref: dict = {}

        class Watching(ThrottleNTimes):
            def create_prediction(self, *args):
                attempts_seen.append(runner_ref["runner"].store.load_attempt(rid_ref["rid"]))
                return super().create_prediction(*args)

        bundle = bundle_of(plain_candidate)
        rid_ref = {"rid": bundle.plan.runnable[0].request_id}
        adapter = Watching(2, retry_after_s=1.0)
        sleeps: list[float] = []
        runner = rl_runner(tmp_path, bundle, adapter, sleeps)
        runner_ref["runner"] = runner
        run(runner, tmp_path)
        assert len(attempts_seen) == 3
        for attempt in attempts_seen:
            assert attempt is not None and attempt["state"] == "reserved"
            assert "prediction_id" not in attempt

    def test_total_wait_budget_caps_retries(self, tmp_path, plain_candidate):
        # Each wait is capped at 60s; the total cap (180s) aborts before the
        # per-count limit is reached.
        adapter = AlwaysThrottled(retry_after_s=100.0)
        sleeps: list[float] = []
        runner = rl_runner(tmp_path, bundle_of(plain_candidate), adapter, sleeps)
        outcome = run(runner, tmp_path)
        rate_waits = [s for s in sleeps if s > 0]
        assert rate_waits == [pytest.approx(60.0)] * 3
        assert sum(rate_waits) <= RATE_LIMIT_MAX_TOTAL_WAIT_S
        assert outcome.halted_reason


class TestExhaustedThrottles:
    def test_exhausted_retries_release_and_halt(self, tmp_path, plain_candidate):
        adapter = AlwaysThrottled(retry_after_s=1.0)
        sleeps: list[float] = []
        runner = rl_runner(tmp_path, bundle_of(plain_candidate, brief_count=3), adapter, sleeps)
        outcome = run(runner, tmp_path)

        # Bounded: initial attempt + RATE_LIMIT_MAX_RETRIES, then no later
        # matrix request is submitted.
        assert len(adapter.create_calls) == RATE_LIMIT_MAX_RETRIES + 1
        assert outcome.halted_reason and "rate limit" in outcome.halted_reason.lower()
        assert len(outcome.failed) == 1
        record = runner.store.load(outcome.failed[0])
        assert record.error_category == "provider_rate_limited"
        assert "429" in (record.error_message or "")
        # Never assumed spend: the reservation is released.
        entries = ledger_of(runner)["entries"]
        assert len(entries) == 1
        entry = next(iter(entries.values()))
        assert entry["status"] == "released"
        assert ledger_of(runner)["totals"]["spent_usd"] == 0

    def test_exhausted_throttle_is_retryable_with_the_same_run_id(self, tmp_path, plain_candidate):
        first = SucceedThenAlwaysThrottled()
        sleeps: list[float] = []
        runner1 = rl_runner(tmp_path, bundle_of(plain_candidate, brief_count=3), first, sleeps)
        outcome1 = run(runner1, tmp_path)
        assert len(outcome1.succeeded) == 1 and len(outcome1.failed) == 1
        assert outcome1.halted_reason

        good = MockAdapter()
        sleeps2: list[float] = []
        runner2 = rl_runner(tmp_path, bundle_of(plain_candidate, brief_count=3), good, sleeps2)
        outcome2 = run(runner2, tmp_path)
        # Prior success is not re-sent; the throttled and unattempted
        # requests both run now.
        assert len(good.create_calls) == 2
        assert outcome2.resumed == outcome1.succeeded
        assert outcome2.failed == [] and outcome2.halted_reason is None
        records = runner2.store.load_all()
        assert sorted(r.status for r in records) == ["succeeded"] * 3
        totals = ledger_of(runner2)["totals"]
        assert totals["spent_usd"] == pytest.approx(3 * 0.04)  # counted once


class TestPollingThrottles:
    class PollingThrottled(MockAdapter):
        def __init__(self):
            super().__init__()
            self.get_calls: list[str] = []

        def create_prediction(self, replicate_id, version, input_params):
            self.create_calls.append((replicate_id, version, input_params))
            return Prediction(
                id="pred-accepted",
                status="processing",
                output=None,
                error=None,
                model_version="v-mock",
                raw={},
            )

        def get_prediction(self, prediction_id):
            self.get_calls.append(prediction_id)
            if len(self.get_calls) == 1:
                raise polling_throttle_error(1.0)
            return Prediction(
                id=prediction_id,
                status="succeeded",
                output="https://example.com/output.png",
                error=None,
                model_version="v-mock",
                raw={},
            )

    def test_polling_429_retries_polling_and_never_resubmits(self, tmp_path, plain_candidate):
        adapter = self.PollingThrottled()
        sleeps: list[float] = []
        runner = rl_runner(tmp_path, bundle_of(plain_candidate), adapter, sleeps)
        outcome = run(runner, tmp_path)

        assert len(adapter.create_calls) == 1, "polling throttles must never resubmit creation"
        assert adapter.get_calls == ["pred-accepted", "pred-accepted"]
        assert len(outcome.succeeded) == 1
        rate_waits = [s for s in sleeps if s > 0]
        assert rate_waits == [pytest.approx(2.0)]  # 1s hint + 1s margin
        # The reservation was never released mid-flight; it reconciled.
        entry = next(iter(ledger_of(runner)["entries"].values()))
        assert entry["status"] == "reconciled"
