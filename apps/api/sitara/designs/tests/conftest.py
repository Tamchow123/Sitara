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
