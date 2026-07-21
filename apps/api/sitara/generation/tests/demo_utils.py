"""Shared helpers for Phase 15 demo-package tests."""

import copy

from sitara.generation.demo.synthetic_pack import build_synthetic_demo_pack


def a_valid_manifest_dict() -> dict:
    manifest, _images = build_synthetic_demo_pack()
    return manifest.model_dump(mode="json")


def mutate(base: dict, **changes) -> dict:
    data = copy.deepcopy(base)
    data.update(changes)
    return data
