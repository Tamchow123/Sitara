"""Install (or verify) a demo asset pack into private storage (Phase 15 Part A).

    python manage.py install_demo_asset_pack --manifest <path> --source-dir <path> [--verify-only]
    python manage.py install_demo_asset_pack --dev-synthetic

Validates the complete manifest (schema + cultural/coverage guarantees)
before writing anything, verifies every source file is a plain, non-symlink
file inside ``--source-dir``, decodes/sanitises/re-encodes each one, verifies
its dimensions, size and content hash against the manifest, uploads to
private storage under a deterministic content-addressed key, and re-reads
every uploaded object to verify it. Nothing is left half-installed: any
failure cleans up objects newly written by this run and no asset is
activated until the whole pack succeeds. Makes no network calls other than
the configured private object-storage backend.

Prints only safe counts, asset IDs and the manifest hash — never a source
path, filename or storage key."""

import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from pydantic import ValidationError

from sitara.generation.demo.ingest import DemoAssetImageError, sanitise_demo_source_image
from sitara.generation.demo.manifest import (
    DemoManifest,
    ManifestCoverageError,
    assert_production_content_ready,
    manifest_sha256,
    validate_manifest_coverage,
)
from sitara.generation.demo.storage import build_demo_asset_key, demo_asset_storage
from sitara.generation.demo.synthetic_pack import (
    SyntheticPackNotAllowed,
    build_synthetic_demo_pack,
)

_MAX_SOURCE_BYTES = 8_000_000
_MAX_SOURCE_PIXELS = 4096 * 4096


def _asset_error(asset_id: str, detail: str) -> CommandError:
    return CommandError(f"asset {asset_id!r} {detail}")


