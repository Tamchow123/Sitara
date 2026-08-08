"""Regenerate the annotated-PNG golden manifest.

Run from ``apps/api`` inside the container:

    docker compose run --rm --no-deps -T api \
        python -m sitara.media.tests.regenerate_annotation_golden

The manifest holds two kinds of digest, and only one of them travels. The PIXEL
digests are the portable contract and are asserted everywhere. The compressed-PNG
digests depend on the zlib the encoder was linked against, so they are recorded
together with an ``environment`` fingerprint and asserted only where that
fingerprint matches — see ``test_annotation_golden.py`` for why this file no
longer claims that generating here makes the bytes reproducible in CI.

Regenerating is a reviewed act. A PIXEL diff means either a deliberate renderer
change (bump ``DESIGN_ANNOTATION_RENDERER_VERSION`` in the same commit) or a
dependency moved underneath the renderer — investigate which before accepting.
"""

from __future__ import annotations

import json
import os


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()

    from django.conf import settings
    from django.test.utils import override_settings

    storages = dict(settings.STORAGES)
    storages["design_images"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}

    with override_settings(STORAGES=storages):
        from sitara.media import annotation_render
        from sitara.media.tests import test_annotation_golden as golden

        manifest = {
            "renderer_version": annotation_render.DESIGN_ANNOTATION_RENDERER_VERSION,
            "font_sha256": annotation_render._FONT_SHA256,
            "page_width": annotation_render.PAGE_WIDTH,
            "bounds": {
                "max_pixels": settings.ANNOTATION_RENDER_MAX_PIXELS,
                "max_bytes": settings.ANNOTATION_RENDER_MAX_BYTES,
                "read_deadline_seconds": settings.ANNOTATION_RENDER_READ_DEADLINE_SECONDS,
            },
            "environment": golden._environment(),
            "fixtures": {name: golden._observed(name) for name in sorted(golden.FIXTURES)},
        }

    golden.GOLDEN_MANIFEST.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {golden.GOLDEN_MANIFEST}")


if __name__ == "__main__":
    main()
