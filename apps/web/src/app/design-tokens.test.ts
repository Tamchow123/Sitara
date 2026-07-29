// Guards the contrast contract documented at the top of styles/tokens.css.
//
// The Organic ramp is transcribed from the design handoff rather than
// imported, so nothing stops a future retune from quietly dropping a text
// token below AA on the cream ground. These tests read the real stylesheet
// and recompute WCAG 2.x contrast, so a regression fails here rather than in
// a manual accessibility pass.
//
// The handoff's own .btn-primary (accent ground + bg text) measures 3.03:1;
// we deliberately use --color-accent-700 for that instead, and the
// "decorative only" assertions below record why the lighter steps exist.

import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// globals.css is only an import manifest; the rules live in styles/*.css.
// Read the manifest plus every partial so these guards keep applying no matter
// which partial a token or rule is moved into — and so a NEW partial is covered
// the moment it is added, rather than silently escaping the contrast contract.
const STYLES_DIR = path.join(__dirname, "styles");
const PARTIALS = readdirSync(STYLES_DIR)
  .filter((name) => name.endsWith(".css"))
  .sort()
  .map((name) => ({ name, source: readFileSync(path.join(STYLES_DIR, name), "utf8") }));
const CSS = [
  readFileSync(path.join(__dirname, "globals.css"), "utf8"),
  ...PARTIALS.map((partial) => partial.source),
].join("\n");

/**
 * Locate the vendored handoff stylesheet that globals.css transcribes from.
 *
 * Walks up from this file rather than hard-coding a depth, because the repo
 * root sits at a different level depending on where the suite runs: CI checks
 * the whole repo out and runs with `working-directory: apps/web`, while a local
 * containerised run mounts a narrower tree.
 *
 * Two candidate paths, newest first: `design_handoff_sitara_flow/` is the
 * bundle Phase 17 is built against and is therefore the binding source, and
 * `design/sitara-handoff/` is the earlier vendored copy Phase 16B transcribed
 * from. The two stylesheets are byte-identical today; the fallback exists so
 * removing the older bundle does not silently unverify this contract, and the
 * ordering means the newer bundle wins if they ever diverge.
 *
 * Deliberately throws rather than skipping — if the source of truth cannot be
 * found the transcription contract below is unverified, and silently passing
 * would defeat the point of the test.
 */
function vendoredStylesheet(): string {
  const candidates = [
    path.join(
      "design_handoff_sitara_flow",
      "_ds",
      "organic-ccb06f12-17fe-45b2-9248-ebf010085646",
      "styles.css",
    ),
    path.join("design", "sitara-handoff", "_ds", "organic-styles.css"),
  ];
  let dir = __dirname;
  for (;;) {
    for (const relative of candidates) {
      const candidate = path.join(dir, relative);
      if (existsSync(candidate)) return readFileSync(candidate, "utf8");
    }
    const parent = path.dirname(dir);
    if (parent === dir) {
      throw new Error(
        `could not find any of ${candidates.join(", ")} in an ancestor of ${__dirname} — ` +
          "the vendored design system is the source of truth for the token block " +
          "in globals.css and must be present for these tests to mean anything",
      );
    }
    dir = parent;
  }
}

/** Pull a literal `--name: #rrggbb;` declaration out of the token block. */
function token(name: string): string {
  const match = CSS.match(new RegExp(`--${name}:\\s*(#[0-9a-f]{6})\\s*;`, "i"));
  if (!match) throw new Error(`token --${name} not found (or is not a hex literal)`);
  return match[1];
}