class Command(BaseCommand):
    help = "Validate and install a demo asset pack into private storage; provider-free."

    def add_arguments(self, parser):
        parser.add_argument("--manifest", help="Path to the manifest JSON file.")
        parser.add_argument(
            "--source-dir", help="Directory containing the manifest's source images."
        )
        parser.add_argument(
            "--verify-only",
            action="store_true",
            help="Validate and verify without writing to storage.",
        )
        parser.add_argument(
            "--dev-synthetic",
            action="store_true",
            help="Install the built-in development-only synthetic pack (never production content).",
        )

    def handle(self, *args, **options):
        if options["dev_synthetic"]:
            if options["manifest"] or options["source_dir"]:
                raise CommandError(
                    "--dev-synthetic cannot be combined with --manifest/--source-dir"
                )
            self._install_dev_synthetic(verify_only=options["verify_only"])
            return

        if not options["manifest"] or not options["source_dir"]:
            raise CommandError("--manifest and --source-dir are required (or use --dev-synthetic)")
        self._install_from_files(
            manifest_path=options["manifest"],
            source_dir=options["source_dir"],
            verify_only=options["verify_only"],
        )

    def _install_dev_synthetic(self, *, verify_only: bool) -> None:
        try:
            manifest, images = build_synthetic_demo_pack()
        except SyntheticPackNotAllowed as exc:
            raise CommandError(str(exc)) from None
        # The synthetic pack's bytes are already canonical (generated
        # in-process, never from an external file) — sanitising them again
        # would re-encode an already-lossy WebP a second time and could
        # legitimately produce different bytes/hash. Only real, untrusted
        # source files (the --manifest/--source-dir path) need sanitisation.
        self._install(
            manifest=manifest,
            image_bytes_by_asset_id=images,
            verify_only=verify_only,
            production_ready_check=False,
            sanitize=False,
        )

    def _install_from_files(
        self, *, manifest_path: str, source_dir: str, verify_only: bool
    ) -> None:
        manifest = self._load_manifest(manifest_path)
        source_root = Path(source_dir).resolve()
        if not source_root.is_dir():
            raise CommandError("source-dir must be an existing directory")

        images: dict[str, bytes] = {}
        for asset in manifest.assets:
            candidate = (source_root / asset.filename).resolve()
            if candidate.is_symlink():
                raise CommandError(
                    f"source file for asset {asset.asset_id!r} must not be a symlink"
                )
            if source_root not in candidate.parents:
                raise CommandError(f"source file for asset {asset.asset_id!r} escapes source-dir")
            if not candidate.is_file():
                raise CommandError(f"source file for asset {asset.asset_id!r} is missing")
            images[asset.asset_id] = candidate.read_bytes()

        self._install(
            manifest=manifest,
            image_bytes_by_asset_id=images,
            verify_only=verify_only,
            production_ready_check=True,
            sanitize=True,
        )

    def _load_manifest(self, manifest_path: str) -> DemoManifest:
        path = Path(manifest_path)
        if not path.is_file():
            raise CommandError("manifest file not found")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise CommandError("manifest is not readable JSON") from None
        try:
            manifest = DemoManifest.model_validate(raw)
        except ValidationError as exc:
            fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
            raise CommandError(
                f"manifest failed schema validation ({len(fields)} field(s)): {fields}"
            ) from None
        try:
            validate_manifest_coverage(manifest)
        except ManifestCoverageError as exc:
            raise CommandError(f"manifest failed coverage validation: {exc}") from None
        return manifest

    def _install(
        self,
        *,
        manifest: DemoManifest,
        image_bytes_by_asset_id: dict[str, bytes],
        verify_only: bool,
        production_ready_check: bool,
        sanitize: bool,
    ) -> None:
        if production_ready_check and getattr(settings, "APP_ENV", "development") == "production":
            try:
                assert_production_content_ready(manifest)
            except ManifestCoverageError as exc:
                raise CommandError(f"manifest is not production-ready: {exc}") from None

        manifest_hash = manifest_sha256(manifest)
        storage = demo_asset_storage()
        newly_written_keys: list[str] = []
        installed_ids: list[str] = []
        idempotent_ids: list[str] = []

        try:
            for asset in manifest.assets:
                raw_bytes = image_bytes_by_asset_id[asset.asset_id]
                if sanitize:
                    try:
                        sanitised = sanitise_demo_source_image(
                            raw_bytes, max_bytes=_MAX_SOURCE_BYTES, max_pixels=_MAX_SOURCE_PIXELS
                        )
                    except DemoAssetImageError as exc:
                        raise _asset_error(
                            asset.asset_id, f"failed image processing: {exc}"
                        ) from None
                    final_bytes = sanitised.image_bytes
                    final_sha256 = sanitised.sha256
                    final_width, final_height = sanitised.width, sanitised.height
                else:
                    final_bytes = raw_bytes
                    final_sha256 = hashlib.sha256(raw_bytes).hexdigest()
                    final_width, final_height = asset.width, asset.height

                if (
                    final_sha256 != asset.sha256
                    or final_width != asset.width
                    or final_height != asset.height
                ):
                    raise _asset_error(
                        asset.asset_id, "does not match its manifest entry after processing"
                    )

                key = build_demo_asset_key(
                    pack_id=manifest.pack_id, manifest_hash=manifest_hash, asset_id=asset.asset_id
                )

                if verify_only:
                    idempotent_ids.append(asset.asset_id)
                    continue

                if storage.exists(key):
                    with storage.open(key, "rb") as existing:
                        existing_bytes = existing.read()
                    if hashlib.sha256(existing_bytes).hexdigest() != asset.sha256:
                        raise _asset_error(
                            asset.asset_id, "already exists in storage with conflicting content"
                        )
                    idempotent_ids.append(asset.asset_id)
                    continue

                storage.save(key, ContentFile(final_bytes))
                newly_written_keys.append(key)

                with storage.open(key, "rb") as written:
                    written_bytes = written.read()
                if hashlib.sha256(written_bytes).hexdigest() != asset.sha256:
                    raise _asset_error(asset.asset_id, "failed read-back verification")

                installed_ids.append(asset.asset_id)
        except CommandError:
            for key in newly_written_keys:
                try:
                    storage.delete(key)
                except OSError:
                    pass
            raise

        self.stdout.write(self.style.SUCCESS("Demo asset pack processed successfully."))
        self.stdout.write(f"pack_id: {manifest.pack_id}")
        self.stdout.write(f"manifest_hash: {manifest_hash}")
        self.stdout.write(f"manifest_schema_version: {manifest.schema_version}")
        self.stdout.write(f"asset_count: {len(manifest.assets)}")
        self.stdout.write(f"installed: {sorted(installed_ids)}")
        self.stdout.write(f"idempotent_or_verified: {sorted(idempotent_ids)}")
        if verify_only:
            self.stdout.write("mode: verify-only (nothing written)")
