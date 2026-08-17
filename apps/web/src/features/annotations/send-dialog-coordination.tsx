"use client";

// One send dialog at a time, per screen.
//
// Until the comparison view gained its actions there was never more than one
// "Send to account" control on a page, so nothing had to coordinate them.
// ModalDialog's containment is a Tab cycle plus a fixed scrim, which stops a
// mouse and stops Tab but not a screen reader's browse mode: that walks the DOM
// to the other card's Send and opens a second aria-modal dialog on top of the
// first. ModalDialog restores focus to the `document.activeElement` it captured
// at mount, so closing the second one returns focus INTO the first rather than
// to a trigger.
//
// The thorough fix is a portal plus `inert` on the rest of the document, and
// that is deliberately NOT what this is. `inert` is unimplemented in jsdom, so
// every test that could prove it would only be asserting an attribute string,
// and portalling out of the RTL container would silently stop the existing
// `axeViolations(container)` assertions from auditing the dialog at all — a
// green test checking nothing. That refactor deserves its own slice with real
// browser coverage.
//
// This is the small provable thing instead: the screen that renders two send
// controls lends them a claim, and a control nobody holds the claim for renders
// disabled. A disabled button is out of the tab order AND out of the browse-mode
// action set, which is the actual hole. No provider means no coordination, so
// every existing single-send surface is untouched.

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

type Coordinator = {
  /** True when this key may open its dialog: nobody holds the claim, or it does. */
  mayOpen: (key: string) => boolean;
  claim: (key: string) => void;
  release: (key: string) => void;
};

// The default is deliberately permissive rather than absent. A send button
// outside any provider is the single-control case, which needs no coordination
// and must not become conditional on a provider being remembered.
const ALWAYS_ALLOWED: Coordinator = {
  mayOpen: () => true,
  claim: () => {},
  release: () => {},
};

const SendDialogCoordinationContext = createContext<Coordinator>(ALWAYS_ALLOWED);

export function SendDialogCoordinator({ children }: { children: ReactNode }) {
  const [heldBy, setHeldBy] = useState<string | null>(null);

  const claim = useCallback((key: string) => {
    // First claim wins. Two simultaneous claims cannot happen from user input
    // (one dialog must be open before its sibling is reachable), and refusing
    // to overwrite means a stale release can never strand the other control.
    setHeldBy((current) => current ?? key);
  }, []);

  const release = useCallback((key: string) => {
    setHeldBy((current) => (current === key ? null : current));
  }, []);

  const value = useMemo<Coordinator>(
    () => ({
      mayOpen: (key: string) => heldBy === null || heldBy === key,
      claim,
      release,
    }),
    [heldBy, claim, release],
  );

  return (
    <SendDialogCoordinationContext.Provider value={value}>
      {children}
    </SendDialogCoordinationContext.Provider>
  );
}

export function useSendDialogCoordination(): Coordinator {
  return useContext(SendDialogCoordinationContext);
}
