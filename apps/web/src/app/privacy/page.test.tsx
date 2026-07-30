import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import PrivacyPage from "./page";
import { axeViolations } from "@/test-utils/axe";

// This page is the one place a bride can go to find out what happens to an
// image she uploads. Most of what follows tests for the ABSENCE of comfortable
// sentences the repository's own rules forbid: an invented retention period, a
// deletion promise, or any wording that describes the ADR 0019 reference-image
// exposure as though it had been removed.

function pageText(): string {
  return document.body.textContent ?? "";
}

describe("privacy page", () => {
  it("leads with private-by-default and says there is no public gallery", () => {
    render(<PrivacyPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      /designs are private by default/i,
    );
    expect(pageText()).toMatch(/no public gallery/i);
  });

  it("explains anonymous ownership, account claiming and the indistinguishable 404", () => {
    render(<PrivacyPage />);
    const text = pageText();
    expect(text).toMatch(/browser session/i);
    expect(text).toMatch(/claimed by your account/i);
    // Knowing the address must not be described as granting access.
    expect(text).toMatch(/does not exist/i);
  });

  it("says an upload stays out of the shared catalogue and is not verified rights", () => {
    render(<PrivacyPage />);
    const text = pageText();
    expect(text).toMatch(/never added to Sitara's shared inspiration catalogue/i);
    // The per-upload self-affirmation must never be presented as verified
    // rights. The page may mention the catalogue's staff-verified record, but
    // only to say the upload confirmation is weaker than it.
    expect(text).toMatch(/not a rights check/i);
    expect(text).toMatch(/deliberately weaker/i);
    expect(text).not.toMatch(/your (uploads?|images?)[^.]{0,40}rights are verified/i);
  });

  it("states that selected reference images are sent to the image provider", () => {
    render(<PrivacyPage />);
    const text = pageText();
    expect(text).toMatch(/reference images you selected/i);
    expect(text).toMatch(/perpetual, irrevocable licence/i);
    // The exposure was accepted and recorded — never removed. Any of these
    // would be the false reassurance ADR 0019 and CLAUDE.md §13 forbid.
    expect(text).not.toMatch(/never sent to (an|the) (AI )?provider\b/i);
    expect(text).not.toMatch(/images are not sent/i);
    expect(text).not.toMatch(/not sent to the generation models/i);
  });

  it("promises no retention period, deletion window or provider guarantee", () => {
    render(<PrivacyPage />);
    const text = pageText();
    expect(text).toMatch(/publishes no retention window/i);
    expect(text).toMatch(/cannot promise a reference image you send is deleted/i);
    // No invented number of days/months, and no deletion guarantee.
    expect(text).not.toMatch(/\b\d+\s*(days?|weeks?|months?|years?)\b/i);
    expect(text).not.toMatch(/we (will )?delete/i);
    expect(text).not.toMatch(/guarantee[ds]? (?:to be )?(?:deleted|removed|private)/i);
  });

  it("says demo mode sends nothing at all", () => {
    render(<PrivacyPage />);
    expect(pageText()).toMatch(/in demo mode.*nothing leaves the machine/is);
  });

  it("names what is never sent to a provider", () => {
    render(<PrivacyPage />);
    const text = pageText();
    for (const item of [/storage location/i, /attribution/i, /rights record/i]) {
      expect(text).toMatch(item);
    }
  });

  it("describes a signed image link honestly, as a bearer link that cannot be withdrawn", () => {
    render(<PrivacyPage />);
    const text = pageText();
    expect(text).toMatch(/temporary link/i);
    expect(text).toMatch(/cannot withdraw one early/i);
    // A signed URL must never be described as revocable or unshareable. The
    // bare word "revocable" is not the test — "irrevocable licence" appears
    // legitimately above, describing the provider's terms — so this looks for
    // the claim itself.
    expect(text).not.toMatch(/links?[^.]{0,30}\b(revocable|can be revoked)\b/i);
    expect(text).not.toMatch(/only you can open/i);
  });

  it("keeps one h1 and links onward to the concept page", () => {
    render(<PrivacyPage />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("link", { name: /AI-assisted concept is/i })).toHaveAttribute(
      "href",
      "/concepts",
    );
  });

  it("has no axe violations", async () => {
    const { container } = render(<PrivacyPage />);
    expect(await axeViolations(container)).toHaveNoViolations();
  });
});
