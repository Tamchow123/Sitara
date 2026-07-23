"use client";

// A concise option description that expands through a real <button> with
// aria-expanded / aria-controls. Selection never depends on expanding — the
// disclosure is purely informational. When there is no description this renders
// nothing.

import { useId, useState } from "react";

export function ExpandableOptionDescription({
  description,
  label,
}: {
  description: string | undefined;
  label: string;
}) {
  const regionId = useId();
  const [open, setOpen] = useState(false);
  if (!description) return null;
  return (
    <span className="option-disclosure">
      <button
        type="button"
        className="option-disclosure-toggle"
        aria-expanded={open}
        aria-controls={regionId}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? "Hide details" : "Details"}
        <span className="visually-hidden"> for {label}</span>
      </button>
      <span id={regionId} className="option-description" hidden={!open}>
        {description}
      </span>
    </span>
  );
}
