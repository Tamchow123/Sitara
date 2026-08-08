// Guards the one number the browser overlay and the server-composed PNG must
// agree on: the mark colour.
//
// A mark is drawn twice from the same document — once as SVG in the browser
// (MarkShapes.tsx, coloured through the .mark-<palette> classes in
// styles/annotations.css) and once as pixels on the server
// (apps/api/sitara/media/annotation_render.py's MARK_COLOURS). The two are
// hand-mirrored, not generated: there is no shared source a rebrand would
// update for both. So a future retune of --color-accent would leave the emailed
// attachment drawing marks in a colour the stylist never saw on screen, with
// nothing failing — the Python side is only checked against its own golden-byte
// manifest, which would simply be regenerated.
//
// This test closes that gap from the frontend side, following the precedent set
// by design-tokens.test.ts: it reads both real sources and compares them, and
// deliberately throws rather than skipping when a source cannot be found,
// because a silently-passing parity test is worse than no parity test. Like
// design-tokens.test.ts it therefore reads outside apps/web and only works
// where the whole repository is checked out (the host and CI, not the narrower
// tree the web container mounts — see CLAUDE.md §20).

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { PALETTES } from "./limits";

const TOKENS = readFileSync(path.join(__dirname, "..", "..", "app", "styles", "tokens.css"), "utf8");
const ANNOTATIONS_CSS = readFileSync(
  path.join(__dirname, "..", "..", "app", "styles", "annotations.css"),
  "utf8",
);

/** Walk up to the repository root to find the server-side compositor. */
function renderModule(): string {
  const relative = path.join("apps", "api", "sitara", "media", "annotation_render.py");
  let dir = __dirname;
  for (;;) {
    const candidate = path.join(dir, relative);
    if (existsSync(candidate)) return readFileSync(candidate, "utf8");
    const parent = path.dirname(dir);
    if (parent === dir) {
      throw new Error(
        `could not find ${relative} in an ancestor of ${__dirname} — it holds the ` +
          "server-side mark palette this test exists to compare against, and " +
          "passing without it would assert nothing",
      );
    }
    dir = parent;
  }
}

/** `MARK_COLOURS = {"terracotta": (198, 113, 57), ...}` -> hex by palette name. */
function serverPalette(): Record<string, string> {
  const block = renderModule().match(/MARK_COLOURS\s*=\s*\{([^}]*)\}/);
  if (!block) throw new Error("MARK_COLOURS not found in annotation_render.py");
  const entries = [...block[1].matchAll(/"(\w+)":\s*\((\d+),\s*(\d+),\s*(\d+)\)/g)];
  if (!entries.length) throw new Error("MARK_COLOURS parsed but held no (r, g, b) entries");
  return Object.fromEntries(
    entries.map(([, name, r, g, b]) => [
      name,
      "#" +
        [r, g, b]
          .map((channel) => Number(channel).toString(16).padStart(2, "0"))
          .join(""),
    ]),
  );
}

/** `.mark-terracotta { --mark-color: var(--color-accent); }` -> hex by palette. */
function browserPalette(): Record<string, string> {
  // The declaration is searched for INSIDE the rule body rather than required to
  // be the whole of it. Demanding a single-declaration block would mean an
  // unrelated addition — a second per-palette property, or a comment before the
  // closing brace — silently dropped that palette from the result and made this
  // file fail as a colour mismatch, which is a misleading reason to fail and the
  // quickest possible route to someone deleting the test instead of reading it.
  const rules = [...ANNOTATIONS_CSS.matchAll(/\.mark-(\w+)\s*\{([^}]*)\}/g)];
  const declarations = rules.flatMap(([, palette, body]) => {
    const declared = body.match(/--mark-color:\s*var\(--([\w-]+)\)/);
    return declared ? [[palette, declared[1]] as const] : [];
  });
  if (!declarations.length) throw new Error("no .mark-<palette> --mark-color rules found");
  return Object.fromEntries(
    declarations.map(([palette, token]) => {
      const value = TOKENS.match(new RegExp(`--${token}:\\s*(#[0-9a-f]{6})\\s*;`, "i"));
      if (!value) throw new Error(`token --${token} not found (or is not a hex literal)`);
      return [palette, value[1].toLowerCase()];
    }),
  );
}

describe("mark palette parity between the overlay and the server render", () => {
  it("defines the same palette names on both sides", () => {
    expect(Object.keys(serverPalette()).sort()).toEqual(Object.keys(browserPalette()).sort());
  });

  it("resolves every palette to the same colour on both sides", () => {
    // Not styling: the emailed PNG is meant to be the picture the stylist
    // confirmed on screen, so a drift here is a correctness bug, not a polish
    // one. If this fails, one of the two sources was retuned alone.
    expect(serverPalette()).toEqual(browserPalette());
  });

  it("covers every palette the editor offers", () => {
    // PALETTES is what the editor puts in front of a user, aliased from the
    // generated contract. A palette the server can store but cannot draw would
    // fail only at send time, after the annotating was done.
    expect(Object.keys(serverPalette()).sort()).toEqual([...PALETTES].sort());
  });
});
