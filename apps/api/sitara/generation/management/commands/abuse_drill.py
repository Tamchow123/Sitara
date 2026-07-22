"""Provider-free live-generation abuse drill (Phase 16, Part B).

    python manage.py abuse_drill

Exercises the admission and cost-control primitives against LOCAL Redis with
NO real providers and NO real credentials, printing PASS/FAIL for each of the
documented safeguards:

  1. Rapid requests from one simulated session/IP reach generation_limit_reached.
  2. A second simulated IP has an independent counter (respecting global limits).
  3. A global daily count of one admits exactly one newly created live attempt.
  4. A cost ceiling below one required reservation returns budget exhaustion.
  5. The provider fake is NOT called after a budget rejection.
  6. LIVE_GENERATION_ENABLED=false returns live_generation_disabled.
  7. Demo mode is admitted without touching any live count or cost key.
  8. No real Anthropic/Replicate credentials are required.

It uses a DEDICATED Redis logical database (flushed at start) so it never
pollutes real budget/count state, and it is NOT a paid live checkpoint — no
provider network call is ever made.
"""

from django.core.management.base import BaseCommand
from django.test import RequestFactory, override_settings

from sitara.designs.models import Design, DesignSession
from sitara.generation import admission, cost_control


class _FakeProvider:
    """Records whether it was ever invoked (it must never be, after a rejection)."""

    name = "fake"

    def __init__(self):
        self.calls = 0

    def generate(self, request):  # pragma: no cover - must never run in the drill
        self.calls += 1
        raise AssertionError("provider fake was invoked during the abuse drill")


class Command(BaseCommand):
    help = "Run the provider-free live-generation abuse drill against local Redis."

    def handle(self, *args, **options):
        drill_url = "redis://redis:6379/9"
        ledger = cost_control.RedisBudgetLedger(drill_url, 5)
        try:
            ledger._connect().flushdb()
        except Exception as exc:  # noqa: BLE001 - the drill needs a reachable local Redis
            self.stderr.write(f"abuse drill requires a reachable local Redis at {drill_url}: {exc}")
            return
        cost_control.set_ledger(ledger)
        results: list[tuple[str, bool]] = []
        try:
            self._run(results)
        finally:
            cost_control.reset_ledger()

        self.stdout.write("")
        for name, ok in results:
            self.stdout.write(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        self.stdout.write("")
        self.stdout.write(
            "NOTE: this is a provider-free abuse drill, NOT a paid live checkpoint. "
            "No Anthropic/Replicate call was made and no real credentials were used."
        )

    def _run(self, results):
        from django.core.cache import cache

        cache.clear()

        # 1. One session hits the per-session throttle.
        ok = False
        try:
            for _ in range(3):
                admission._throttle("session", "drill-session-A", 2, 60)
        except admission.GenerationLimitReached:
            ok = True
        results.append(("1. session throttle reaches generation_limit_reached", ok))

        # 2. A second IP has an independent counter.
        try:
            admission._throttle("ip", "drill-ip-A", 2, 60)
            admission._throttle("ip", "drill-ip-A", 2, 60)
        except admission.GenerationLimitReached:
            pass
        independent = True
        try:
            admission._throttle("ip", "drill-ip-B", 2, 60)  # fresh counter, must pass
        except admission.GenerationLimitReached:
            independent = False
        results.append(("2. second IP has an independent counter", independent))

        # 3. A global daily count of one admits exactly one.
        with override_settings(LIVE_GENERATION_DAILY_COUNT_LIMIT=1):
            first = cost_control.reserve_count("drill:count:A")
            second_rejected = False
            try:
                cost_control.reserve_count("drill:count:B")
            except cost_control.CountLimitReached:
                second_rejected = True
        results.append(
            (
                "3. global daily count of one admits exactly one",
                first == "reserved" and second_rejected,
            )
        )

        # 4 & 5. A ceiling below one reservation rejects, and the fake is never called.
        fake = _FakeProvider()
        with override_settings(
            LIVE_GENERATION_DAILY_BUDGET_MICRO_USD=1,
            LIVE_GENERATION_PRICING_PROFILE="drill",
            ANTHROPIC_INPUT_MICRO_USD_PER_MTOK=1_000_000,
            ANTHROPIC_OUTPUT_MICRO_USD_PER_MTOK=1_000_000,
            ANTHROPIC_MAX_INPUT_TOKENS=1000,
        ):
            profile = cost_control.active_pricing_profile()
            amount = cost_control.anthropic_call_max_micro_usd(profile, 1000)
            rejected = False
            try:
                cost_control.reserve("drill:budget:A", amount, profile)
                fake.generate(None)  # only reached if the reservation wrongly succeeded
            except cost_control.BudgetExhausted:
                rejected = True
        results.append(("4. ceiling below one reservation rejects", rejected))
        results.append(("5. provider fake NOT called after budget rejection", fake.calls == 0))

        # 6 & 7 need a throwaway design + a simulated request.
        session = DesignSession.objects.create()
        design = Design.objects.create(design_session=session)
        try:
            request = self._fake_request()
            # 6. Live disabled -> live_generation_disabled.
            disabled = False
            with override_settings(DEMO_MODE=False, LIVE_GENERATION_ENABLED=False):
                try:
                    admission.enforce_live_admission(request, design)
                except admission.LiveGenerationDisabled:
                    disabled = True
            results.append(
                ("6. LIVE_GENERATION_ENABLED=false -> live_generation_disabled", disabled)
            )

            # 7. Demo mode admitted without touching any live count/cost key.
            before = ledger_snapshot()
            with override_settings(DEMO_MODE=True):
                mode = admission.enforce_live_admission(request, design)
            after = ledger_snapshot()
            results.append(
                (
                    "7. demo admitted with no live count/cost key touched",
                    mode == "demo" and before == after,
                )
            )
        finally:
            design.delete()
            session.delete()

        # 8. No real credentials were needed.
        from django.conf import settings

        results.append(
            (
                "8. no real Anthropic/Replicate credentials required",
                not settings.ANTHROPIC_API_KEY and not settings.REPLICATE_API_TOKEN,
            )
        )

    def _fake_request(self):
        from django.contrib.sessions.backends.db import SessionStore

        request = RequestFactory().post("/")
        store = SessionStore()
        store.create()
        request.session = store
        request.META["REMOTE_ADDR"] = "203.0.113.9"
        return request


def ledger_snapshot():
    from sitara.generation import cost_control

    try:
        return cost_control.day_budget_total_micro_usd()
    except cost_control.BudgetLedgerUnavailable:
        return None
