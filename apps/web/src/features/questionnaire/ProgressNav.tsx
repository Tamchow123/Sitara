"use client";

// The handoff's questionnaire progress navigation. One component renders both
// presentations from ONE list of buttons — the wide pill row and the narrow
// "Step n of m" bar with its "All steps" disclosure — because duplicating the
// list would put two copies of every step in the accessibility tree. Which
// presentation is visible is a pure CSS decision at the 700px breakpoint; the
// disclosure only governs the list below that width, where the stylesheet
// collapses it by default.
//
// Navigation is offered only for categories the wizard says are unlocked, so a
// pill can never skip past a step whose required answers are still missing.

import { useId, useState } from "react";

export type ProgressCategory = {
  // Stable machine id of the category (a schema step id, not a display label).
  id: string;
  label: string;
  // The wizard screen index this category jumps to.
  step: number;
  // Answered/visited already — shown with a tick instead of its number.
  complete: boolean;
  // Not yet reachable: rendered disabled rather than removed, so the shape of
  // the whole journey stays visible.
  locked: boolean;
};

type Props = {
  categories: ProgressCategory[];
  // Index into `categories` of the one being shown now.
  activeIndex: number;
  // A navigation is already in flight: every pill is inert until it settles, so
  // two overlapping jumps cannot race each other. Kept separate from `locked`
  // so a merely-busy pill is never announced as unavailable.
  busy?: boolean;
  onNavigate: (step: number) => void;
};

export function ProgressNav({ categories, activeIndex, busy, onNavigate }: Props) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const total = categories.length;
  // Clamp for display only: a caller that is momentarily out of range must not
  // produce an aria-valuenow outside its own declared min/max.
  const position = Math.min(Math.max(activeIndex + 1, 1), Math.max(total, 1));
  const current = categories[activeIndex];

  return (
    <nav className="progress-nav" aria-label="Questionnaire progress">
      <div className="progress-bar-row">
        <p className="progress-bar-label">
          Step {position} of {total}
          {current ? ` — ${current.label}` : ""}
        </p>
        <div
          className="progress-bar"
          role="progressbar"
          // Named distinctly from the enclosing <nav> so the two are told apart
          // when a screen reader lists landmarks and controls together.
          aria-label="Progress through the questionnaire"
          aria-valuemin={1}
          aria-valuemax={Math.max(total, 1)}
          aria-valuenow={position}
          aria-valuetext={`Step ${position} of ${total}${current ? `: ${current.label}` : ""}`}
        >
          <span
            className="progress-bar-fill"
            style={{ width: `${(position / Math.max(total, 1)) * 100}%` }}
          />
        </div>
        <button
          type="button"
          className="progress-toggle"
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => setOpen((value) => !value)}
        >
          All steps
        </button>
      </div>
      <ol className="progress-steps" id={listId} data-open={open}>
        {categories.map((category, index) => (
          <li key={category.id}>
            <button
              type="button"
              className={index === activeIndex ? "progress-pill progress-pill-active" : "progress-pill"}
              aria-current={index === activeIndex ? "step" : undefined}
              disabled={category.locked || busy === true}
              onClick={() => onNavigate(category.step)}
            >
              <span className="progress-pill-badge" aria-hidden="true">
                {category.complete ? "✓" : index + 1}
              </span>
              {category.label}
              {category.locked ? <span className="visually-hidden"> (not yet available)</span> : null}
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}
