"use client";

// The handoff's bottom-sheet information drawer: a modal explanation of one
// option, opened from a card's "i" trigger. It is a real modal dialog —
// `role="dialog"` + `aria-modal`, focus moved in on open, Tab cycled inside the
// panel, Escape closes it, and focus returns to whatever opened it. Rendered
// only while open, so the trap and the listeners exist only when they apply.
//
// A <div> dialog rather than the native <dialog>: `showModal()` is not
// implemented in the jsdom environment the accessibility tests run in, so the
// native element would leave exactly the trapped-focus behaviour these tests
// exist to prove unverified.

import { useEffect, useId, useRef } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from "react";

type Props = {
  title: string;
  body: string;
  onClose: () => void;
};

// Everything that can hold focus inside the panel. Deliberately not filtered by
// visibility: the panel's own content is the whole trap, and jsdom reports no
// layout, so a visibility filter would empty this list under test.
const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function InfoDrawer({ title, body, onClose }: Props) {
  const titleId = useId();
  const bodyId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Capture the trigger on mount (React has not moved focus yet at this point,
  // so document.activeElement is still the element that opened the drawer) and
  // restore it on unmount, which is also how the drawer closes.
  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    return () => {
      trigger?.focus();
    };
  }, []);

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key === "Escape") {
      // Stop here rather than letting an ancestor also react to the same Escape.
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const panel = panelRef.current;
    if (!panel) return;
    const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    // The panel itself is a valid focus holder (tabIndex -1, and a pointer
    // press on the title, body or handle lands there), so BOTH directions have
    // to treat it as an edge — otherwise Tab from the panel matches no branch,
    // nothing is prevented, and focus escapes to the page behind the scrim.
    if (event.shiftKey && (active === first || active === panel)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || active === panel)) {
      event.preventDefault();
      first.focus();
    }
  };

  // Only a press that both starts and ends on the scrim itself dismisses, so a
  // drag that happens to finish outside the panel does not close it.
  const onScrimMouseDown = (event: ReactMouseEvent<HTMLDivElement>): void => {
    if (event.target === event.currentTarget) onClose();
  };

  return (
    <div className="info-scrim" onMouseDown={onScrimMouseDown} onKeyDown={onKeyDown}>
      <div
        className="info-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        ref={panelRef}
        tabIndex={-1}
      >
        <span className="info-drawer-handle" aria-hidden="true" />
        <h2 className="info-drawer-title" id={titleId}>
          {title}
        </h2>
        <p className="info-drawer-body" id={bodyId}>
          {body}
        </p>
        <button type="button" className="info-drawer-close" ref={closeRef} onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}
