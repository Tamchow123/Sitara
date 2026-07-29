"""End-to-end tests for the install_demo_asset_pack management command."""

import io
import json

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image

from sitara.generation.demo.ingest import sanitise_demo_source_image
from sitara.generation.demo.manifest import DemoManifest, manifest_sha256
from sitara.generation.demo.storage import build_demo_asset_key, demo_asset_storage
from sitara.generation.demo.synthetic_pack import build_synthetic_demo_pack

pytestmark = pytest.mark.usefixtures("inmemory_storage")


def _run_install(manifest_path, source_dir, *extra_args):
    call_command(
        "install_demo_asset_pack",
        "--manifest",
        str(manifest_path),
        "--source-dir",
        str(source_dir),
        *extra_args,
    )


# A minimal three-asset layout that still satisfies full pack-wide coverage
# (all six garments via <=2 tags per asset, all six ceremonies, both minimal
# and heavy embellishment density, a modest-coverage example, >=5 colours,
# >=3 fabrics) — mirrors the constraints in
# sitara.generation.demo.manifest.validate_manifest_coverage.
_ASSET_SPECS = (
    {
        "asset_id": "asset-one",
        "colour": (150, 20, 30),
        "garment_types": ["lehenga", "saree"],
        "ceremonies": ["baraat", "nikah", "anand_karaj"],
        "silhouettes": ["classic_saree_drape"],
        "colours": ["red", "gold", "maroon"],
        "fabrics": ["silk", "velvet"],
        "embellishment_styles": ["zardozi"],
        "embellishment_densities": ["minimal"],
        "coverage_preferences": ["full_sleeves"],
        "necklines": ["high_neck"],
        "dupatta_styles": [],
        "saree_drapes": ["nivi_drape"],
    },
    {
        "asset_id": "asset-two",
        "colour": (40, 120, 60),
        "garment_types": ["gharara", "anarkali"],
        "ceremonies": ["mehndi", "walima"],
        "silhouettes": ["gharara_construction"],
        "colours": ["green", "yellow"],
        "fabrics": ["cotton_silk"],
        "embellishment_styles": ["mirror_work"],
        "embellishment_densities": ["heavy"],
        "coverage_preferences": [],
        "dupatta_styles": ["one_shoulder"],
        "saree_drapes": [],
    },
    {
        "asset_id": "asset-three",
        "colour": (20, 30, 70),
        "garment_types": ["sharara", "shalwar_kameez"],
        "ceremonies": ["pheras", "reception"],
        "silhouettes": ["sharara_construction"],
        "colours": ["blue", "navy"],
        "fabrics": ["georgette"],
        "embellishment_styles": ["resham_threadwork"],
        "embellishment_densities": ["balanced"],
        "coverage_preferences": [],
        "dupatta_styles": ["front_drape"],
        "saree_drapes": [],
    },
)


