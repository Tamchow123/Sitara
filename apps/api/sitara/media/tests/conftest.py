"""Media test fixtures: zero network, isolated in-memory storage aliases."""

import copy
import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def guard(*args, **kwargs):
        raise AssertionError("network access attempted during a media test")

    monkeypatch.setattr(socket.socket, "connect", guard)


@pytest.fixture(autouse=True)
def inmemory_storages(settings):
    """Both the staging (default) and permanent (design_images) aliases
    resolve to isolated in-memory storage — CI has no MinIO, and the network
    guard above makes any accidental S3 construction fail loudly."""
    storages = copy.deepcopy(settings.STORAGES)
    storages["default"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
    storages["design_images"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
    settings.STORAGES = storages
