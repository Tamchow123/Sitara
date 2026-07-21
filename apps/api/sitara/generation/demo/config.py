"""Active demo-pack resolution (Phase 15 Part C).

The single source of truth for "which demo manifest is currently active" is
one well-known private-storage object (:data:`ACTIVE_MANIFEST_KEY`) written
by ``install_demo_asset_pack`` only after a complete, successful pack
install — never a filesystem path, so this works uniformly across every
configured storage backend (S3/MinIO in most environments, the filesystem
backend in local development) without a separate setting to keep in sync.
Demo-generation readiness and every real asset selection/download resolve
through this module, never by re-deriving the active pack from settings or
re-reading the install command's arguments."""

import json

from .manifest import DemoManifest, validate_manifest_coverage
from .storage import demo_asset_storage

ACTIVE_MANIFEST_KEY = "demo-assets/active-manifest.json"


class DemoAssetsUnavailable(Exception):
    """No valid, coverage-complete demo manifest is currently active — the
    manifest is missing, unreadable, fails schema/coverage validation, or
    private demo storage itself is unavailable. Never reveals which internal
    object or path failed."""


def load_active_demo_manifest() -> DemoManifest:
    """Load and fully validate the currently active demo manifest.

    Raises :class:`DemoAssetsUnavailable` for every failure mode (missing
    pointer, malformed JSON, schema validation failure, coverage-guarantee
    failure, or ANY storage-backend error — a connection failure, a
    credential problem, an unreachable bucket) with the same generic
    message — never distinguishing which internal cause applied. This is a
    deliberate fail-closed availability boundary (matching
    ``sitara.health.checks``'s precedent): "required storage available" is
    part of demo readiness, so any storage exception must be reported as
    "not ready," never propagate as an unhandled error."""
    try:
        storage = demo_asset_storage()
        if not storage.exists(ACTIVE_MANIFEST_KEY):
            raise DemoAssetsUnavailable("no active demo manifest is configured")
        with storage.open(ACTIVE_MANIFEST_KEY, "rb") as handle:
            raw = handle.read()
        manifest = DemoManifest.model_validate(json.loads(raw))
        validate_manifest_coverage(manifest)
    except DemoAssetsUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - deliberate fail-closed availability boundary
        raise DemoAssetsUnavailable("the active demo manifest is unavailable") from exc
    return manifest


def demo_generation_is_available() -> bool:
    """True only when a complete, valid demo manifest is currently active."""
    try:
        load_active_demo_manifest()
    except DemoAssetsUnavailable:
        return False
    return True
