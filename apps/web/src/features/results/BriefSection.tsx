"use client";

// One collapsible specification card on the concept screen
// (`Sitara Concept.dc.html`): a real <button aria-expanded> whose panel is a
// sibling, never a <div> with a click handler and never an interactive control
// nested inside another. Closed cards show a one-line summary so the column is
// scannable without opening anything.

import type { ReactNode } from "react";

type Props = {
  id: string;
  title: string;
  /** Shown only while collapsed — a glance at what is inside. */
  summary: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
};

export function BriefSection({ id, title, summary, open, onToggle, children }: Props) {
  const headingId = `${id}-heading`;
  const panelId = `${id}-panel`;
  return (
    <section
      className={open ? "disclosure brief-section disclosure-open" : "disclosure brief-section"}
      aria-labelledby={headingId}
    >
      {/* The heading wraps the button so the card is a landmark in the heading
          outline as well as a control — a screen-reader user can reach the
          eight sections by heading and open the one she wants. */}
      <h2 className="brief-section-heading" id={headingId}>
        <button
          type="button"
          className="disclosure-trigger"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={onToggle}
        >
          <span className="brief-section-text">
            <span className="brief-section-title">{title}</span>
            {open ? null : <span className="brief-section-summary">{summary}</span>}
          </span>
          <span className="disclosure-chevron" aria-hidden="true">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.75"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M6 9 L12 15 L18 9" />
            </svg>
          </span>
        </button>
      </h2>
      {/* Unmounted while closed rather than kept in the DOM behind `hidden`.
          A `hidden` panel is display:none, so it is out of reach of in-page
          find and of assistive technology alike — keeping it mounted would buy
          nothing and would put the card's summary and its body in the document
          twice. */}
      {open ? (
        <div className="disclosure-body" id={panelId}>
          {children}
        </div>
      ) : null}
    </section>
  );
}
