"use client";

// One choice option rendered as an accessible card wrapping a REAL radio or
// checkbox input (never a div pretending to be one). Selected state is conveyed
// by the native input plus a border/check indicator — never colour alone. An
// approved project-owned illustration is shown when the option's visual_key maps
// to one; otherwise the card falls back to text. Hidden/restricted options are
// never rendered by the parent, so their images are never fetched.

import { useState } from "react";

import { illustration } from "./visuals/manifest";
import { ExpandableOptionDescription } from "./ExpandableOptionDescription";
import type { QuestionOption } from "./types";

type Props = {
  option: QuestionOption;
  name: string;
  type: "radio" | "checkbox";
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
  onBlur?: () => void;
};

export function ChoiceOptionCard({ option, name, type, checked, disabled, onChange, onBlur }: Props) {
  // If the illustration fails to load at runtime, degrade to the same text-only
  // presentation used when an option has no visual at all.
  const [imageFailed, setImageFailed] = useState(false);
  const visual = imageFailed ? null : illustration(option.visual_key);
  const className = [
    "choice-card",
    checked ? "choice-card-selected" : "",
    disabled ? "choice-card-disabled" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <label className={className}>
      <input
        type={type}
        name={name}
        value={option.value}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        onBlur={onBlur}
      />
      {visual ? (
        // Decorative here: the adjacent title fully communicates the choice, so
        // the alt is empty to avoid double-announcing. Fixed intrinsic size
        // reserves space so lazy loading never shifts layout. Plain <img>, never
        // next/image, matching the questionnaire's other local-asset usage.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          className="choice-card-visual"
          src={visual.path}
          alt=""
          width={visual.width}
          height={visual.height}
          loading="lazy"
          decoding="async"
          onError={() => setImageFailed(true)}
        />
      ) : null}
      <span className="choice-card-body">
        <span className="choice-card-title">{option.label}</span>
        <ExpandableOptionDescription description={option.description} label={option.label} />
      </span>
    </label>
  );
}
