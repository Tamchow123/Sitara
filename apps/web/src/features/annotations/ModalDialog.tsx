"use client";

// A focus-trapped modal, following `features/questionnaire/InfoDrawer.tsx` —
// including its reason for a <div> over the native <dialog>: `showModal()` is not
// implemented in jsdom, so the native element would leave the trapped-focus
// behaviour the accessibility tests exist to prove unverified.
//
// Only the two dialogs §12/§14 call modals use this: clear-all and conflict. The
// unsaved-leave warning is an anchored popover instead, because it belongs beside
// the control that triggered it rather than over the work.

import { useEffect, useId, useRef } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

type Props = {
  title: string;
  body: ReactNode;
  children: ReactNode;
  onClose: () => void;
  /** Where focus lands on open — the least destructive action. */
  initialFocusRef: React.RefObject<HTMLButtonElement | null>;
};

export function ModalDialog({ title, body, children, onClose, initialFocusRef }: Props) {
  const titleId = useId();
  const bodyId = useId();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    initialFocusRef.current?.focus();
    return () => {
      trigger?.focus();
    };
  }, [initialFocusRef]);

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key === "Escape") {
      // Stopped here so the workspace's own Escape handling (return to select
      // mode, cancel a mark) does not also fire behind the dialog.
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
    const first = focusable[0]!;
    const last = focusable[focusable.length - 1]!;
    const active = document.activeElement;
    // The panel itself holds focus (tabIndex -1), so both directions treat it as
    // an edge — otherwise Tab from the panel matches no branch, nothing is
    // prevented, and focus escapes to the page behind the scrim.
    if (event.shiftKey && (active === first || active === panel)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || active === panel)) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="annotation-scrim" onKeyDown={onKeyDown}>
      <div
        className="annotation-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        ref={panelRef}
        tabIndex={-1}
      >
        <h2 className="annotation-dialog-title" id={titleId}>
          {title}
        </h2>
        <div className="annotation-dialog-body" id={bodyId}>
          {body}
        </div>
        <div className="annotation-dialog-actions">{children}</div>
      </div>
    </div>
  );
}
