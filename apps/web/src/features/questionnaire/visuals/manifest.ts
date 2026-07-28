// Frontend-owned, source-controlled manifest of explanatory questionnaire
// visuals (Phase 16B). These visuals only HELP a user understand an option —
// they are never sent to any AI provider and never influence DesignSpec
// generation. Every asset here is project-owned: colour swatches are rendered
// from project-authored hex values (a colour value is not third-party imagery),
// and option photographs are cropped from the project's own source photography
// under `images/` by scripts/build-questionnaire-images.py, which also writes
// the SHA-256 in asset-integrity.json that manifest.test.ts verifies against
// the served bytes. Inspiration-catalogue assets are NEVER reused here. A
// missing or unapproved visual falls back to plain text.

import assetIntegrity from "./asset-integrity.json";
import colourSwatches from "./colour-swatches.json";

export type RightsStatus = "project_owned" | "development_placeholder";

// A colour group's display heading, keyed by the schema's bounded `group`
// machine id. Grouping is presentation only. These are the schema v4 groups;
// apps/api/sitara/questionnaire/tests/test_visual_keys.py asserts that the
// swatch data and the fixture agree in both directions, so a group renamed on
// one side cannot silently lose its heading on the other.
export const COLOUR_GROUP_LABELS: Record<string, string> = {
  match: "Matching",
  reds_maroons: "Reds & maroons",
  pinks_roses: "Pinks & roses",
  golds_ivories: "Golds & ivories",
  greens: "Greens",
  blues: "Blues",
  purples_wines: "Purples & wines",
  silvers_pastels: "Silvers & pastels",
};

export type ColourSwatch = {
  visualKey: string;
  group: string;
  // A CSS colour string, or null for an option that is not a colour at all
  // (schema v4's "Match the fabric"), which the component renders as a
  // deliberately non-solid chip. Project-owned; never a canonical answer.
  hex: string | null;
  rightsStatus: RightsStatus;
  sourceNote: string;
};

export type OptionVisual = {
  visualKey: string;
  path: string;
  width: number;
  height: number;
  alt: string;
  contentHash: string;
  kind: "photo";
  rightsStatus: RightsStatus;
  sourceNote: string;
};

const SWATCH_SOURCE = "Project-authored colour value; Sitara Phase 16B.";

// Keyed by the option `visual_key` (colour_<value>). The hex values are the
// design handoff's own palette, transcribed once into colour-swatches.json so
// the API-side contract test can read them without parsing TypeScript. Every
// swatch is paired with a text label in the UI, so a choice never relies on
// colour alone.
const COLOUR_MANIFEST: Record<string, ColourSwatch> = Object.fromEntries(
  Object.entries(colourSwatches as Record<string, { hex: string | null; group: string }>).map(
    ([visualKey, entry]) => [
      visualKey,
      {
        visualKey,
        group: entry.group,
        hex: entry.hex,
        rightsStatus: "project_owned" as const,
        sourceNote: SWATCH_SOURCE,
      },
    ],
  ),
);

type IntegrityEntry = {
  path: string;
  width: number;
  height: number;
  sha256: string;
  alt: string;
  // Repository-relative path, under `images/`, of the project-owned source
  // photograph this visual was cropped from — kept for rights auditing.
  source: string;
};

const VISUAL_MANIFEST: Record<string, OptionVisual> = Object.fromEntries(
  Object.entries(assetIntegrity as Record<string, IntegrityEntry>).map(([visualKey, entry]) => [
    visualKey,
    {
      visualKey,
      path: entry.path,
      width: entry.width,
      height: entry.height,
      alt: entry.alt,
      contentHash: entry.sha256,
      kind: "photo" as const,
      rightsStatus: "project_owned" as const,
      sourceNote: `Project-owned source photography: images/${entry.source}`,
    },
  ]),
);

export function colourSwatch(visualKey: string | undefined): ColourSwatch | null {
  if (!visualKey) return null;
  return COLOUR_MANIFEST[visualKey] ?? null;
}

export function optionVisual(visualKey: string | undefined): OptionVisual | null {
  if (!visualKey) return null;
  return VISUAL_MANIFEST[visualKey] ?? null;
}

export function colourGroupLabel(group: string): string {
  return COLOUR_GROUP_LABELS[group] ?? group;
}

export const _internal = { COLOUR_MANIFEST, VISUAL_MANIFEST };
