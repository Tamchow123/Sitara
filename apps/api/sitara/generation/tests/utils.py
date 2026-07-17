"""Shared helpers for the generation tests.

Fixtures are ORIGINAL synthetic data written for these tests — no third-party
description is copied."""

import copy
import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

VALID_FIXTURES = ("nikah_lehenga", "mehndi_gharara", "pheras_saree")


def load_spec_dict(name: str) -> dict:
    """A fresh deep copy of a recorded valid DesignSpec fixture (as a dict)."""
    with (FIXTURE_DIR / f"{name}.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def a_valid_spec_dict() -> dict:
    return load_spec_dict("nikah_lehenga")


def mutate(base: dict, **changes) -> dict:
    data = copy.deepcopy(base)
    data.update(changes)
    return data
