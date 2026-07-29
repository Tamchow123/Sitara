import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DesignBrief } from "./DesignBrief";
import type { DesignResult } from "@/lib/api";

function result(overrides: Partial<DesignResult> = {}): DesignResult {
  return {
    design_id: "d1-secret-id",
    design_version_id: "v1-secret-id",
    version_number: 1,
    title: "Ivory and gold flared lehenga",
    concept_summary: "A concept summary describing the overall look.",
    garment_breakdown: {
      overall_form: "A fitted choli with a full flared skirt.",
      garment_components: ["Choli", "Lehenga skirt"],
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
    ],
    embellishment_plan: {
      techniques: ["Zardozi"],
      density: "Balanced.",
      placement: ["Hem"],
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
    styling_notes: ["Keep jewellery warm-toned."],
    construction_caveats: [
      "This is a concept visualisation, not a sewing pattern.",
      "It does not guarantee the garment can be constructed exactly as shown.",
    ],
    image_alt_text: "A model in an ivory flared lehenga with gold embroidery.",
    created_at: "2026-07-19T12:00:00Z",
    inspiration_acknowledgements: [],
    lineage: { kind: "initial", parent_version_id: null, refinement: null },
    is_demo: false,
    ...overrides,
  };
}

describe("DesignBrief — demo disclosure", () => {
  it("shows the demo disclaimer when the result is_demo", () => {
    render(<DesignBrief result={result({ is_demo: true })} />);
    const disclaimer = screen.getByRole("note", { name: /demo disclaimer/i });
    expect(disclaimer).toHaveTextContent(/curated demo pack/i);
    expect(disclaimer).toHaveTextContent(/not newly generated/i);
  });

  it("does not show the demo disclaimer for a live result", () => {
    render(<DesignBrief result={result({ is_demo: false })} />);
    expect(screen.queryByRole("note", { name: /demo disclaimer/i })).not.toBeInTheDocument();
  });

  it("keeps the concept disclaimer alongside the demo disclaimer", () => {
    render(<DesignBrief result={result({ is_demo: true })} />);
    expect(screen.getByRole("note", { name: /concept disclaimer/i })).toBeInTheDocument();
    expect(screen.getByRole("note", { name: /demo disclaimer/i })).toBeInTheDocument();
  });
});

// Every specification card starts collapsed, exactly as the handoff's concept
// screen does, and a collapsed card does not render its panel at all. These
// tests open the card they are about instead of asserting on content no user
// could reach yet.
function openCard(name: RegExp | string) {
  fireEvent.click(screen.getByRole("button", { name }));
}

describe("DesignBrief — inspiration acknowledgements", () => {
  it("renders no acknowledgement section when the list is empty", () => {
    render(<DesignBrief result={result()} />);
    expect(screen.queryByText("Inspiration acknowledgements")).not.toBeInTheDocument();
  });

  it("renders one acknowledgement with its attribution", () => {
    render(
      <DesignBrief
        result={result({
          inspiration_acknowledgements: [
            { position: 1, title: "Emerald look", attribution: "Studio A" },
          ],
        })}
      />,
    );
    expect(screen.getByText("Inspiration acknowledgements")).toBeInTheDocument();
    openCard(/Inspiration acknowledgements/i);
    expect(screen.getByText("Emerald look")).toBeInTheDocument();
    expect(screen.getByText(/Studio A/)).toBeInTheDocument();
  });

  it("renders three acknowledgements in selection order", () => {
    render(
      <DesignBrief
        result={result({
          inspiration_acknowledgements: [
            { position: 1, title: "First look", attribution: "Studio A" },
            { position: 2, title: "Second look", attribution: "Studio B" },
            { position: 3, title: "Third look", attribution: "" },
          ],
        })}
      />,
    );
    openCard(/Inspiration acknowledgements/i);
    const items = screen.getAllByRole("listitem").filter((li) =>
      ["First look", "Second look", "Third look"].some((title) =>
        li.textContent?.includes(title),
      ),
    );
    expect(items.map((li) => li.textContent)).toEqual([
      expect.stringContaining("First look"),
      expect.stringContaining("Second look"),
      expect.stringContaining("Third look"),
    ]);
  });

  it("handles an empty attribution without a trailing separator", () => {
    render(
      <DesignBrief
        result={result({
          inspiration_acknowledgements: [
            { position: 1, title: "Unattributed look", attribution: "" },
          ],
        })}
      />,
    );
    openCard(/Inspiration acknowledgements/i);
    const item = screen.getByText("Unattributed look").closest("li");
    expect(item?.textContent).toBe("Unattributed look");
  });

  it("escapes attribution text rather than rendering markup", () => {
    render(
      <DesignBrief
        result={result({
          inspiration_acknowledgements: [
            { position: 1, title: "A look", attribution: "<script>alert(1)</script>" },
          ],
        })}
      />,
    );
    openCard(/Inspiration acknowledgements/i);
    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
    expect(document.querySelector("script")).not.toBeInTheDocument();
  });

  it("never shows the asset UUID or provider cues", () => {
    render(
      <DesignBrief
        result={result({
          inspiration_acknowledgements: [
            { position: 1, title: "Emerald look", attribution: "Studio A" },
          ],
        })}
      />,
    );
    openCard(/Inspiration acknowledgements/i);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
    expect(text).not.toMatch(/garment_type/i);
    expect(text).not.toMatch(/visual_description/i);
  });

  it("tells the truth about what happened to a selected reference image", () => {
    // This copy used to say the source images "were not sent to the generation
    // models". ADR 0019 reversed that for the references a user actually picks,
    // and CLAUDE.md forbids describing the exposure as removed anywhere. A
    // stale reassurance here is worse than no reassurance: it is the sentence
    // a bride would rely on when deciding what to upload.
    render(
      <DesignBrief
        result={result({
          inspiration_acknowledgements: [
            { position: 1, title: "Emerald look", attribution: "Studio A" },
          ],
        })}
      />,
    );
    openCard(/Inspiration acknowledgements/i);
    expect(screen.getByText(/sent to the AI image provider/i)).toBeInTheDocument();
    expect(screen.queryByText(/not sent to the generation models/i)).toBeNull();
  });

  it("says nothing was sent to a provider when the concept came from the demo pack", () => {
    render(
      <DesignBrief
        result={result({
          is_demo: true,
          inspiration_acknowledgements: [{ position: 1, title: "Emerald look", attribution: "" }],
        })}
      />,
    );
    openCard(/Inspiration acknowledgements/i);
    expect(screen.getByText(/no image .* was sent to an AI provider/i)).toBeInTheDocument();
  });
});
