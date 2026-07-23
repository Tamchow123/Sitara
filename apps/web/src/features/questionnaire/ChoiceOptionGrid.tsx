"use client";

// A responsive grid of ChoiceOptionCards for a single_choice (radio) or
// multi_choice (checkbox) question. The parent supplies only currently-allowed
// options, so restricted options are never rendered (and their images never
// fetched). When no option has a visual the cards render text-only — a natural
// text-list fallback with the same semantics.

import { ChoiceOptionCard } from "./ChoiceOptionCard";
import { illustration } from "./visuals/manifest";
import type { QuestionOption } from "./types";

type Props = {
  options: QuestionOption[];
  name: string;
  type: "radio" | "checkbox";
  selected: string[];
  disabledValues?: Set<string>;
  onToggle: (value: string, checked: boolean) => void;
  onBlur?: () => void;
};

// The enclosing <fieldset>/<legend> provides the group semantics for both radios
// and checkboxes; a radio's ``name`` (shared with the no-preference control that
// renders as a sibling) makes it one native group, so no extra radiogroup role
// is needed here (and it would wrongly exclude that sibling).
export function ChoiceOptionGrid({
  options,
  name,
  type,
  selected,
  disabledValues,
  onToggle,
  onBlur,
}: Props) {
  const hasVisuals = options.some((option) => illustration(option.visual_key));
  const className = hasVisuals ? "choice-grid choice-grid-visual" : "choice-grid";
  return (
    <div className={className}>
      {options.map((option) => (
        <ChoiceOptionCard
          key={option.value}
          option={option}
          name={name}
          type={type}
          checked={selected.includes(option.value)}
          disabled={disabledValues?.has(option.value) ?? false}
          onChange={(checked) => onToggle(option.value, checked)}
          onBlur={onBlur}
        />
      ))}
    </div>
  );
}
