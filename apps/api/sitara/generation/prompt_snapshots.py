"""Golden image-prompt snapshots: locations, hashing and the regeneration guard.

Normal tests use this module read-only to COMPARE built prompts against the
committed ``.txt`` snapshots and ``manifest.json``. Regeneration happens only
through the ``regenerate_image_prompt_snapshots`` management command, which uses
:func:`evaluate_regeneration` to REFUSE overwriting committed snapshots whenever
the rendered output changed while :data:`PROMPT_BUILDER_VERSION` did not — a
deliberate version bump is required first.
"""

import hashlib
import json
from enum import Enum
from pathlib import Path

from .design_spec import DesignSpec
from .prompt_builder import PROMPT_BUILDER_VERSION, build_image_prompt

_HERE = Path(__file__).resolve().parent
FIXTURE_DIR = _HERE / "tests" / "fixtures" / "prompt_builder"
SNAPSHOT_DIR = _HERE / "tests" / "snapshots" / "image_prompt" / "v1"
MANIFEST_PATH = SNAPSHOT_DIR / "manifest.json"


class RegenerationDecision(str, Enum):
    """Outcome of evaluating a proposed snapshot regeneration."""

    # First-ever write (no committed manifest yet).
    INITIALISE = "initialise"
    # Output is unchanged from the committed manifest — writing is a no-op.
    UNCHANGED = "unchanged"
    # Output changed and the builder version was deliberately bumped.
    VERSION_BUMPED = "version_bumped"
    # Output changed but the builder version did not — refuse.
    REFUSED_VERSION_UNCHANGED = "refused_version_unchanged"

    @property
    def allowed(self) -> bool:
        return self is not RegenerationDecision.REFUSED_VERSION_UNCHANGED


def fixture_names() -> list[str]:
    return sorted(path.stem for path in FIXTURE_DIR.glob("*.json"))


def load_fixture_spec(name: str) -> DesignSpec:
    with (FIXTURE_DIR / f"{name}.json").open(encoding="utf-8") as handle:
        return DesignSpec.model_validate(json.load(handle))


def build_all_prompts() -> dict[str, str]:
    """Build every fixture's prompt (deterministic, zero network)."""
    return {name: build_image_prompt(load_fixture_spec(name)) for name in fixture_names()}


def combined_hash(prompts: dict[str, str]) -> str:
    payload = json.dumps(prompts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_manifest() -> dict | None:
    if not MANIFEST_PATH.is_file():
        return None
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def evaluate_regeneration(
    existing_manifest: dict | None,
    new_hash: str,
    current_version: str = PROMPT_BUILDER_VERSION,
) -> RegenerationDecision:
    """Decide whether a regeneration may proceed.

    Pure and file-free so it can be unit-tested directly:

    - no committed manifest → ``INITIALISE`` (allowed);
    - identical hash → ``UNCHANGED`` (allowed, idempotent) regardless of version;
    - changed hash with the SAME builder version → ``REFUSED_VERSION_UNCHANGED``;
    - changed hash with a DIFFERENT builder version → ``VERSION_BUMPED`` (allowed).
    """
    if existing_manifest is None:
        return RegenerationDecision.INITIALISE
    if existing_manifest.get("combined_sha256") == new_hash:
        return RegenerationDecision.UNCHANGED
    if existing_manifest.get("prompt_builder_version") == current_version:
        return RegenerationDecision.REFUSED_VERSION_UNCHANGED
    return RegenerationDecision.VERSION_BUMPED


def write_snapshots(prompts: dict[str, str]) -> str:
    """Write every ``.txt`` snapshot and the manifest; return the combined hash.

    Callers MUST gate this behind :func:`evaluate_regeneration`."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for name, prompt in prompts.items():
        (SNAPSHOT_DIR / f"{name}.txt").write_text(prompt, encoding="utf-8")
    digest = combined_hash(prompts)
    manifest = {
        "prompt_builder_version": PROMPT_BUILDER_VERSION,
        "combined_sha256": digest,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return digest
