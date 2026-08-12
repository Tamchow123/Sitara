// The ADR 0019 disclosure, written for two screens and rendered by one
// (Phase 22, ADR 0026 as amended 2026-08-12 — the iPad's camera and picker went,
// and its affirmation went with them, so only the customer's phone shows this
// today). Both scopes are still held to the same substance, because the wording
// is what any future device-local path must reuse rather than reinvent.
//
// Phase 22 requires the customer's phone to show "the full ADR 0019 disclosure
// ... in the same words as the iPad, not a shortened version. A phone screen is
// smaller; that is a layout problem, not a licence to abbreviate."
//
// Asserted structurally rather than by comparing two hand-written strings,
// because a test that compares two copies passes right up until someone edits
// both. There is ONE component, and these tests hold it to the substance ADR
// 0019 requires it to disclose.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RIGHTS_AFFIRMATION_LABEL, RightsDisclosure } from "./RightsDisclosure";

/** The clauses that make the exposure what it is. Softening any one of them
 *  changes what a person is consenting to, so each is named individually. */
const REQUIRED_CLAUSES = [
  /sent to the external AI image provider/i,
  /perpetual, irrevocable licence/i,
  /train and improve their technology/i,
  /no time limit on how long they keep it/i,
  /unresolved whether those terms differ when Sitara reaches them through Replicate/i,
  /cannot undo that once an image is sent/i,
  /not one that shows someone who has not agreed to it/i,
];

describe("the rights disclosure", () => {
  it.each(["own-device", "handoff"] as const)(
    "states every ADR 0019 clause in the %s scope",
    (scope) => {
      render(<RightsDisclosure id="d" scope={scope} />);

      for (const clause of REQUIRED_CLAUSES) {
        expect(screen.getByText(clause, { exact: false })).toBeInTheDocument();
      }
    },
  );

  it("says the same thing about the provider on both screens", () => {
    // The only sentence allowed to differ is the privacy one: the iPad says
    // "your design"; the phone is addressing someone who has not seen it.
    const own = render(<RightsDisclosure id="a" scope="own-device" />);
    const ownProvider = screen.getByText(/Before you add a photograph/i).textContent;
    own.unmount();

    render(<RightsDisclosure id="b" scope="handoff" />);
    const handoffProvider = screen.getByText(/Before you add a photograph/i).textContent;

    expect(handoffProvider).toBe(ownProvider);
  });

  it("tells each reader where their photograph is not going", () => {
    const own = render(<RightsDisclosure id="a" scope="own-device" />);
    expect(screen.getByText(/never added to Sitara's catalogue/i)).toBeInTheDocument();
    own.unmount();

    render(<RightsDisclosure id="b" scope="handoff" />);
    expect(screen.getByText(/never added to Sitara's catalogue/i)).toBeInTheDocument();
  });

  it("keeps the affirmation label one shared string", () => {
    // Both screens render this constant, so the phone cannot end up affirming
    // something narrower than the iPad does.
    expect(RIGHTS_AFFIRMATION_LABEL).toMatch(/I have the right to use these images/i);
    expect(RIGHTS_AFFIRMATION_LABEL).toMatch(/sent to the AI image provider on the terms above/i);
  });

  it("never calls the affirmation a verification", () => {
    // CLAUDE.md §13: a per-upload self-affirmation is deliberately weaker than
    // the staff-verified catalogue model, and retiring the stronger one does
    // not upgrade this one. The copy must not drift into claiming otherwise.
    render(<RightsDisclosure id="d" scope="handoff" />);
    const text = (screen.getByText(/Before you add a photograph/i).textContent ?? "").toLowerCase();

    for (const forbidden of ["verified", "verification", "cleared", "approved rights"]) {
      expect(text).not.toContain(forbidden);
    }
    expect(RIGHTS_AFFIRMATION_LABEL.toLowerCase()).not.toContain("verified");
  });
});
