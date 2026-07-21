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
def live_mode_by_default(settings):
    """Most generation tests exercise the LIVE pipeline (mocking
    ``generation_is_available``/injecting fixture providers) and predate
    Phase 15's demo/live mode precedence — default DEMO_MODE to False here
    so ``enqueue_design_generation``/``enqueue_design_refinement`` evaluate
    live readiness (and therefore an existing mocked
    ``generation_is_available``) exactly as before. A demo-specific test
    sets ``settings.DEMO_MODE = True`` explicitly."""
    settings.DEMO_MODE = False
