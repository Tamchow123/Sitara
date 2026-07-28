import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { _internal, colourGroupLabel, colourSwatch, optionVisual } from "./manifest";

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

    it("declares the full v4 option set", () => {
      expect(entries.length).toBe(62);
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

    it("labels every colour group it declares", () => {
      const declared = new Set(Object.values(COLOUR_MANIFEST).map((entry) => entry.group));
      for (const group of declared) {
        expect(colourGroupLabel(group)).not.toBe(group);
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
