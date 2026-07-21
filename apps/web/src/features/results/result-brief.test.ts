import { describe, expect, it } from "vitest";

import { formatDesignBrief } from "./result-brief";
import type { DesignResult } from "@/lib/api";

const RESULT: DesignResult = {
  design_id: "d1-secret-id",
  design_version_id: "v1-secret-id",
  version_number: 1,
  title: "Ivory and gold flared lehenga",
  concept_summary: "A concept summary describing the overall look.",
  garment_breakdown: {
    overall_form: "A fitted choli with a full flared skirt.",
    garment_components: ["Choli", "Lehenga skirt", "Dupatta"],
    silhouette: "Fitted bodice, wide flare.",
    drape_or_layering: "Dupatta over the head.",
    key_proportions: "Close bodice, defined waist.",
  },
  colour_story: {
    palette_summary: "Ivory with gold accents.",
    placement: "Ivory base, gold embroidery.",
    rationale: "Calm and bridal.",
  },
  fabrics_and_texture: [
    { fabric: "Silk", placement: "Skirt", finish_and_movement: "Smooth drape." },
    { fabric: "Organza", placement: "Dupatta", finish_and_movement: "Sheer float." },
  ],
  embellishment_plan: {
    techniques: ["Zardozi", "Dabka"],
    density: "Balanced.",
    placement: ["Hem", "Bodice"],
    motifs: ["Floral"],
    restraint_notes: "Open ground between motifs.",
  },
  coverage_and_drape: {
    sleeves: "Full length.",
    neckline: "Modest higher neckline.",
    back_and_midriff: "Covered.",
    head_covering: "Dupatta worn over the head.",
    dupatta_or_saree_drape: "Draped over the head.",
  },
  cultural_context: {
    regional_direction: "Pakistani",
    interpretation_notes: ["Treated as one broad regional direction."],
    safeguards: ["No specific community presented as universal."],
  },
  styling_notes: ["Keep jewellery warm-toned.", "Centre-parted hairstyle."],
  construction_caveats: [
    "This is a concept visualisation, not a sewing pattern.",
    "It does not guarantee the garment can be constructed exactly as shown.",
  ],
  image_alt_text: "A model in an ivory flared lehenga.",
  created_at: "2026-07-19T12:00:00Z",
  inspiration_acknowledgements: [],
  lineage: { kind: "initial", parent_version_id: null, refinement: null },
  is_demo: false,
};

describe("formatDesignBrief", () => {
  it("includes the title, concept summary and every rendered section", () => {
    const text = formatDesignBrief(RESULT);
    expect(text).toContain(RESULT.title);
    expect(text).toContain(RESULT.concept_summary);
    expect(text).toContain("Garment breakdown");
    expect(text).toContain(RESULT.garment_breakdown.overall_form);
    expect(text).toContain("Choli");
    expect(text).toContain("Colour story");
    expect(text).toContain(RESULT.colour_story.palette_summary);
    expect(text).toContain("Fabrics and texture");
    expect(text).toContain("Silk");
    expect(text).toContain("Organza");
    expect(text).toContain("Embellishment plan");
    expect(text).toContain("Zardozi");
    expect(text).toContain("Coverage and drape");
    expect(text).toContain(RESULT.coverage_and_drape.sleeves);
    expect(text).toContain("Cultural context");
    expect(text).toContain("Pakistani");
    expect(text).toContain("Styling notes");
    expect(text).toContain("Keep jewellery warm-toned.");
    expect(text).toContain("Construction caveats");
    expect(text).toContain("not a sewing pattern");
  });

  it("includes the generic concept-only disclaimer", () => {
    const text = formatDesignBrief(RESULT);
    expect(text).toMatch(/AI-assisted visual concept/i);
    expect(text).toMatch(/not a photograph/i);
    expect(text).toMatch(/does not guarantee/i);
  });

  it("includes a concise demo disclosure when is_demo, with no internal provenance", () => {
    const demoResult = { ...RESULT, is_demo: true };
    const text = formatDesignBrief(demoResult);
    expect(text).toMatch(/curated demo pack/i);
    expect(text).toMatch(/not newly generated/i);
    expect(text).not.toMatch(/demo-asset/i);
    expect(text).not.toMatch(/seed/i);
    expect(text).not.toMatch(/manifest/i);
  });

  it("omits the demo disclosure for a live result", () => {
    const text = formatDesignBrief(RESULT);
    expect(text).not.toMatch(/curated demo pack/i);
  });

  it("handles a null regional direction without a Regional direction line", () => {
    const noRegional = {
      ...RESULT,
      cultural_context: { ...RESULT.cultural_context, regional_direction: null },
    };
    const text = formatDesignBrief(noRegional);
    expect(text).not.toContain("Regional direction:");
  });

  it("renders multiple fabric entries", () => {
    const text = formatDesignBrief(RESULT);
    expect(text).toContain("- Silk (Skirt): Smooth drape.");
    expect(text).toContain("- Organza (Dupatta): Sheer float.");
  });

  it("excludes ids, signed URLs, and any provider/storage metadata", () => {
    const text = formatDesignBrief(RESULT);
    expect(text).not.toContain(RESULT.design_id);
    expect(text).not.toContain(RESULT.design_version_id);
    expect(text).not.toMatch(/https?:\/\//);
    expect(text).not.toMatch(/version_number/i);
    expect(text).not.toMatch(/prompt/i);
    expect(text).not.toMatch(/provider/i);
    expect(text).not.toMatch(/storage/i);
  });

  it("is deterministic: the same input produces the same output", () => {
    expect(formatDesignBrief(RESULT)).toBe(formatDesignBrief(RESULT));
  });

  it("omits the inspiration acknowledgements section when there are none", () => {
    const text = formatDesignBrief(RESULT);
    expect(text).not.toContain("Inspiration acknowledgements");
  });

  it("includes acknowledgements, attribution and the metadata-only limitation when present", () => {
    const withInspiration: DesignResult = {
      ...RESULT,
      inspiration_acknowledgements: [
        { position: 1, title: "Emerald look", attribution: "Studio A" },
        { position: 2, title: "Rose gold look", attribution: "" },
      ],
    };
    const text = formatDesignBrief(withInspiration);
    expect(text).toContain("Inspiration acknowledgements");
    expect(text).toContain("- Emerald look — Studio A");
    expect(text).toContain("- Rose gold look");
    expect(text).not.toContain("Rose gold look — ");
    expect(text).toMatch(/staff-curated descriptions/i);
    expect(text).toMatch(/not sent to the generation models/i);
  });

  it("excludes asset ids, provider cues, alt text, cultural context and URLs from acknowledgements", () => {
    const withInspiration: DesignResult = {
      ...RESULT,
      inspiration_acknowledgements: [
        { position: 1, title: "Emerald look", attribution: "Studio A" },
      ],
    };
    const text = formatDesignBrief(withInspiration);
    expect(text).not.toMatch(/garment_type/i);
    expect(text).not.toMatch(/visual_description/i);
    expect(text).not.toMatch(/cultural_context/i);
    expect(text).not.toMatch(/alt_text/i);
    expect(text).not.toMatch(/https?:\/\//);
  });
});
