import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import heroes from "./landing-hero.json";

// Integrity guard for the landing hero assets, matching what
// visuals/manifest.test.ts does for the questionnaire visuals.
//
// page.test.tsx imports this same JSON and asserts the rendered <img> matches
// it — which proves the page and the manifest agree, and nothing more. If the
// committed .webp bytes drift from the manifest that describes them (a source
// swapped without rerunning the build script, a hand-edited manifest, a
// half-finished rebuild), that test stays green and the wrong image ships.
// This one reads the bytes off disk and hashes them, so it cannot.
//
// CI additionally rebuilds both asset pipelines and fails on any diff; this is
// the local half of the same contract.

// vitest runs with cwd = the web package root.
const publicPath = (assetPath: string) => join(process.cwd(), "public", assetPath);

const entries = Object.entries(heroes);

describe("landing hero assets", () => {
  it("ships exactly the two heroes the composition uses", () => {
    expect(entries.map(([key]) => key)).toEqual(["hero-1", "hero-2"]);
  });

  it.each(entries)("%s is a local file whose bytes match the recorded hash", (_key, hero) => {
    // Local path only — never a remote URL or a traversal.
    expect(hero.path.startsWith("/landing/")).toBe(true);
    expect(hero.path).not.toContain("..");
    expect(hero.path).not.toMatch(/^https?:/);
    expect(hero.path.endsWith(".webp")).toBe(true);
    expect(hero.sha256).toMatch(/^[0-9a-f]{64}$/);

    const file = publicPath(hero.path.replace(/^\//, ""));
    expect(existsSync(file), `${hero.path} is missing from public/`).toBe(true);
    expect(createHash("sha256").update(readFileSync(file)).digest("hex")).toBe(hero.sha256);
  });

  it.each(entries)("%s is cut to the handoff's 2:3 hero frame", (_key, hero) => {
    expect(hero.width / hero.height).toBeCloseTo(2 / 3, 5);
  });

  it.each(entries)("%s records the project-owned source it was built from", (_key, hero) => {
    // Provenance for rights auditing: every hero comes from the project's own
    // photography under images/, never a download and never a catalogue asset.
    expect(hero.source.startsWith("01-landing-page/")).toBe(true);
    expect(hero.source).not.toContain("..");
  });
});
