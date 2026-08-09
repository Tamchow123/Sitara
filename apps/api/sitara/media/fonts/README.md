# Vendored export font

One font file, used only by `sitara.media.annotation_render` to draw the note
legend of the annotated PNG export. Nothing else reads it, and it is never
served to a browser — the web app uses its own webfonts.

| | |
| --- | --- |
| File | `NotoSans-Regular.ttf` |
| Family | Noto Sans Regular |
| Version | hinted TTF from `notofonts/notofonts.github.io`, retrieved 2026-08-07 |
| SHA-256 | `478c558ea716033cd60c03438f628dfa75694dcf6b5f6d505a2f05fd2b4f3823` |
| Licence | SIL Open Font License 1.1 — full text in `OFL.txt` |
| Size | 621 572 bytes |

The hash is pinned in `annotation_render.py` and asserted on load. A mismatch
fails closed rather than silently changing the rendered output, because the font
is part of the deterministic golden-bytes contract that
`DESIGN_ANNOTATION_RENDERER_VERSION` names.

## Why it is vendored rather than installed

The API image (`python:3.12.7-slim-bookworm`) installs no fonts, and the pinned
Pillow's own bundled fallback (Aileron) covers little more than ASCII — measured,
not assumed: it cannot render `é`, `—`, `£` or `…`, all of which occur in this
product's ordinary copy. An apt-installed font would make the golden bytes depend
on a distro package version; a vendored file with a pinned hash does not.

## What it does and does not cover

Covers Latin (including accents), Greek, Cyrillic, punctuation and currency —
including `₹`. Measured coverage of the cases that matter here:

```text
é  —  ’  £  ₹  …  ×  •  U+FFFD     rendered
Devanagari, Arabic, Bengali, Gurmukhi   NOT rendered
```

**Complex scripts are deliberately absent, and that is not an oversight to be
fixed by adding more font files.** The pinned Pillow wheel reports
`raqm: False`, `harfbuzz: False`, `fribidi: False`, so even with Noto Sans
Devanagari or Noto Naskh Arabic present, conjuncts would break apart, matras
would detach, Arabic would not join, and right-to-left runs would come out
reversed. Rendering a bride's Devanagari note as broken glyphs is worse than
declining to render it, so `annotation_render` detects characters this font
cannot draw and prints an honest line pointing the owner back to the workspace —
where their own system fonts render the note correctly.

Adding real complex-script support means a Pillow built against raqm plus the
matching Noto families, and is its own decision with its own evaluation.