def _png_bytes(colour) -> bytes:
    image = Image.new("RGB", (768, 1024), colour)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _write_file_based_pack(tmp_path, *, provenance="verified_project_owned"):
    """A three-asset pack, written as real PNG source files on disk, whose
    manifest fields are derived from the SAME sanitisation the install
    command performs (mirroring a real curator authoring workflow) — and
    which satisfies full pack-wide coverage validation."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    assets = []
    for spec in _ASSET_SPECS:
        raw_png = _png_bytes(spec["colour"])
        filename = f"{spec['asset_id']}.webp"
        # Sanitisation re-encodes to canonical WebP; write the SOURCE as PNG
        # (a single lossy-webp encode happens once, inside the command).
        webp_buffer = io.BytesIO()
        Image.open(io.BytesIO(raw_png)).save(webp_buffer, format="WEBP", quality=90)
        raw_webp = webp_buffer.getvalue()
        (source_dir / filename).write_bytes(raw_webp)

        sanitised = sanitise_demo_source_image(
            raw_webp, max_bytes=8_000_000, max_pixels=4096 * 4096
        )
        assets.append(
            {
                "asset_id": spec["asset_id"],
                "filename": filename,
                "sha256": sanitised.sha256,
                "size_bytes": sanitised.size_bytes,
                "width": sanitised.width,
                "height": sanitised.height,
                "alt_text": (
                    f"A placeholder alt text for {spec['asset_id']}, "
                    "well past the minimum length."
                ),
                "garment_types": spec["garment_types"],
                "ceremonies": spec["ceremonies"],
                "silhouettes": spec["silhouettes"],
                "colours": spec["colours"],
                "fabrics": spec["fabrics"],
                "embellishment_styles": spec["embellishment_styles"],
                "embellishment_densities": spec["embellishment_densities"],
                "coverage_preferences": spec["coverage_preferences"],
                "necklines": spec.get("necklines", []),
                "dupatta_styles": spec["dupatta_styles"],
                "saree_drapes": spec["saree_drapes"],
                "regional_styles": [],
                "provenance_status": provenance,
            }
        )

    manifest_dict = {"schema_version": 2, "pack_id": "test-file-pack", "assets": assets}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_dict), encoding="utf-8")
    return manifest_path, source_dir, manifest_dict


class TestDevSyntheticInstall:
    def test_installs_successfully(self):
        call_command("install_demo_asset_pack", "--dev-synthetic")
        manifest, _images = build_synthetic_demo_pack()
        manifest_hash = manifest_sha256(manifest)
        storage = demo_asset_storage()
        for asset in manifest.assets:
            key = build_demo_asset_key(
                pack_id=manifest.pack_id, manifest_hash=manifest_hash, asset_id=asset.asset_id
            )
            assert storage.exists(key)

    def test_idempotent_reinstall(self):
        call_command("install_demo_asset_pack", "--dev-synthetic")
        call_command("install_demo_asset_pack", "--dev-synthetic")  # does not raise

    def test_conflicting_existing_object_is_rejected(self):
        manifest, _images = build_synthetic_demo_pack()
        manifest_hash = manifest_sha256(manifest)
        storage = demo_asset_storage()
        first_asset = manifest.assets[0]
        key = build_demo_asset_key(
            pack_id=manifest.pack_id, manifest_hash=manifest_hash, asset_id=first_asset.asset_id
        )
        storage.save(key, ContentFile(b"conflicting bytes that do not match the manifest hash"))
        with pytest.raises(CommandError):
            call_command("install_demo_asset_pack", "--dev-synthetic")

    def test_readback_corruption_after_write_is_rejected_and_cleaned_up(self, monkeypatch):
        # A storage backend that reports a successful save() but returns
        # different bytes on the read-back open() must be caught, and the
        # newly-written (corrupt) object must be cleaned up, not left behind.
        real_storage = demo_asset_storage()

        class _CorruptingStorage:
            def exists(self, key):
                return real_storage.exists(key)

            def save(self, key, content):
                return real_storage.save(key, content)

            def open(self, key, mode="rb"):
                return io.BytesIO(b"corrupted on read-back, does not match the saved hash")

            def delete(self, key):
                return real_storage.delete(key)

        monkeypatch.setattr(
            "sitara.generation.management.commands.install_demo_asset_pack.demo_asset_storage",
            lambda: _CorruptingStorage(),
        )

        manifest, _images = build_synthetic_demo_pack()
        manifest_hash = manifest_sha256(manifest)
        first_key = build_demo_asset_key(
            pack_id=manifest.pack_id,
            manifest_hash=manifest_hash,
            asset_id=manifest.assets[0].asset_id,
        )

        with pytest.raises(CommandError):
            call_command("install_demo_asset_pack", "--dev-synthetic")

        assert not real_storage.exists(first_key)

    def test_verify_only_writes_nothing(self):
        call_command("install_demo_asset_pack", "--dev-synthetic", "--verify-only")
        manifest, _images = build_synthetic_demo_pack()
        manifest_hash = manifest_sha256(manifest)
        storage = demo_asset_storage()
        for asset in manifest.assets:
            key = build_demo_asset_key(
                pack_id=manifest.pack_id, manifest_hash=manifest_hash, asset_id=asset.asset_id
            )
            assert not storage.exists(key)

    def test_rejected_in_production(self, settings):
        settings.APP_ENV = "production"
        with pytest.raises(CommandError):
            call_command("install_demo_asset_pack", "--dev-synthetic")

    def test_combining_dev_synthetic_with_manifest_is_rejected(self, tmp_path):
        with pytest.raises(CommandError):
            call_command(
                "install_demo_asset_pack", "--dev-synthetic", "--manifest", str(tmp_path / "x.json")
            )


class TestFileBasedInstall:
    def test_installs_from_manifest_and_source_dir(self, tmp_path):
        manifest_path, source_dir, manifest_dict = _write_file_based_pack(tmp_path)
        _run_install(manifest_path, source_dir)
        manifest = DemoManifest.model_validate(manifest_dict)
        manifest_hash = manifest_sha256(manifest)
        storage = demo_asset_storage()
        for asset in manifest.assets:
            key = build_demo_asset_key(
                pack_id=manifest.pack_id, manifest_hash=manifest_hash, asset_id=asset.asset_id
            )
            assert storage.exists(key)

    def test_missing_source_file_is_rejected(self, tmp_path):
        manifest_path, source_dir, _manifest_dict = _write_file_based_pack(tmp_path)
        (source_dir / "asset-one.webp").unlink()
        with pytest.raises(CommandError):
            _run_install(manifest_path, source_dir)

    def test_path_traversal_filename_is_rejected_at_manifest_layer(self, tmp_path):
        manifest_path, source_dir, manifest_dict = _write_file_based_pack(tmp_path)
        manifest_dict["assets"][0]["filename"] = "../escape.webp"
        manifest_path.write_text(json.dumps(manifest_dict), encoding="utf-8")
        with pytest.raises(CommandError):
            _run_install(manifest_path, source_dir)

    def test_corrupt_source_file_triggers_full_cleanup(self, tmp_path):
        # asset-three's source file is corrupt: asset-one/asset-two objects
        # already written this run must be rolled back too (no partial pack).
        manifest_path, source_dir, manifest_dict = _write_file_based_pack(tmp_path)
        (source_dir / "asset-three.webp").write_bytes(b"not a real image")

        with pytest.raises(CommandError):
            _run_install(manifest_path, source_dir)

        manifest = DemoManifest.model_validate(manifest_dict)
        manifest_hash = manifest_sha256(manifest)
        storage = demo_asset_storage()
        key_one = build_demo_asset_key(
            pack_id=manifest.pack_id, manifest_hash=manifest_hash, asset_id="asset-one"
        )
        key_two = build_demo_asset_key(
            pack_id=manifest.pack_id, manifest_hash=manifest_hash, asset_id="asset-two"
        )
        assert not storage.exists(key_one)
        assert not storage.exists(key_two)

    def test_verify_only_writes_nothing(self, tmp_path):
        manifest_path, source_dir, manifest_dict = _write_file_based_pack(tmp_path)
        _run_install(manifest_path, source_dir, "--verify-only")
        manifest = DemoManifest.model_validate(manifest_dict)
        key = build_demo_asset_key(
            pack_id=manifest.pack_id, manifest_hash=manifest_sha256(manifest), asset_id="asset-one"
        )
        assert not demo_asset_storage().exists(key)

    def test_symlinked_source_file_is_rejected(self, tmp_path):
        manifest_path, source_dir, manifest_dict = _write_file_based_pack(tmp_path)
        real_target = tmp_path / "outside.webp"
        real_target.write_bytes((source_dir / "asset-one.webp").read_bytes())
        link_path = source_dir / "asset-one.webp"
        link_path.unlink()
        try:
            link_path.symlink_to(real_target)
        except OSError:
            pytest.skip("symlinks are not supported in this environment")
        with pytest.raises(CommandError):
            _run_install(manifest_path, source_dir)

    def test_manifest_failing_coverage_validation_is_rejected(self, tmp_path):
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        webp_buffer = io.BytesIO()
        Image.open(io.BytesIO(_png_bytes((10, 10, 10)))).save(
            webp_buffer, format="WEBP", quality=90
        )
        raw_webp = webp_buffer.getvalue()
        (source_dir / "asset-one.webp").write_bytes(raw_webp)
        sanitised = sanitise_demo_source_image(
            raw_webp, max_bytes=8_000_000, max_pixels=4096 * 4096
        )

        # A single-garment asset can never satisfy pack-wide coverage.
        manifest_dict = {
            "schema_version": 1,
            "pack_id": "test-incomplete-pack",
            "assets": [
                {
                    "asset_id": "asset-one",
                    "filename": "asset-one.webp",
                    "sha256": sanitised.sha256,
                    "size_bytes": sanitised.size_bytes,
                    "width": sanitised.width,
                    "height": sanitised.height,
                    "alt_text": "A placeholder alt text, well past the minimum length requirement.",
                    "garment_types": ["lehenga"],
                    "ceremonies": ["baraat"],
                    "silhouettes": ["flared_lehenga"],
                    "colours": ["red"],
                    "fabrics": ["silk"],
                    "embellishment_styles": ["zardozi"],
                    "embellishment_densities": ["heavy"],
                    "coverage_preferences": ["full_sleeves"],
                    "dupatta_styles": ["head_drape"],
                    "saree_drapes": [],
                    "regional_styles": [],
                    "provenance_status": "verified_project_owned",
                }
            ],
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_dict), encoding="utf-8")
        with pytest.raises(CommandError):
            _run_install(manifest_path, source_dir)

    def test_synthetic_provenance_is_never_production_ready(self, tmp_path, settings):
        settings.APP_ENV = "production"
        manifest_path, source_dir, _manifest_dict = _write_file_based_pack(
            tmp_path, provenance="synthetic_development_placeholder"
        )
        with pytest.raises(CommandError):
            _run_install(manifest_path, source_dir)
