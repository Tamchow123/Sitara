"""Generation test fixtures.

Tests that create inspiration assets need the isolated in-memory storage the
catalogue uses (CI has no MinIO). Opt-in via the ``inmemory_storage`` fixture.
A network guard makes any accidental socket connection fail loudly."""

import copy
import socket

import pytest


@pytest.fixture
def inmemory_storage(settings):
    storages = copy.deepcopy(settings.STORAGES)
    storages["default"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
    settings.STORAGES = storages


@pytest.fixture(autouse=True)
def inmemory_design_image_storage(settings):
    """Every generation test resolves the ``design_images`` alias (Phase 11
    permanent ingest) to isolated in-memory storage — CI has no MinIO, and the
    network guard below makes any accidental S3 construction fail loudly."""
    storages = copy.deepcopy(settings.STORAGES)
    storages["design_images"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
    settings.STORAGES = storages


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def guard(*args, **kwargs):
        raise AssertionError("network access attempted during a generation test")

    monkeypatch.setattr(socket.socket, "connect", guard)


@pytest.fixture(autouse=True)
def in_memory_budget_ledger(settings):
    """Install a provider-free in-memory budget ledger for every generation test
    (the real ledger talks to Redis, which the ``no_network`` guard blocks). A
    generous default ceiling and a valid pricing profile let live-path fixture
    providers run exactly as before; a cost-specific test overrides the ceiling,
    prices or ledger to assert accounting behaviour. The real Redis concurrency
    proof lives in its own module that overrides this fixture."""
    from sitara.generation import cost_control
    from sitara.generation.tests.cost_fakes import InMemoryBudgetLedger

    settings.LIVE_GENERATION_DAILY_BUDGET_MICRO_USD = 1_000_000_000
    settings.LIVE_GENERATION_PRICING_PROFILE = "test-profile-1"
    # A valid live profile needs a POSITIVE price for every billable stage (a zero
    # price fails closed); set generous defaults so live-path fixture providers run
    # exactly as before. A cost-specific test overrides these.
    settings.ANTHROPIC_INPUT_MICRO_USD_PER_MTOK = 3_000_000
    settings.ANTHROPIC_OUTPUT_MICRO_USD_PER_MTOK = 15_000_000
    settings.REPLICATE_MAX_IMAGE_MICRO_USD = 40_000
    # Generous admission allowances so live-path enqueue tests are not blocked by
    # the count/throttle limits; a limits-specific test overrides these.
    settings.LIVE_GENERATION_DAILY_COUNT_LIMIT = 1_000_000
    ledger = InMemoryBudgetLedger()
    cost_control.set_ledger(ledger)
    yield ledger
    cost_control.reset_ledger()


@pytest.fixture(autouse=True)
def live_mode_by_default(settings):
    """Most generation tests exercise the LIVE pipeline (mocking
    ``generation_is_available``/injecting fixture providers) and predate
    Phase 15's demo/live mode precedence — default DEMO_MODE to False here
    so ``enqueue_design_generation``/``enqueue_design_refinement`` evaluate
    live readiness (and therefore an existing mocked
    ``generation_is_available``) exactly as before. A demo-specific test
    sets ``settings.DEMO_MODE = True`` explicitly."""
    settings.DEMO_MODE = False
