"""Design test fixtures.

Design tests that create inspiration assets need the same isolated in-memory
storage the catalogue tests use (CI has no MinIO, and no test may touch a
real bucket). This fixture is opt-in — requested only by the tests that
ingest inspiration images."""

import copy

import pytest


@pytest.fixture
def inmemory_storage(settings):
    storages = copy.deepcopy(settings.STORAGES)
    storages["default"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
    settings.STORAGES = storages


@pytest.fixture(autouse=True)
def live_mode_by_default(settings):
    """Most generation/refinement HTTP tests exercise the LIVE pipeline
    (mocking ``generation_is_available``) and predate Phase 15's demo/live
    mode precedence — default DEMO_MODE to False here so
    ``enqueue_design_generation``/``enqueue_design_refinement`` evaluate
    live readiness (and therefore an existing mocked
    ``generation_is_available``) exactly as before. A demo-specific test
    sets ``settings.DEMO_MODE = True`` explicitly."""
    settings.DEMO_MODE = False


@pytest.fixture(autouse=True)
def live_admission_ready(settings):
    """Phase 16 gated the generate/refine endpoints behind live admission
    (LIVE_GENERATION_ENABLED + session/IP/count throttles + budget preflight).
    Give every design HTTP test a passing baseline — flag on, generous limits,
    valid cost config and an in-memory budget ledger (no Redis/network) — so the
    pre-Phase-16 success/conflict tests behave exactly as before. An admission-
    specific test tightens a limit or flips a flag explicitly. Harmless for
    non-generation tests (the ledger is only touched on a live enqueue)."""
    from sitara.generation import cost_control
    from sitara.generation.tests.cost_fakes import InMemoryBudgetLedger

    settings.LIVE_GENERATION_ENABLED = True
    settings.LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = 1_000_000_000
    settings.LIVE_GENERATION_PRICING_PROFILE = "test-profile-1"
    settings.LIVE_GENERATION_DAILY_COUNT_LIMIT = 1_000_000
    settings.LIVE_GENERATION_SESSION_LIMIT = 1_000_000
    settings.LIVE_GENERATION_IP_LIMIT = 1_000_000
    cost_control.set_ledger(InMemoryBudgetLedger())
    yield
    cost_control.reset_ledger()