function channel(value: number): number {
  const c = value / 255;
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function luminance(hex: string): number {
  const n = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => channel(parseInt(n.slice(i, i + 2), 16)));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const AA = 4.5;
const AA_LARGE = 3;

describe("design tokens: text contrast on the cream ground", () => {
  const bg = () => token("color-bg");

  it.each([
    ["color-text", 7],
    ["color-neutral-800", 7],
    ["color-neutral-700", AA],
    ["color-accent-700", AA],
    ["color-accent-2-700", AA],
    ["color-ok", AA],
    ["color-bad", AA],
  ])("--%s clears %s:1 against --color-bg", (name, minimum) => {
    expect(contrast(token(name), bg())).toBeGreaterThanOrEqual(minimum);
  });

  it("keeps body copy legible on the raised neutral-100 surface too", () => {
    const surface = token("color-neutral-100");
    expect(contrast(token("color-text"), surface)).toBeGreaterThanOrEqual(7);
    expect(contrast(token("color-neutral-800"), surface)).toBeGreaterThanOrEqual(AA);
    expect(contrast(token("color-accent-700"), surface)).toBeGreaterThanOrEqual(AA);
  });

  it("gives the primary-button ground AA against the label it carries", () => {
    // .cta paints --color-accent-700 behind --color-bg text.
    expect(contrast(token("color-accent-700"), bg())).toBeGreaterThanOrEqual(AA);
  });
});

describe("design tokens: the decorative accent steps", () => {
  it("documents that --color-accent is below AA, so it is fills-only", () => {
    // Not a defect — this is exactly why the token block forbids small text
    // on it. If a retune ever lifted it past AA the comment would be stale.
    const ratio = contrast(token("color-accent"), token("color-bg"));
    expect(ratio).toBeLessThan(AA);
    expect(ratio).toBeGreaterThanOrEqual(AA_LARGE);
  });

  it("keeps the selection ring distinguishable from the card ground", () => {
    // Selected option cards swap to --color-accent-100 with an accent border;
    // the border must stay visible against that tinted fill.
    expect(contrast(token("color-accent"), token("color-accent-100"))).toBeGreaterThanOrEqual(
      AA_LARGE,
    );
  });
});

describe("design tokens: scale integrity", () => {
  it("exposes an unbroken 1-8 spacing scale on a single step", () => {
    const steps = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => {
      const match = CSS.match(new RegExp(`--space-${n}:\\s*([0-9.]+)px\\s*;`));
      if (!match) throw new Error(`--space-${n} missing`);
      return Number(match[1]);
    });
    steps.forEach((value, index) => {
      expect(value).toBeCloseTo(4.4 * (index + 1), 5);
    });
  });

  it("routes every legacy alias onto the Organic ramp, not stale literals", () => {
    for (const alias of ["ink", "paper", "accent", "ok", "bad", "muted", "line"]) {
      const match = CSS.match(new RegExp(`--${alias}:\\s*([^;]+);`));
      expect(match?.[1].trim(), `--${alias} should alias a --color-* token`).toMatch(
        /^var\(--color-[a-z0-9-]+\)$/,
      );
    }
  });

  it("declares every neutral and accent ramp step the components rely on", () => {
    for (const step of [100, 200, 300, 400, 500, 600, 700, 800, 900]) {
      expect(() => token(`color-neutral-${step}`)).not.toThrow();
      expect(() => token(`color-accent-${step}`)).not.toThrow();
      expect(() => token(`color-accent-2-${step}`)).not.toThrow();
    }
  });
});

describe("design tokens: fidelity to the vendored design system", () => {
  // globals.css transcribes the Organic ramp instead of importing it (the
  // vendored file also carries prototype component classes we do not use, and a
  // Google Fonts @import our CSP forbids). Transcription can drift silently, so
  // compare the two mechanically rather than trusting a code comment.
  const VENDORED = vendoredStylesheet();

  function vendoredToken(name: string): string | null {
    const match = VENDORED.match(new RegExp(`--${name}:\\s*([^;]+);`));
    return match ? match[1].trim() : null;
  }

  const RAMP_STEPS = [100, 200, 300, 400, 500, 600, 700, 800, 900];
  const COLOUR_TOKENS = [
    "color-bg",
    "color-surface",
    "color-text",
    "color-accent",
    "color-accent-2",
    ...RAMP_STEPS.map((s) => `color-neutral-${s}`),
    ...RAMP_STEPS.map((s) => `color-accent-${s}`),
    ...RAMP_STEPS.map((s) => `color-accent-2-${s}`),
  ];

  it.each(COLOUR_TOKENS)("--%s matches the vendored value exactly", (name) => {
    const upstream = vendoredToken(name);
    expect(upstream, `--${name} is missing from the vendored stylesheet`).not.toBeNull();
    expect(token(name).toLowerCase()).toBe(upstream!.toLowerCase());
  });

  it.each([1, 2, 3, 4, 6, 8])("--space-%i matches the vendored value", (step) => {
    // 5 and 7 are ours: the handoff ships an incomplete scale and we
    // interpolate the gaps, so only the shipped steps are compared.
    expect(vendoredToken(`space-${step}`)).toBe(
      CSS.match(new RegExp(`--space-${step}:\\s*([^;]+);`))![1].trim(),
    );
  });

  it.each(["radius-sm", "radius-md", "radius-lg"])("--%s matches the vendored value", (name) => {
    expect(CSS.match(new RegExp(`--${name}:\\s*([^;]+);`))![1].trim()).toBe(vendoredToken(name));
  });
});

