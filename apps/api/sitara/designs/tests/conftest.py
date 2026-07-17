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
