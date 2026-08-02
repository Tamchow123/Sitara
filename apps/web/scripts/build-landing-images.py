#!/usr/bin/env python3
"""Build the landing page's hero photography from the project's source images.

Same discipline as `build-questionnaire-images.py` and the same deterministic
crop-and-encode helpers (`imagekit.py`): named sources only, never a directory
scan; integer cropping; fixed encoder options; a recorded SHA-256 per output.
`src/app/landing-hero.test.ts` reads the committed bytes back and proves they
still match it, and CI rebuilds this script and fails on any diff.

    python scripts/build-landing-images.py

Kept as a SEPARATE script and a separate manifest on purpose. The questionnaire
manifest is under a bidirectional contract with the Django questionnaire schema
(`apps/api/sitara/questionnaire/tests/test_visual_keys.py`: every entry must be
an option, every option must have an entry). A landing photograph is not an
option, so putting it in that manifest would break a contract that has nothing
to do with it.

Rights: both sources are project-owned photography generated for Sitara, added
under `images/01-landing-page/` by the baseline commit for exactly this purpose.
No third-party or downloaded imagery, and no inspiration-catalogue asset, is
used here. These are decorative page furniture; they are never sent to an AI
provider and never influence DesignSpec generation.

On the choice of sources: `Sitara Home.dc.html` references
`02-ceremonies/sitara__ceremonies__baraat__v2.jpg` and `…walima__v1.jpg`, but the
baseline commit DELETED both from `images/` while adding this dedicated
`01-landing-page/` folder in the same change. These two are the maintainer's own
replacement for that pair, and unlike the ceremony scenes they are what the hero
composition actually needs: a single bride, full length, on a plain ground. The
prototype's staggered 2-up 2:3 composition, radii and washed treatment are
reproduced exactly. The substitution is recorded in the Phase 17 completion
report rather than made silently.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from imagekit import digest, render

WEB_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = WEB_ROOT.parent.parent
SOURCE_ROOT = REPO_ROOT / "images"
OUTPUT_ROOT = WEB_ROOT / "public" / "landing"
MANIFEST_PATH = WEB_ROOT / "src" / "app" / "landing-hero.json"

# The handoff's hero tiles are `aspect-ratio: 2/3`. Cutting to that shape here
# rather than letting the browser do it with object-fit means the framing
# decision is reviewable in this file and the served bytes are exactly the
# frame — no downloading of pixels that get cropped away on every visit.
TARGET = (720, 1080)

# Both sources are 896x1152 (7:9), so a 2:3 cut trims the sides and keeps the
# figure head to hem. `focus` is therefore irrelevant to these two, but is
# passed explicitly so a future taller source crops predictably.
FULL_FIGURE_FOCUS = 0.42

HEROES = [
    {
        "name": "hero-1",
        "source": "01-landing-page/image-012.webp",
        # The hero is decorative — the headline beside it carries the meaning —
        # but "decorative" is not the same as "meaningless", and a bride
        # choosing a bridalwear service deserves to know what the page is
        # showing her. Each alt names the garment and its palette, and neither
        # describes the model.
        "alt": (
            "A bridal outfit in deep maroon velvet, worked all over in gold, "
            "worn with a sheer red dupatta over the head."
        ),
    },
    {
        "name": "hero-2",
        "source": "01-landing-page/image-046.webp",
        "alt": (
            "A bridal outfit in ivory and blush with soft rose-gold embroidery, "
            "worn with a matching dupatta over the head."
        ),
    },
]


def main() -> int:
    missing = [h["source"] for h in HEROES if not (SOURCE_ROOT / h["source"]).is_file()]
    if missing:
        print(f"{len(missing)} source image(s) missing under {SOURCE_ROOT}:", file=sys.stderr)
        for entry in missing:
            print(f"  {entry}", file=sys.stderr)
        return 1

    # Render before touching the tree, so a missing or undecodable source aborts
    # with the committed assets untouched. The disk phase below is a sequence of
    # independent writes and is NOT itself atomic; CI rebuilding both pipelines
    # and failing on any diff is what covers a half-finished run.
    rendered = [
        (hero, render(SOURCE_ROOT / hero["source"], TARGET, FULL_FIGURE_FOCUS)) for hero in HEROES
    ]

    if OUTPUT_ROOT.is_dir():
        for stale in sorted(OUTPUT_ROOT.glob("*.webp")):
            stale.unlink()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, dict[str, object]] = {}
    for hero, payload in rendered:
        filename = f"{hero['name']}.webp"
        (OUTPUT_ROOT / filename).write_bytes(payload)
        manifest[hero["name"]] = {
            "path": f"/landing/{filename}",
            "width": TARGET[0],
            "height": TARGET[1],
            "sha256": digest(payload),
            "alt": hero["alt"],
            "source": hero["source"],
        }

    # newline="\n" explicitly: without it Python rewrites "\n" to "\r\n" on
    # Windows, so the same inputs would produce different bytes per platform.
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {len(manifest)} landing images to {OUTPUT_ROOT}")
    print(f"wrote {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
