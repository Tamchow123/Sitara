"""Private demo-source-asset storage key builder (Phase 15 Part A).

Demo source images live in the same private object storage as Phase 10 raw
staging (``default_storage``), under a deterministic, server-generated key
namespace. These keys are never exposed through any API, OpenAPI schema,
frontend code, log line or exception — only :mod:`sitara.generation.demo`
internals ever read or write them, and the eventual DesignVersion image uses
its own normal, unrelated ``design_images`` storage key (Phase 11 canonical
ingest), never this one."""

import re

from django.core.files.storage import default_storage

_ASSET_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_PACK_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def build_demo_asset_key(*, pack_id: str, manifest_hash: str, asset_id: str) -> str:
    """The deterministic private storage key for one demo source asset.

    Shape: ``demo-assets/<pack-id>/<manifest-hash>/<asset-id>.webp``. Every
    component is validated so the key can never contain a path-traversal
    segment or an unexpected character."""
    if not _PACK_ID_PATTERN.match(pack_id):
        raise ValueError("pack_id must be a lowercase kebab machine identifier")
    if not _SHA256_PATTERN.match(manifest_hash):
        raise ValueError("manifest_hash must be 64 lowercase hex characters")
    if not _ASSET_ID_PATTERN.match(asset_id):
        raise ValueError("asset_id must be a lowercase kebab machine identifier")
    return f"demo-assets/{pack_id}/{manifest_hash}/{asset_id}.webp"


def demo_asset_storage():
    """The private storage backend demo source assets are read/written from.

    Resolved as a plain reference (matching the Phase 10 raw-staging
    precedent of using ``default_storage``) rather than cached at import
    time, so test/environment overrides always apply."""
    return default_storage
