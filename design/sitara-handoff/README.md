# Handoff: Sitara — AI bridalwear design flow

## Overview

Sitara is a guided, image-led questionnaire that captures a bride's vision for a South Asian bridal outfit, generates an AI concept from it, and lets her refine that concept a limited number of times. This bundle covers six screens: **Home → Questionnaire → Generation → Concept → Amendments → History**.

## About the design files

The `.dc.html` files here are **design references created in HTML** — working prototypes that show intended look, copy and behaviour. They are **not production code to copy**. They run on a small in-house template runtime (`support.js`); do not port that runtime.

The task is to **recreate these designs in your app's existing environment** (React, Vue, SwiftUI, native, etc.) using its established patterns, component library and state management. If no environment exists yet, choose an appropriate framework and implement there.

To view a prototype: open any `.dc.html` in a browser (served over http, not `file://`, so the image paths resolve).

## Fidelity

**High fidelity.** Final colours, typography, spacing, imagery, copy and interaction states. Recreate pixel-faithfully using your own component library where equivalents exist.

## Design system

The visual language is the **Organic** design system, bundled at `_ds/organic-ccb06f12-17fe-45b2-9248-ebf010085646/`. `styles.css` in that folder is the single source of truth for tokens; read it rather than transcribing values. Summary in *Design tokens* below.

Display type is **Cormorant Garamond** (Google Fonts) for the Sitara wordmark and page headings; body copy uses the Organic body face.

---

## Screens

### 1. Home — `Sitara Home.dc.html`

**Purpose:** Set the tone and start the questionnaire.

**Layout:** Single centred column, max-width 1020px, page padding `--space-4`. Two-column hero: left = wordmark, headline, sub-copy and primary CTA; right = a 2-up image grid (`grid-template-columns:1fr 1fr`, gap `--space-3`) where the first tile is pushed down by `margin-top:var(--space-6)` for an asymmetric stagger. Both tiles are `aspect-ratio:2/3`, `border-radius:var(--radius-lg)`, wrapped in `.washed`.

**Components**
- Wordmark: 36px accent-filled circle with an 8-point star glyph + "Sitara" in Cormorant Garamond 600, 27px, letter-spacing .04em. Whole lockup is a link to home with a `--color-accent-100` hover pill and a 2px accent focus ring.
- Headline: `--font-heading`, weight 400, `clamp(30px,4.5vw,42px)`, line-height 1.12.
- Copy: 15px, `--color-neutral-800`, max-width 58ch.
- Primary CTA: `.btn .btn-primary` → Questionnaire.
- Hero images: `uploads/images/02-ceremonies/sitara__ceremonies__baraat__v2.jpg` and `…walima__v1.jpg`.

### 2. Questionnaire — `Sitara Questionnaire.dc.html` (the core screen)

**Purpose:** Capture the brief across **16 single-question screens** grouped into **9 progress categories**: Occasion, Heritage, Colour & cloth, Adornment, Coverage, The drape, Inspiration, Anything else, Review.

**Screen order (index → key)**

| # | Key | Question | Required |
|---|-----|----------|----------|
| 0 | ceremony | Which celebration are we dressing? | yes |
| 1 | garment | Which garment calls to you? | yes |
| 2 | culture | Whose traditions shape the look? | no |
| 3 | silhouette | Which silhouette speaks to you? (options depend on garment) | no |
| 4 | colours | Which colours are yours? (three sub-questions) | no |
| 5 | fabrics | What should it be made of? (multi, max 3) | no |
| 6 | embroidery | Which handwork do you love? (multi, max 4) | no |
| 7 | richness | How richly should it be worked? | no |
| 8 | neckline | How should it frame your face? | no |
| 9 | sleeves | How much sleeve? | no |
| 10 | back | How should the back be cut? | no |
| 11 | midriff | And the midriff? | no |
| 12 | head | Will you cover your head? | no |
| 13 | drape / dupatta | How should it flow? (drape if garment = saree, else dupatta) | no |
| 14 | inspiration | Which looks inspire you? (multi, max 3 + uploads) | no |
| 15 | review | Summary of the whole brief | — |

**Layout**
- Header: wordmark, one line of reassurance copy, then progress nav.
  - ≥700px: horizontal pill nav, one pill per category (number badge or ✓, label). Locked categories are 55% opacity and disabled until both required answers exist.
  - <700px: "Step N of 9" + 6px accent progress bar + an "All steps" toggle revealing the same list vertically.
- Body: kicker (`Step N of 9 — Category · Question x of y`, 13px uppercase, letter-spacing .09em, `--color-accent-700`), then the question as an `h1`.
- Each question is a `<fieldset>` with a `<legend>` (heading face, 24px), a `role="status"` limit note (13px, `--color-accent-2-700`) and helper copy (15px, `--color-neutral-800`, max 62ch).
- Card grid: 2 columns below 700px, 3 above; gap `--space-3`.
- Footer row: Back (secondary) · hint text · Skip (ghost, hidden on required screens) · Continue (primary, disabled until required answers exist). Separated by a 1px `--color-neutral-200` rule.

