import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ConceptsPage from "./page";

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

  it("describes exactly one refinement, constrained to one area", () => {
    render(<ConceptsPage />);
    const text = pageText();
    expect(text).toMatch(/refined once/i);
    expect(text).toMatch(/one area/i);
    // The prototype's "as many changes as you like" must not survive anywhere.
    expect(text).not.toMatch(/as many (changes|refinements)/i);
    expect(text).not.toMatch(/unlimited/i);
    expect(text).not.toMatch(/3 refinements/i);
  });

  it("never presents refinement as editing the original image", () => {
    render(<ConceptsPage />);
    const text = pageText();
    expect(text).toMatch(/not an edit of the first one/i);
    expect(text).toMatch(/never sent anywhere or altered/i);
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
});
