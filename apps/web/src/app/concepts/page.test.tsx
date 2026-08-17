import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ConceptsPage from "./page";
import { axeViolations } from "@/test-utils/axe";

function pageText(): string {
  return document.body.textContent ?? "";
}

describe("concepts page", () => {
  it("states plainly that a concept is not a pattern and is not guaranteed constructible", () => {
    render(<ConceptsPage />);
    const text = pageText();
    expect(text).toMatch(/not a sewing pattern|does not produce sewing patterns/i);
    expect(text).toMatch(/does not guarantee/i);
    expect(text).toMatch(/impossible to realise/i);
  });

  it("describes three refinements, each constrained to one area", () => {
    // This test used to assert /refined once/ AND not.toMatch(/3 refinements/i),
    // which is why it stayed green when ADR 0029 raised the budget to three: it
    // was enforcing the very claim that had become false. The page is the
    // canonical explanation of what a concept is, linked from the footer of
    // every screen, so a stale count here is a promise broken on the shop floor.
    render(<ConceptsPage />);
    const text = pageText();
    expect(text).toMatch(/refined up to three times/i);
    expect(text).toMatch(/one area/i);
    // Still bounded, and still one area per round — the two things that were
    // true before and remain true.
    expect(text).not.toMatch(/as many (changes|refinements)/i);
    expect(text).not.toMatch(/unlimited/i);
    // And the superseded claim must not come back.
    expect(text).not.toMatch(/refined once/i);
    expect(text).not.toMatch(/may be refined only/i);
  });

  it("says the rounds chain rather than each starting from the original", () => {
    // The behaviour a customer is most likely to be surprised by: round two
    // refines round one's OUTPUT. If the page omitted this, someone would
    // reasonably expect three independent variations of the first concept.
    render(<ConceptsPage />);
    const text = pageText();
    expect(text).toMatch(/starts from the concept the first one produced/i);
    expect(text).toMatch(/most recent concept/i);
  });

  it("never presents refinement as editing the image it came from", () => {
    render(<ConceptsPage />);
    const text = pageText();
    // "the one before it", not "the first one": from round two the image a
    // refinement starts from is itself a refinement (ADR 0029).
    expect(text).toMatch(/not an edit of the one before it/i);
    expect(text).not.toMatch(/not an edit of the first one/i);
    // The safety claim is unchanged in strength and now covers every version.
    expect(text).toMatch(/no concept image is ever sent anywhere or altered/i);
    expect(text).toMatch(/continuity is an aim, not a promise/i);
  });

  it("keeps regional influence a direction, not a claim about a community", () => {
    render(<ConceptsPage />);
    const text = pageText();
    // The distinctions CLAUDE.md §12 requires stay intact and unflattened.
    expect(text).toMatch(/gharara is not a sharara/i);
    expect(text).toMatch(/saree is not a lehenga/i);
    expect(text).toMatch(/never as a statement about how any community dresses/i);
  });

  it("says the result is not a reproduction, and rules designer names out", () => {
    render(<ConceptsPage />);
    const text = pageText();
    expect(text).toMatch(/not a\s+reproduction/i);
    expect(text).toMatch(/designer and brand names are not part of what you can ask for/i);
  });

  it("keeps one h1 and links to the privacy page for the reference-image answer", () => {
    render(<ConceptsPage />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(
      screen.getByRole("link", { name: /happens to a reference image you select/i }),
    ).toHaveAttribute("href", "/privacy");
  });

  it("has no axe violations", async () => {
    const { container } = render(<ConceptsPage />);
    expect(await axeViolations(container)).toHaveNoViolations();
  });
});