**Option card**
`<label>` wrapping a visually hidden native radio/checkbox (accessibility is on the native control, not the div). Padding `--space-2`, `border-radius:var(--radius-md)`, 2px border (`transparent` → `--color-accent` when selected), background `--color-neutral-100` → `--color-accent-100` when selected. Hover: `translateY(-2px)` + `--shadow-md`, 180ms. Focus-within: 2px accent outline, 2px offset. Image fills the top of the card at a per-question aspect ratio, radius `calc(var(--radius-md) - 6px)`, `.washed`. Title row: 15px/600, plus a 22px accent circle with a white tick when selected. An "i" button (26px, top-right, translucent ink, accent on hover) opens the info drawer.

**Per-question frame ratios** (chosen to match the source photography, so nothing crops):

| Question | Aspect | Min column |
|---|---|---|
| ceremony | 3 / 2 | 240px |
| garment, dupatta, inspiration | 3 / 4 | default 150px |
| culture, silhouette, drape | 2 / 3 | default |
| fabrics | 3 / 2 | 150px |
| embroidery, richness | 1 / 1 | 132 / 170px |
| neckline | 5 / 4 | 132px |
| sleeves, back, midriff, head | 4 / 5 | 132px |

**Colour screen (index 4)** — three swatch sections in one screen:
1. **The fabric** — multi, max 2 (`maxFabricColours`), ranked by tap order.
2. **The embroidery** — multi, max 3 (`maxEmbColours`).
3. **The dupatta** — single, preceded by a **"Match the fabric"** pill.

Each section is a wrapping row of 38px circular swatches, gap 10px. Selected = `0 0 0 3px var(--color-bg), 0 0 0 6px var(--color-accent)` ring plus a tick (dark tick on light swatches, white on dark) and, for multi sections, a 19px rank badge at top-right. Light swatches carry `inset 0 0 0 1.5px rgba(32,30,29,.22)` so they read against the cream ground. Hover scales to 1.09.

Each row ends with an **"Any colour"** pill (dashed 2px `--color-neutral-300` border, 19px conic-gradient dot). It toggles an inline panel: a native `<input type="color">` (56px, circular), the live hex, one line of guidance, **Add** (primary) and **Cancel** (ghost). Added colours become permanent swatches available in all three sections; adding one while a section is at its limit replaces the oldest selection. Chosen colours are echoed below as removable chips ("1. Scarlet").

**Palette** — 7 groups × 4: reds & maroons (`#c22b33 #701a2b #4e1620 #b0512f`), pinks & roses (`#e9bcbd #d98a9c #c2377b #b97a7a`), golds & ivories (`#e8a13a #c08a2e #e7d3a8 #f6efe0`), greens (`#1d6b4f #5d6e33 #8f9a72 #c3c79b`), blues (`#2b3d8f #1d2547 #14606b #aac3da`), purples & wines (`#46203f #7c4a78 #b9a1c6 #5b2340`), silvers & pastels (`#c9c9cf #efe9e4 #cfe0d3 #f2cdb4`).

**Inspiration screen** — 12 preset looks (multi, max 3) plus **Upload your own**: a dashed pill wrapping a hidden `<input type="file" accept="image/*" multiple>`. Uploads render as a `minmax(120px,1fr)` grid of 3:4 thumbnails, each with a 26px remove button. In the prototype files are held as object URLs; in production upload to your own storage and keep the returned URL.

**Info drawer** — bottom sheet over a `rgba(32,30,29,.45)` scrim: 44px grab handle, title (heading face 24px), body (16px/1.6), meta line (13px, `--color-accent-700`, 700). Focus is trapped, Escape closes, focus returns to the trigger. Slides up 280ms.

**Review screen** — rows of `label · value · Edit`, on a `--color-neutral-100` panel with `--radius-lg`. Unanswered rows read "Left to Sitara's imagination" in `--color-accent-2-700`. Rows: Ceremony, Garment, Cultural direction, Silhouette, Fabric colour, Embroidery colour, Dupatta colour, Fabrics, Embellishment, Richness, Neckline, Sleeves, Back, Midriff, Head covering, Drape/Dupatta, Inspiration, Your uploads, Your note. Ends with "Begin designing" (primary) and "Start over" (ghost).

### 3. Generation — `Sitara Generation.dc.html`

Three phases — Preparing, Design brief, Visual concept — with an animated progress bar, auto-advancing over roughly 5 seconds, then on to the concept. Replace the timer with your real generation job; keep the three-phase copy so the wait feels narrated.

### 4. Concept — `Sitara Concept.dc.html`

Two columns: a sticky 3:4 render on the left; on the right, collapsible specification cards (all collapsed on load) covering the brief. Actions: "Edit answers" and "Refine this concept".

### 5. Amendments — `Sitara Amendments.dc.html`

Free-form amendment requests. Unlimited changes per round, but only **3 regenerations total**; the control locks afterwards with a screen-reader announcement.

### 6. History — `Sitara History.dc.html`

Current design large, previous versions in a grid below, each labelled "Original concept" / "Refinement n" with a version number.

---

## Interactions & behaviour

