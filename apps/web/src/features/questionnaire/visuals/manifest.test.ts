import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { _internal, COLOUR_GROUP_ORDER, colourSwatch, optionVisual } from "./manifest";

const { COLOUR_MANIFEST, VISUAL_MANIFEST } = _internal;

// vitest runs with cwd = the web package root.
const publicPath = (assetPath: string) => join(process.cwd(), "public", assetPath);

// Scope note: whether the manifest COVERS the schema (no option without a
// visual, no visual without an option) is asserted on the backend, in the
// package that owns the schema — see
// apps/api/sitara/questionnaire/tests/test_visual_keys.py. This file asserts
// what the web package owns: that every declared visual is a safe, local file
// whose bytes still match the reviewed hash, and that cards within a question
// share one aspect.

describe("questionnaire visual manifest", () => {
  it("has no key collision between colour and photographic entries", () => {
    const colourKeys = new Set(Object.keys(COLOUR_MANIFEST));
    for (const key of Object.keys(VISUAL_MANIFEST)) {
      expect(colourKeys.has(key)).toBe(false);
    }
  });

  it("ships only approved project-owned assets (no development placeholders)", () => {
    for (const entry of Object.values(COLOUR_MANIFEST)) {
      expect(entry.rightsStatus).toBe("project_owned");
    }
    for (const entry of Object.values(VISUAL_MANIFEST)) {
      expect(entry.rightsStatus).toBe("project_owned");
    }
  });

  // NOTE: schema v4 also replaces the colour vocabulary and its group ids
  // wholesale (29 keys across 8 groups, including the non-solid
  // `colour_match_fabric`). COLOUR_MANIFEST still carries the v3 vocabulary
  // because ColourSwatchGrid's group ordering and tests are bound to it — both
  // move together in the colour-selector slice, which adds the matching
  // bidirectional colour contract test.

  describe("photographic option visuals", () => {
    const entries = Object.entries(VISUAL_MANIFEST);

    // A bare count, deliberately. The authoritative bidirectional contract —
    // every v4 option with a `visual_key` has a shipped asset and vice versa —
    // lives in the package that owns the schema
    // (apps/api/sitara/questionnaire/tests/test_visual_keys.py), because this
    // package cannot read the Django fixture. This is the frontend's canary: it
    // fails if the manifest is regenerated without the schema moving with it.
    //
    // 104 = the original 62 plus the 42 that ceremony, regional_style, fabrics,
    // embellishment_styles and embellishment_density gained. Those five
    // questions shipped text-only while their source photography sat unused
    // under images/. Two options stay text-only on purpose — "No specific
    // regional direction" and "No embellishment" describe an absence.
    it("declares the full v4 option set", () => {
      expect(entries.length).toBe(104);
    });

    it.each(entries)("%s is a safe, local, existing, integrity-checked asset", (_key, entry) => {
      // Local path only — never a remote URL or a traversal.
      expect(entry.path.startsWith("/questionnaire-visuals/")).toBe(true);
      expect(entry.path).not.toContain("..");
      expect(entry.path).not.toMatch(/^https?:/);
      expect(entry.path.endsWith(".webp")).toBe(true);
      expect(entry.kind).toBe("photo");
      // Bounded, positive intrinsic dimensions and a non-empty alt.
      expect(entry.width).toBeGreaterThan(0);
      expect(entry.height).toBeGreaterThan(0);
      expect(entry.alt.trim().length).toBeGreaterThan(0);
      expect(entry.contentHash).toMatch(/^[0-9a-f]{64}$/);
      // Provenance is recorded and stays inside the project's own image set.
      expect(entry.sourceNote).toContain("images/");
      expect(entry.sourceNote).not.toContain("..");
      // The file exists and its content hash matches (integrity).
      const file = publicPath(entry.path.replace(/^\//, ""));
      expect(existsSync(file)).toBe(true);
      const actual = createHash("sha256").update(readFileSync(file)).digest("hex");
      expect(actual).toBe(entry.contentHash);
    });

    it("ships every question group, so a partial build cannot pass quietly", () => {
      // The count above would catch a build that dropped a group, but only by
      // arithmetic — "expected 104, got 92" does not say WHICH question lost
      // its photography. Naming the groups makes a partial build report itself.
      const groups = new Set(entries.map(([, entry]) => entry.path.split("/")[2]));
      expect([...groups].sort()).toEqual([
        "back-coverage",
        "ceremonies",
        "cultural-styling",
        "dupatta",
        "embellishment-density",
        "embroidery",
        "fabrics",
        "garments",
        "head-covering",
        "midriff",
        "necklines",
        "saree-drapes",
        "silhouettes",
        "sleeves",
      ]);
    });

    it("gives every option within one question group a single card aspect", () => {
      // Cards in a grid must be the same shape, so a group directory may only
      // ever declare one width/height pair.
      const aspectsByGroup = new Map<string, Set<string>>();
      for (const entry of Object.values(VISUAL_MANIFEST)) {
        const group = entry.path.split("/")[2];
        const shape = `${entry.width}x${entry.height}`;
        const seen = aspectsByGroup.get(group) ?? new Set<string>();
        seen.add(shape);
        aspectsByGroup.set(group, seen);
      }
      for (const [group, shapes] of aspectsByGroup) {
        expect(`${group}:${[...shapes].join(",")}`).toBe(`${group}:${[...shapes][0]}`);
      }
    });

    it("frames each question at the aspect the handoff chose for it", () => {
      // Phase 17 Part C/8. Before this, the build script offered only 3:4 and
      // 1:1, so a room-sized ceremony scene was cropped to a portrait plate and
      // a neckline lost the shoulders either side of it. The served asset is cut
      // to exactly these shapes and ChoiceOptionGrid reads the ratio straight
      // off the manifest, so pinning the numbers here pins what the cards look
      // like — a silent revert to one shape for everything fails right here.
      const EXPECTED: Record<string, string> = {
        // 3:2 — scene shots and fields of cloth.
        ceremonies: "720x480",
        fabrics: "720x480",
        // 3:4 — a whole figure.
        garments: "540x720",
        dupatta: "540x720",
        // 2:3 — full length, where the silhouette is the answer.
        "cultural-styling": "480x720",
        silhouettes: "480x720",
        "saree-drapes": "480x720",
        // 1:1 — a surface with no orientation of its own.
        embroidery: "720x720",
        "embellishment-density": "720x720",
        // 5:4 — wide enough to hold both shoulders.
        necklines: "720x576",
        // 4:5 — an upright band of the body.
        sleeves: "576x720",
        "back-coverage": "576x720",
        midriff: "576x720",
        "head-covering": "576x720",
      };
      const actual: Record<string, string> = {};
      for (const entry of Object.values(VISUAL_MANIFEST)) {
        actual[entry.path.split("/")[2]] = `${entry.width}x${entry.height}`;
      }
      expect(actual).toEqual(EXPECTED);
    });

    it("uses the source photograph the design handoff chose for each corrected option", () => {
      // Phase 17 Part C/7. The baseline commit reorganised `images/` and left
      // 26 options pointing at a different photograph from the one the handoff's
      // own imgMap() names — and 15 of those sources at no file at all, which is
      // why the visuals build could not run. Each entry below was opened and
      // checked against what the card has to explain, not inferred from its
      // filename. Pinning the SOURCE (recorded in sourceNote) rather than the
      // hash means this test states an intent a reader can check against the
      // prototype, and survives a re-encode.
      const CORRECTED: Record<string, string> = {
        // Ceremony cards are scene shots in the handoff, not bride portraits.
        ceremony_nikah: "images/02-ceremonies/Nikah.png",
        ceremony_mehndi: "images/02-ceremonies/mendhi.png",
        ceremony_baraat: "images/02-ceremonies/Baraat.png",
        ceremony_walima: "images/02-ceremonies/Walima.png",
        ceremony_pheras: "images/02-ceremonies/Pheras.png",
        ceremony_anand_karaj: "images/gaps/Anand Karaj.png",
        ceremony_reception: "images/02-ceremonies/Reception.png",
        // Later takes of the same garment.
        garment_lehenga: "images/03-garments/sitara__garments__lehenga__v2.jpg",
        garment_gharara: "images/03-garments/sitara__garments__gharara__v2.jpg",
        garment_sharara: "images/03-garments/sitara__garments__sharara__v3.jpg",
        // Sources the reorganisation moved or renamed.
        silhouette_front_open_anarkali: "images/gaps/front-open.png",
        silhouette_jacket_style_anarkali: "images/gaps/jacket-style.png",
        silhouette_angrakha_kameez: "images/02-ceremonies/Angrakha kameez.png",
        neckline_high_neck: "images/08-necklines/sitara__necklines__high_neck__v2.jpg",
        neckline_band_collar: "images/08-necklines/sitara__necklines__band_mandarin_collar__v1.jpg",
        midriff_semi_sheer_midriff: "images/11-midriff/Semi-sheer midriff.png",
        midriff_covered_midriff: "images/11-midriff/sitara__midriff__covered__v1.jpg",
        head_covering_hijab: "images/12-head-covering/Hijab — full coverage, no hair shown.png",
        sleeves_cap_sleeve: "images/09-sleeves/sitara__sleeves__short_sleeves__v2.jpg",
        saree_drape_lehenga_drape:
          "images/13-saree-drapes/sitara__saree_drapes__pinned_pleats__v1.jpg",
        // These two were SWAPPED: each question showed the other's photograph.
        // The embellished bridal shot belongs to head covering; the plain wrap
        // belongs to dupatta styling.
        head_covering_dupatta_over_head:
          "images/12-head-covering/sitara__head_covering__dupatta_over_head__v1.jpg",
        dupatta_head_drape: "images/gaps/Dupatta Over the head.png",
      };
      for (const [key, source] of Object.entries(CORRECTED)) {
        expect(VISUAL_MANIFEST[key], `${key} is missing from the manifest`).toBeDefined();
        expect(VISUAL_MANIFEST[key].sourceNote, `${key} uses the wrong source`).toContain(source);
      }
    });

    it("describes a ceremony card as the occasion it photographs, not as an outfit", () => {
      // The handoff's ceremony photographs show a room, a procession or a pair
      // of hands — no bride is the subject. Alt text that said "bridalwear for
      // a nikah" would describe something the image does not contain.
      for (const [key, entry] of entries) {
        if (!key.startsWith("ceremony_")) continue;
        expect(entry.alt.toLowerCase()).not.toContain("bridalwear");
      }
    });

    it("keeps the gharara and sharara alt text describing distinct constructions", () => {
      // Cultural accuracy: a gharara is fitted to the knee and flares from a
      // joint there; a sharara flares from the waist. These must never be
      // described interchangeably.
      for (const [key, entry] of entries) {
        if (key.includes("gharara")) {
          expect(entry.alt.toLowerCase()).toContain("knee");
        }
        if (key.includes("sharara")) {
          expect(entry.alt.toLowerCase()).toContain("waist");
        }
      }
    });
  });

  describe("colour swatches", () => {
    it("covers the schema v4 colour vocabulary", () => {
      // Which keys exist is asserted BIDIRECTIONALLY against the fixture on the
      // API side (apps/api/sitara/questionnaire/tests/test_visual_keys.py),
      // which owns the schema. This end asserts the shape of what we ship and
      // pins the total, so a silent drop cannot pass unnoticed here either.
      expect(Object.keys(COLOUR_MANIFEST).length).toBe(29);
      for (const key of ["colour_scarlet", "colour_rani_pink", "colour_match_fabric"]) {
        expect(COLOUR_MANIFEST[key]).toBeDefined();
      }
    });

    it("assigns each colour a valid hex (or no colour at all) and a known group", () => {
      const groups = new Set([
        "match",
        "reds_maroons",
        "pinks_roses",
        "golds_ivories",
        "greens",
        "blues",
        "purples_wines",
        "silvers_pastels",
      ]);
      for (const entry of Object.values(COLOUR_MANIFEST)) {
        expect(groups.has(entry.group)).toBe(true);
        if (entry.hex === null) {
          // Exactly one entry is not a colour: schema v4's "Match the fabric".
          expect(entry.visualKey).toBe("colour_match_fabric");
        } else {
          expect(entry.hex).toMatch(/^#[0-9a-f]{6}$/);
        }
      }
    });

    it("places every colour group it declares in the display order", () => {
      // A group missing here does not break — it sorts silently to the end of
      // the palette, which is exactly the kind of change nobody notices in a
      // diff and everybody notices on the screen.
      const declared = new Set(Object.values(COLOUR_MANIFEST).map((entry) => entry.group));
      for (const group of declared) {
        expect(COLOUR_GROUP_ORDER).toContain(group);
      }
    });
  });

  it("returns null for an unknown visual key", () => {
    expect(colourSwatch("colour_not_real")).toBeNull();
    expect(optionVisual("neckline_not_real")).toBeNull();
    expect(colourSwatch(undefined)).toBeNull();
    expect(optionVisual(undefined)).toBeNull();
  });
});
