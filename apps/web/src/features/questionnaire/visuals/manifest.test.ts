import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { _internal, colourGroupLabel, colourSwatch, illustration } from "./manifest";

const { COLOUR_MANIFEST, ILLUSTRATION_MANIFEST } = _internal;

// vitest runs with cwd = the web package root.
const publicPath = (assetPath: string) => join(process.cwd(), "public", assetPath);

describe("questionnaire visual manifest", () => {
  it("has no key collision between colour and illustration entries", () => {
    const colourKeys = new Set(Object.keys(COLOUR_MANIFEST));
    const illustrationKeys = Object.keys(ILLUSTRATION_MANIFEST);
    for (const key of illustrationKeys) {
      expect(colourKeys.has(key)).toBe(false);
    }
  });

  it("ships only approved project-owned assets (no development placeholders)", () => {
    for (const entry of Object.values(COLOUR_MANIFEST)) {
      expect(entry.rightsStatus).toBe("project_owned");
    }
    for (const entry of Object.values(ILLUSTRATION_MANIFEST)) {
      expect(entry.rightsStatus).toBe("project_owned");
    }
  });

  describe("illustrations", () => {
    const entries = Object.entries(ILLUSTRATION_MANIFEST);
    it("declares at least the nine necklines", () => {
      expect(entries.length).toBeGreaterThanOrEqual(9);
    });

    it.each(entries)("%s is a safe, local, existing, integrity-checked asset", (_key, entry) => {
      // Local path only — never a remote URL or a traversal.
      expect(entry.path.startsWith("/questionnaire-visuals/")).toBe(true);
      expect(entry.path).not.toContain("..");
      expect(entry.path).not.toMatch(/^https?:/);
      // Bounded, positive intrinsic dimensions and a non-empty alt.
      expect(entry.width).toBeGreaterThan(0);
      expect(entry.height).toBeGreaterThan(0);
      expect(entry.alt.trim().length).toBeGreaterThan(0);
      expect(entry.contentHash).toMatch(/^[0-9a-f]{64}$/);
      // The file exists and its content hash matches (integrity).
      const file = publicPath(entry.path.replace(/^\//, ""));
      expect(existsSync(file)).toBe(true);
      const actual = createHash("sha256").update(readFileSync(file)).digest("hex");
      expect(actual).toBe(entry.contentHash);
    });
  });

  describe("colour swatches", () => {
    it("covers the expanded v3 colour vocabulary", () => {
      const expected = [
        "colour_ivory",
        "colour_ruby",
        "colour_burgundy",
        "colour_coral",
        "colour_dusty_rose",
        "colour_forest_green",
        "colour_powder_blue",
        "colour_royal_blue",
        "colour_lilac",
        "colour_mauve",
        "colour_taupe",
        "colour_multicolour",
      ];
      for (const key of expected) {
        expect(COLOUR_MANIFEST[key]).toBeDefined();
      }
      expect(Object.keys(COLOUR_MANIFEST).length).toBeGreaterThanOrEqual(41);
    });

    it("assigns each colour a valid hex (or the multicolour flag) and a known group", () => {
      const groups = new Set([
        "neutrals",
        "reds",
        "pinks",
        "yellows_metallics",
        "greens",
        "blues_teals",
        "purples",
      ]);
      for (const entry of Object.values(COLOUR_MANIFEST)) {
        expect(groups.has(entry.group)).toBe(true);
        if (entry.multicolour) {
          expect(entry.hex).toBeNull();
        } else {
          expect(entry.hex).toMatch(/^#[0-9a-f]{6}$/i);
        }
      }
    });

    it("labels every colour group", () => {
      for (const group of ["neutrals", "reds", "pinks", "purples"]) {
        expect(colourGroupLabel(group)).not.toBe(group);
      }
    });
  });

  it("returns null for an unknown visual key", () => {
    expect(colourSwatch("colour_not_real")).toBeNull();
    expect(illustration("neckline_not_real")).toBeNull();
    expect(colourSwatch(undefined)).toBeNull();
    expect(illustration(undefined)).toBeNull();
  });
});
