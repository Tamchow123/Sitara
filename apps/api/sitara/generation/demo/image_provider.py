"""Asynchronous local demo image adapter (Phase 15 Part C).

Implements the exact :class:`~sitara.ai_gateway.image_generation.ImageProvider`
protocol the live Replicate provider implements — ``create_prediction`` /
``get_prediction`` / ``cancel_prediction`` — but never contacts a remote
host and never constructs a provider SDK client. The selected asset is
referenced by a private, opaque ``demo-asset://`` scheme (never an ordinary
HTTP/HTTPS URL); only :func:`demo_image_downloader` in this module may
resolve it, and the live downloader
(:func:`sitara.generation.image_download.download_replicate_output`) is
never handed a URL in this scheme.

The normal pipeline states (starting -> succeeded) are preserved: a demo
prediction "starts" on create and resolves to "succeeded" on the first poll
— no live provider states are skipped, only the network transport is
replaced."""

import hashlib

from sitara.ai_gateway.image_generation import (
    PREDICTION_STARTING,
    PREDICTION_SUCCEEDED,
    ImageGenerationRequest,
    ImagePrediction,
)

from .config import DemoAssetsUnavailable, load_active_demo_manifest
from .manifest import DemoManifest, manifest_sha256
from .selector import DemoAssetSelection
from .storage import DEMO_SOURCE_ASSET_MAX_BYTES, build_demo_asset_key, demo_asset_storage

DEMO_ASSET_URL_SCHEME = "demo-asset://"


class DemoImageDownloadFailed(Exception):
    """The referenced demo source asset could not be read back safely.
    Generic, safe message — never a storage key or path."""


def build_demo_asset_reference(*, pack_id: str, manifest_hash: str, asset_id: str) -> str:
    """The private, opaque ``demo-asset://`` reference for one selection.
    Never a real storage key, never fetched over HTTP."""
    return f"{DEMO_ASSET_URL_SCHEME}{pack_id}/{manifest_hash}/{asset_id}"


def _parse_demo_asset_reference(url: str) -> tuple[str, str, str]:
    if not url.startswith(DEMO_ASSET_URL_SCHEME):
        raise DemoImageDownloadFailed("not a demo-asset:// reference")
    remainder = url[len(DEMO_ASSET_URL_SCHEME) :]
    parts = remainder.split("/")
    if len(parts) != 3 or not all(parts):
        raise DemoImageDownloadFailed("malformed demo-asset:// reference")
    pack_id, manifest_hash, asset_id = parts
    return pack_id, manifest_hash, asset_id


class DemoImageProvider:
    """Deterministic local image provider for one already-selected demo asset.

    Never contacts a remote host. ``create_prediction`` returns a
    ``starting`` prediction; ``get_prediction`` always resolves it to
    ``succeeded`` on the first poll with the private ``demo-asset://``
    reference as its output — the normal pipeline poll loop is exercised
    unchanged, only the transport differs."""

    name = "demo"

    def __init__(self, *, selection: DemoAssetSelection, manifest: DemoManifest):
        self._prediction_id = f"demo-{selection.asset_id}"
        self._output_url = build_demo_asset_reference(
            pack_id=manifest.pack_id,
            manifest_hash=selection.manifest_hash,
            asset_id=selection.asset_id,
        )

    def create_prediction(self, request: ImageGenerationRequest) -> ImagePrediction:
        return ImagePrediction(
            prediction_id=self._prediction_id,
            provider=self.name,
            model=request.model,
            status=PREDICTION_STARTING,
        )

    def get_prediction(self, prediction_id: str) -> ImagePrediction:
        return ImagePrediction(
            prediction_id=prediction_id,
            provider=self.name,
            model=self.name,
            status=PREDICTION_SUCCEEDED,
            output_url=self._output_url,
        )

    def cancel_prediction(self, prediction_id: str) -> None:
        return None


def demo_image_downloader(output_url: str) -> bytes:
    """Resolve a ``demo-asset://`` reference to verified source bytes.

    Resolves the persisted selection, computes the deterministic private
    source key internally, reads from private storage, enforces a size
    limit, and verifies the SHA-256 against the active manifest's recorded
    value for that asset. Rejects any ordinary HTTP/HTTPS URL outright."""
    pack_id, manifest_hash, asset_id = _parse_demo_asset_reference(output_url)

    try:
        manifest = load_active_demo_manifest()
    except DemoAssetsUnavailable as exc:
        raise DemoImageDownloadFailed("the active demo manifest is unavailable") from exc
    if manifest.pack_id != pack_id or manifest_hash != manifest_sha256(manifest):
        raise DemoImageDownloadFailed("the referenced pack is no longer active")
    asset = next((a for a in manifest.assets if a.asset_id == asset_id), None)
    if asset is None:
        raise DemoImageDownloadFailed("the referenced asset is no longer in the active pack")

    key = build_demo_asset_key(pack_id=pack_id, manifest_hash=manifest_hash, asset_id=asset_id)
    storage = demo_asset_storage()
    try:
        if not storage.exists(key):
            raise DemoImageDownloadFailed("the referenced source asset is missing")
        with storage.open(key, "rb") as handle:
            data = handle.read(DEMO_SOURCE_ASSET_MAX_BYTES + 1)
    except DemoImageDownloadFailed:
        raise
    except OSError as exc:
        raise DemoImageDownloadFailed("the referenced source asset could not be read") from exc
    if not data or len(data) > DEMO_SOURCE_ASSET_MAX_BYTES:
        raise DemoImageDownloadFailed("the referenced source asset is invalid")

    if hashlib.sha256(data).hexdigest() != asset.sha256:
        raise DemoImageDownloadFailed("the referenced source asset failed verification")
    return bytes(data)
