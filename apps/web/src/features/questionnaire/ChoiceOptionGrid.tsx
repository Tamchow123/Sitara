"use client";

// A responsive grid of ChoiceOptionCards for a single_choice (radio) or
// multi_choice (checkbox) question. The parent supplies only currently-allowed
// options, so restricted options are never rendered (and their images never
// fetched). When no option has a visual the cards render text-only — a natural
// text-list fallback with the same semantics.
//
// The grid owns the single info drawer for the question: one option's
// explanation is open at a time, and closing it returns focus to the "i" that
// opened it (InfoDrawer restores the element that was focused on open).

import { useState } from "react";
import type { CSSProperties } from "react";

import { ChoiceOptionCard } from "./ChoiceOptionCard";
import { InfoDrawer } from "./InfoDrawer";
import { optionVisual } from "./visuals/manifest";
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
  const [info, setInfo] = useState<QuestionOption | null>(null);

  // Every card in one question shares one frame shape: the build script crops a
  // question's whole option group to a single aspect, and the asset manifest
  // test asserts that invariant, so the first available visual describes them
  // all. Cards without a visual simply have no frame to size.
  const firstVisual = options.map((option) => optionVisual(option.visual_key)).find(Boolean) ?? null;
  const className = firstVisual ? "choice-grid choice-grid-visual" : "choice-grid";
  const style = firstVisual
    ? ({ "--choice-card-aspect": `${firstVisual.width} / ${firstVisual.height}` } as CSSProperties)
    : undefined;

  return (
    <>
      <div className={className} style={style}>
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
            onShowInfo={setInfo}
          />
        ))}
      </div>
      {info?.description ? (
        <InfoDrawer
          title={info.label}
          body={info.description}
          onClose={() => setInfo(null)}
        />
      ) : null}
    </>
  );
}
