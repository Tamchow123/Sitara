"""The snapshot regeneration guard: a version bump is required when output changes.

These tests exercise the pure decision function (no file writes) and prove that
the normal comparison path never modifies committed snapshots.
"""

import hashlib

from sitara.generation.prompt_builder import PROMPT_BUILDER_VERSION
from sitara.generation.prompt_snapshots import (
    SNAPSHOT_DIR,
    RegenerationDecision,
    build_all_prompts,
    combined_hash,
    evaluate_regeneration,
    read_manifest,
)

_VERSION = "9.9.9"
_OTHER_VERSION = "9.9.10"
_HASH = "a" * 64
_OTHER_HASH = "b" * 64


def test_changed_hash_unchanged_version_is_refused():
    manifest = {"prompt_builder_version": _VERSION, "combined_sha256": _HASH}
    decision = evaluate_regeneration(manifest, _OTHER_HASH, current_version=_VERSION)
    assert decision is RegenerationDecision.REFUSED_VERSION_UNCHANGED
    assert not decision.allowed


def test_changed_hash_bumped_version_is_accepted():
    manifest = {"prompt_builder_version": _VERSION, "combined_sha256": _HASH}
    decision = evaluate_regeneration(manifest, _OTHER_HASH, current_version=_OTHER_VERSION)
    assert decision is RegenerationDecision.VERSION_BUMPED
    assert decision.allowed


def test_unchanged_hash_unchanged_version_is_accepted():
    manifest = {"prompt_builder_version": _VERSION, "combined_sha256": _HASH}
    decision = evaluate_regeneration(manifest, _HASH, current_version=_VERSION)
    assert decision is RegenerationDecision.UNCHANGED
    assert decision.allowed


def test_missing_manifest_initialises():
    decision = evaluate_regeneration(None, _HASH, current_version=_VERSION)
    assert decision is RegenerationDecision.INITIALISE
    assert decision.allowed


def test_committed_state_is_idempotent_for_the_current_version():
    # The committed manifest + current builder must evaluate as UNCHANGED, so a
    # no-op regeneration never rewrites files.
    manifest = read_manifest()
    new_hash = combined_hash(build_all_prompts())
    decision = evaluate_regeneration(manifest, new_hash, current_version=PROMPT_BUILDER_VERSION)
    assert decision is RegenerationDecision.UNCHANGED


def _snapshot_digest() -> str:
    hasher = hashlib.sha256()
    for path in sorted(SNAPSHOT_DIR.glob("*")):
        hasher.update(path.name.encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def test_comparison_mode_does_not_modify_snapshots():
    # Building and comparing (what the normal suite does) writes nothing.
    before = _snapshot_digest()
    prompts = build_all_prompts()
    for name, prompt in prompts.items():
        expected = (SNAPSHOT_DIR / f"{name}.txt").read_text(encoding="utf-8")
        assert prompt == expected
    assert _snapshot_digest() == before