describe("design tokens: color-mix() degrades instead of vanishing", () => {
  // A custom property holding an unsupported color-mix() is invalid at
  // computed-value time, so consumers fall back to the property's INITIAL
  // value — box-shadow: none, background: transparent. Each such token must
  // therefore be declared twice, static fallback first.
  it.each(["color-divider", "shadow-sm", "shadow-md", "shadow-lg"])(
    "--%s declares a static fallback before its color-mix() value",
    (name) => {
      const declarations = [...CSS.matchAll(new RegExp(`--${name}:\\s*([^;]+);`, "g"))].map((m) =>
        m[1].trim(),
      );
      expect(declarations, `--${name} should be declared twice`).toHaveLength(2);
      expect(declarations[0]).toContain("rgba(");
      expect(declarations[0]).not.toContain("color-mix");
      expect(declarations[1]).toContain("color-mix");
    },
  );

  it("has no color-mix() token left without a fallback", () => {
    // Catches a NEW color-mix token added later without the pattern above.
    const withMix = new Set(
      [...CSS.matchAll(/(--[a-z0-9-]+):\s*[^;]*color-mix\([^;]*;/g)].map((m) => m[1]),
    );
    for (const name of withMix) {
      const count = [...CSS.matchAll(new RegExp(`${name}:\\s*[^;]+;`, "g"))].length;
      expect(count, `${name} uses color-mix() without a static fallback declaration`).toBe(2);
    }
  });
});

describe("design tokens: the palette lives in exactly one file", () => {
  // Part A/1 of the Phase 17 brief: "Do not scatter copied hex values and
  // spacing literals across route files." Before this guard the feature
  // partials carried a dozen pre-handoff literals (#faf3ee, #fdeceb, #fff,
  // #f0e7db …) that no palette retune could ever reach, which is a large part
  // of why several screens still read as generic white-and-grey. Colour now
  // enters the system only through tokens.css.
  const COLOUR_LITERAL = /#[0-9a-f]{3,8}\b|\brgba?\(|\bhsla?\(/gi;

  it.each(PARTIALS.filter((partial) => partial.name !== "tokens.css").map((p) => p.name))(
    "%s declares no raw colour literal",
    (name) => {
      const source = PARTIALS.find((partial) => partial.name === name)!.source;
      // Comments explain the tokens by name, and naming a literal in prose is
      // not the same as painting with it.
      const code = source.replace(/\/\*[\s\S]*?\*\//g, "");
      expect([...code.matchAll(COLOUR_LITERAL)].map((m) => m[0])).toEqual([]);
    },
  );

  it("keeps every status tint legible under body ink", () => {
    // The tints ground the error and success message blocks, whose copy is
    // --color-text rather than the status colour itself.
    for (const tint of ["color-ok-100", "color-bad-100"]) {
      expect(contrast(token("color-text"), token(tint))).toBeGreaterThanOrEqual(7);
    }
  });

  it("gives --color-on-accent AA against the accent grounds it is drawn on", () => {
    // Ticks, rank badges and pill numerals are small, so AA (not AA-large) is
    // the bar. --color-accent itself is a decorative fill and is excluded by
    // the rule above it; these are the two grounds that actually carry glyphs.
    for (const ground of ["color-accent-700", "color-accent-800"]) {
      expect(contrast(token("color-on-accent"), token(ground))).toBeGreaterThanOrEqual(AA);
    }
  });
});

describe("design tokens: motion honours reduced-motion", () => {
  it("zeroes each motion duration inside the reduced-motion block", () => {
    const block = CSS.match(/@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\n\}/);
    expect(block, "reduced-motion block not found").toBeTruthy();
    for (const name of ["--motion-fast", "--motion-drawer", "--motion-screen"]) {
      expect(block![0]).toContain(`${name}: 0ms`);
    }
  });
});

describe("design tokens: no third-party font requests", () => {
  it("never @imports a remote stylesheet (font-src 'self' CSP)", () => {
    // The handoff's styles.css pulls Caprasimo/Figtree from Google Fonts;
    // transcribing that import here would break the CSP and leak visitor IPs.
    expect(CSS).not.toMatch(/@import\s+url\(/i);
    expect(CSS).not.toMatch(/fonts\.googleapis\.com|fonts\.gstatic\.com/i);
  });

  it("resolves each family through a next/font variable", () => {
    for (const family of ["--font-cormorant", "--font-caprasimo", "--font-figtree"]) {
      expect(CSS).toContain(`var(${family})`);
    }
  });
});