- **Navigation:** Continue / Back move one screen; Skip is Continue on optional screens. Category pills jump to the first screen of that category. Every change scrolls to top smoothly. Answers persist in memory across navigation — nothing is lost when moving back.
- **Gating:** Categories beyond Occasion stay locked until both ceremony and garment are chosen; Continue is disabled on required screens with no answer, and the footer hint changes to "Choose a ceremony and a garment to continue."
- **Dependent options:** Changing garment clears silhouette, drape and dupatta. Silhouette options are per-garment; saree gets the Drape question, everything else gets Dupatta.
- **Multi-select limits:** fabrics 3, embroidery 4, inspiration 3, fabric colours 2, embroidery colours 3. At the limit, further taps are ignored (except the colour picker, which replaces the oldest).
- **Animation:** screens enter with `sitaraUp` (18px rise + fade, 350ms); drawer and picker use `sitaraFade` 180–200ms.
- **Responsive:** single fluid column, 1020px max; card grid 2→3 columns at 700px; progress nav swaps to the mobile bar at the same breakpoint.
- **Accessibility:** native radios/checkboxes inside labels, `fieldset`/`legend` per question, `aria-pressed` on swatch buttons, `role="status"` on limit notes, `role="progressbar"` on the mobile bar, focus trap + Escape on the drawer, 2px accent `:focus-visible` rings throughout.

## State

```
step, maxReached, navOpen, picker (which colour picker is open), draft (hex)
answers: {
  ceremony, garment, culture, silhouette,
  fabricColours[], embColours[], dupattaColour ('match' | id), customColours[],
  fabrics[], embroidery[], richness,
  neckline, sleeves, back, midriff, head,
  drape, dupatta,
  inspiration[], uploads[{id,url,name}], note
}
```

Only `ceremony` and `garment` are required. Everything else may be null/empty and is rendered as "Left to Sitara's imagination" — the generation prompt should treat absent answers as free choice.

Tunable limits (exposed as props in the prototype): `maxFabricColours` 2, `maxEmbColours` 3, `maxFabrics` 3, `maxEmbroidery` 4, `showHelpers` true.

## Design tokens

From `_ds/organic-…/styles.css` — use the variables, not the literals:

- **Colour:** ground `--color-bg` #f5ead8, ink `--color-text` #201e1d, accent `--color-accent` #c67139, second accent `--color-accent-2` #7a8a5e. Each role has a 100–900 ramp (e.g. `--color-accent-100` tinted fills, `-700/-800` text on tints). Neutrals `--color-neutral-100…900`.
- **Type:** `--font-heading` (Caprasimo) for headings, `--font-body` (Figtree) for text; Cormorant Garamond for the wordmark and page `h1`s. Sizes used: 42/30 (h1 clamp), 24 (legend), 16 (body), 15 (helper), 13.5–13 (meta), 11.5–12 (labels).
- **Spacing:** `--space-1…8`.
- **Radius:** `--radius-md` cards, `--radius-lg` panels and images, `999px` pills and swatches.
- **Shadow:** `--shadow-sm/md/lg`.
- Body copy in the accent must use `--color-accent-700` or darker for contrast.

## Assets

All photography is AI-generated for this project and ships in the bundle:

- `uploads/images/02-ceremonies` — ceremony scene shots (3:2)
- `uploads/images/03-garments` — garment portraits (3:4)
- `uploads/images/04-cultural-styling` — regional styling (2:3)
- `uploads/images/05-silhouettes/*` — per-garment shapes (2:3)
- `uploads/images/06-fabrics` (3:2), `07-embroidery` (1:1) — material and handwork macros
- `uploads/images/08-necklines` (5:4), `09-sleeves`, `10-back-coverage`, `11-midriff` (4:5), `12-head-covering` (4:5)
- `uploads/images/13-saree-drapes`, `14-dupatta-styling` — drape and dupatta styling
- `uploads/gaps` — later additions: gap-filling silhouettes, richness macros, hijab, dupatta styling, ceremony scenes
- `uploads/*.png|jpg` — loose one-off replacements (cap sleeve, semi-sheer midriff, front-open and jacket-style anarkali)

The exact filename for every option lives in the `imgMap()` method of `Sitara Questionnaire.dc.html` — the authoritative mapping of option id → image path. Read that when wiring your own asset pipeline.

**Known gaps** (cards intentionally show an empty slot): straight-cut lehenga, pre-stitched saree, angrakha kameez, long-line kameez. Icons elsewhere are Lucide at stroke-width 2.75.

## Files

- `Sitara Home.dc.html` — landing
- `Sitara Questionnaire.dc.html` — the 16-screen questionnaire (also contains all option data, copy and the image map)
- `Sitara Generation.dc.html` — generation wait states
- `Sitara Concept.dc.html` — generated concept and specification
- `Sitara Amendments.dc.html` — refinement requests, 3-regeneration limit
- `Sitara History.dc.html` — version history
- `_ds/organic-…/` — design system stylesheet and bundle (tokens live here)
- `image-slot.js` — the prototype's drop-in image placeholder component; not needed in production
- `support.js` — prototype template runtime; **do not port**
