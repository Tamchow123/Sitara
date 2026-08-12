"use client";

// "Finish and hand back" — ending a walk-in session on a shared shop device
// (Phase 22, ADR 0027).
//
// Sitara runs on a boutique's iPad, and one customer's consultation is followed
// by the next customer's. This is the control that draws the line between them:
// it forgets the workspace, revokes any live phone-handoff code, and returns to
// a neutral start screen.
//
// It does NOT sign the shop out. The account is the boutique's and the next
// customer using it is the intended state — a login screen between every
// customer would cost the stylist a password each time and buy no privacy. And
// it deletes nothing: the concepts are the shop's work product, which it
// decides whether to pass on.
//
// The server is the boundary. The idle timeout does the same thing unprompted,
// because customers walk away and nobody remembers to tap a button. This
// control is the deliberate version of that, not a substitute for it.
//
// What it cannot do, stated here so nobody reads more into the button than it
// delivers: the shop stays signed in, and a signed-in account owns every design
// it has produced (ADR 0027 §1). A design URL still sitting in this browser's
// history therefore still resolves if someone deliberately navigates back to
// it. The full-document replace below is what this side CAN do about that —
// it discards every in-memory copy and takes the current entry out of the back
// stack — and ADR 0027 records the remainder as accepted and bounded.

import { useState } from "react";

import { endWalkInSession } from "@/lib/api";

type Stage = "idle" | "confirming" | "ending" | "failed";

export function FinishAndHandBack() {
  const [stage, setStage] = useState<Stage>("idle");

  const finish = async (): Promise<void> => {
    setStage("ending");
    const result = await endWalkInSession();
    if (!result.ok) {
      // Never claim a hand-back that did not happen. The stylist is about to
      // turn this screen towards someone else on the strength of it.
      setStage("failed");
      return;
    }
    // Only after the server has confirmed — and a full document load rather
    // than router.push(), for two reasons a soft navigation cannot give.
    //
    // It tears down the whole JavaScript context, so every in-memory copy of
    // the previous customer goes with it: the TanStack cache holding their
    // result payload, the short-lived signed image URLs, any annotation state
    // held in a component. router.push() keeps all of that alive in a tab
    // someone else is now holding.
    //
    // And `replace` rather than `assign` so this screen is not one back-press
    // away. It cannot erase the entries before it — no web API can — which is
    // why ADR 0027 records back-navigation to an earlier design URL as an
    // accepted, bounded exposure rather than pretending this closes it.
    window.location.replace("/");
  };

  if (stage === "confirming") {
    return (
      <div className="handback" role="group" aria-label="Finish and hand back">
        {/* A second press rather than a browser confirm dialog: a modal blocks
            the page, reads poorly on a tablet, and this is a control a stylist
            uses several times a day with a customer watching. */}
        <p className="handback-question">Clear this screen for the next customer?</p>
        <button type="button" className="btn btn-secondary" onClick={() => void finish()}>
          Yes, hand back
        </button>
        <button type="button" className="btn btn-ghost" onClick={() => setStage("idle")}>
          Not yet
        </button>
      </div>
    );
  }

  if (stage === "ending") {
    return (
      <p className="handback-status" role="status">
        Clearing the screen…
      </p>
    );
  }

  if (stage === "failed") {
    return (
      <div className="handback" role="group" aria-label="Finish and hand back">
        <p className="handback-status handback-status-error" role="alert">
          The screen could not be cleared. Please try again before handing it
          over.
        </p>
        <button type="button" className="btn btn-secondary" onClick={() => void finish()}>
          Try again
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      className="btn btn-ghost"
      onClick={() => setStage("confirming")}
    >
      Finish and hand back
    </button>
  );
}
