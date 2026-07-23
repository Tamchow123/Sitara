// Deterministic generator for the project-owned questionnaire neckline
// illustrations and their integrity manifest. These are simple, original
// schematic line drawings authored in this repository (no third-party or
// downloaded imagery), written to apps/web/public/questionnaire-visuals/ and
// integrity-recorded in src/features/questionnaire/visuals/asset-integrity.json.
//
//   node scripts/generate-questionnaire-visuals.mjs
//
// Re-run after editing a shape below and commit both the SVGs and the JSON.

import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = join(HERE, "..");
const PUBLIC_DIR = join(WEB_ROOT, "public", "questionnaire-visuals", "necklines");
const INTEGRITY_PATH = join(
  WEB_ROOT,
  "src",
  "features",
  "questionnaire",
  "visuals",
  "asset-integrity.json",
);

const W = 120;
const H = 150;

// Each neckline is drawn as the same shoulder/torso frame with a distinct
// neckline path. Deterministic strings only — no randomness, no timestamps.
const FRAME_A = `<path d="M12 40 C 30 26, 90 26, 108 40 L 108 140 L 12 140 Z" fill="#f4ece1" stroke="#8d3f5e" stroke-width="2"/>`;
const NECKLINES = {
  classic_crew: `<path d="M46 40 Q60 58 74 40" fill="#fdf9f3" stroke="#8d3f5e" stroke-width="2.5"/>`,
  curved_scoop: `<path d="M42 40 Q60 72 78 40" fill="#fdf9f3" stroke="#8d3f5e" stroke-width="2.5"/>`,
  v_neck: `<path d="M44 40 L60 66 L76 40" fill="#fdf9f3" stroke="#8d3f5e" stroke-width="2.5"/>`,
  deep_v_neck: `<path d="M42 40 L60 96 L78 40" fill="#fdf9f3" stroke="#8d3f5e" stroke-width="2.5"/>`,
  boat_neck: `<path d="M34 42 Q60 52 86 42" fill="#fdf9f3" stroke="#8d3f5e" stroke-width="2.5"/>`,
  square_neck: `<path d="M44 40 L44 66 L76 66 L76 40" fill="#fdf9f3" stroke="#8d3f5e" stroke-width="2.5"/>`,
  sweetheart_neck: `<path d="M44 40 Q52 62 60 52 Q68 62 76 40" fill="#fdf9f3" stroke="#8d3f5e" stroke-width="2.5"/>`,
  high_neck: `<path d="M50 34 Q60 44 70 34" fill="#fdf9f3" stroke="#8d3f5e" stroke-width="2.5"/>`,
  band_collar: `<path d="M48 40 L48 30 Q60 26 72 30 L72 40" fill="#fdf9f3" stroke="#8d3f5e" stroke-width="2.5"/>`,
};

const NECKLINE_ALT = {
  classic_crew: "Schematic of a classic crew neckline at the base of the neck.",
  curved_scoop: "Schematic of a curved scoop neckline dipping below the collarbone.",
  v_neck: "Schematic of a moderate V-shaped neckline.",
  deep_v_neck: "Schematic of a deep V-shaped neckline plunging below the collarbone.",
  boat_neck: "Schematic of a wide boat neckline across the shoulders.",
  square_neck: "Schematic of a square neckline cut across the chest.",
  sweetheart_neck: "Schematic of a sweetheart neckline shaped like the top of a heart.",
  high_neck: "Schematic of a high neckline rising on the neck.",
  band_collar: "Schematic of an upright band or mandarin collar.",
};

mkdirSync(PUBLIC_DIR, { recursive: true });

const integrity = {};
for (const [value, path] of Object.entries(NECKLINES)) {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" ` +
    `role="img" aria-label="${NECKLINE_ALT[value]}">` +
    FRAME_A +
    path +
    `</svg>\n`;
  const filename = `${value}.svg`;
  writeFileSync(join(PUBLIC_DIR, filename), svg, "utf-8");
  integrity[`neckline_${value}`] = {
    path: `/questionnaire-visuals/necklines/${filename}`,
    width: W,
    height: H,
    sha256: createHash("sha256").update(svg, "utf-8").digest("hex"),
    alt: NECKLINE_ALT[value],
  };
}

const sorted = Object.fromEntries(Object.keys(integrity).sort().map((k) => [k, integrity[k]]));
writeFileSync(INTEGRITY_PATH, JSON.stringify(sorted, null, 2) + "\n", "utf-8");
console.log(`Wrote ${Object.keys(integrity).length} neckline SVG(s) and asset-integrity.json`);
