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
def no_network(monkeypatch):
    def guard(*args, **kwargs):
        raise AssertionError("network access attempted during a generation test")

    monkeypatch.setattr(socket.socket, "connect", guard)
