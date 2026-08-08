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
def inmemory_design_image_storage(settings):
    """Resolve the ``design_images`` alias to in-memory storage for EVERY test in
    this package. Mirrors ``generation/tests/conftest.py``.

    Autouse and unconditional, deliberately. ``inmemory_storage`` above covers
    only the ``default`` alias and is opt-in, but
    ``create_ready_design_version`` writes through ``design_images`` whenever
    ``with_storage_objects`` is left at its default of True — so a test that
    simply uses the helper reaches the configured S3 backend unless something
    stops it. That passes on a developer machine running MinIO and fails in CI,
    which has none: it is exactly how the Phase 19 retention-purge tests broke,
    and the reason three separate modules here had each grown their own private
    copy of this override. One fixture at the package boundary means a NEW test
    module cannot repeat it, which an opt-in fixture could not promise.

    Nothing in this package deliberately exercises the real configured backend.
    A test that ever needs to must override this explicitly and say why."""
    configured = copy.deepcopy(settings.STORAGES)
    configured["design_images"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
    settings.STORAGES = configured


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
