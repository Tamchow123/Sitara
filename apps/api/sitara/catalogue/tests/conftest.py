"""Catalogue test fixtures.

Every catalogue test runs against an isolated in-memory storage backend:
CI has no MinIO, and no test may ever touch a real bucket. The production
default-storage configuration is captured at import time (before any
override) so the storage-safety tests can assert on the REAL settings.
"""

import copy

import pytest
from django.conf import settings as django_settings

# Captured before any per-test override: the storage configuration the
# application actually ships with.
ORIGINAL_DEFAULT_STORAGE = copy.deepcopy(django_settings.STORAGES["default"])


@pytest.fixture(autouse=True)
def catalogue_test_storage(settings):
    storages = copy.deepcopy(settings.STORAGES)
    storages["default"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
    settings.STORAGES = storages
