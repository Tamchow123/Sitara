"use client";

// One accessible question control, rendered entirely from the schema. Choice
// groups use a semantic <fieldset>/<legend> with real radio/checkbox inputs,
// now composed from focused presentation components (ChoiceOptionGrid,
// ColourSwatchGrid, NoPreferenceControl) rather than one growing conditional.
// Text uses a labelled <textarea>. Help text and errors are associated via
// aria-describedby. Only currently-allowed options are shown, so active
// restrictions are reflected immediately. No option label or limit is
// hard-coded here.

import { useId } from "react";

import { ChoiceOptionGrid } from "./ChoiceOptionGrid";
import { ColourSwatchGrid } from "./ColourSwatchGrid";
import { NoPreferenceControl } from "./NoPreferenceControl";
import { colourSwatch } from "./visuals/manifest";
import type { AnswerValue, Question } from "./types";

type Props = {
  question: Question;
  value: AnswerValue | undefined;
  error?: string;
  allowed: Set<string>;
  onChange: (value: AnswerValue) => void;
  onBlur?: () => void;
  // The design's own colour palette — the sibling `colour_list` answer, shared
  // by every colour question, plus the bound and the setter for adding to it.
  // Supplied by the wizard, which owns cross-question answers.
  customColours?: string[];
  customColourMax?: number;
  onAddCustomColour?: (hex: string) => void;
};

export function QuestionField({
  question,
  value,
  error,
  allowed,
  onChange,
  onBlur,
  customColours,
  customColourMax,
  onAddCustomColour,
}: Props) {
  const helpId = useId();
  const errorId = useId();
  const describedBy =
    [question.help_text ? helpId : null, error ? errorId : null].filter(Boolean).join(" ") ||
    undefined;

  const help = question.help_text ? (
    <p id={helpId} className="field-help">
      {question.help_text}
    </p>
  ) : null;
  const errorMessage = error ? (
    <p id={errorId} className="field-error" role="alert">
      {error}
    </p>
  ) : null;

  if (question.type === "text") {
    const max = question.constraints?.max_length;
    return (
      <div className="field">
        <label className="field-label" htmlFor={errorId + "-input"}>
          {question.label}
        </label>
        {help}
        <textarea
          id={errorId + "-input"}
          className="field-textarea"
          value={typeof value === "string" ? value : ""}
          maxLength={typeof max === "number" ? max : undefined}
          aria-describedby={describedBy}
          aria-invalid={error ? true : undefined}
          onChange={(event) => onChange(event.target.value)}
          onBlur={onBlur}
        />
        {errorMessage}
      </div>
    );
  }

  // The design's own palette has no field of its own: it is edited through the
  // "Any colour" picker inside each colour question and rendered there as
  // selectable swatches, exactly as the handoff describes.
  if (question.type === "colour_list") return null;

  const options = (question.options ?? []).filter((option) => allowed.has(option.value));
  const palette = customColours ?? [];

  if (question.type === "colour_choice") {
    // A colour answer is a declared swatch value OR one of the design's own
    // hex colours; both are plain strings, so the single-select grid handles
    // them identically and the backend re-checks membership.
    const current = typeof value === "string" ? value : "";
    return (
      <fieldset
        className="field"
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
      >
        <legend className="field-label">{question.label}</legend>
        {help}
        <ColourSwatchGrid
          options={options}
          name={question.id}
          mode="single"
          selected={current ? [current] : []}
          customColours={palette}
          customMax={customColourMax}
          onAddCustomColour={
            question.constraints?.allow_custom ? onAddCustomColour : undefined
          }
          onChange={(next) => onChange(next[0] ?? "")}
          onBlur={onBlur}
        />
        {!question.required ? (
          <NoPreferenceControl
            name={question.id}
            active={current === ""}
            onSelect={() => onChange("")}
            onBlur={onBlur}
          />
        ) : null}
        {errorMessage}
      </fieldset>
    );
  }

  if (question.type === "single_choice") {
    const current = typeof value === "string" ? value : "";
    const optional = !question.required;
    return (
      <fieldset
        className="field"
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
      >
        <legend className="field-label">{question.label}</legend>
        {help}
        <ChoiceOptionGrid
          options={options}
          name={question.id}
          type="radio"
          selected={current ? [current] : []}
          onToggle={(optionValue, checked) => {
            if (checked) onChange(optionValue);
          }}
          onBlur={onBlur}
        />
        {optional ? (
          // No preference is absence, not a persisted option: clearing to ""
          // is dropped by the wizard's stale-answer clean-up, so the answer key
          // simply disappears. Never rendered for a required question.
          <NoPreferenceControl
            name={question.id}
            active={current === ""}
            onSelect={() => onChange("")}
            onBlur={onBlur}
          />
        ) : null}
        {errorMessage}
      </fieldset>
    );
  }

  // multi_choice
  const selected = Array.isArray(value) ? value : [];
  const constraints = question.constraints ?? {};
  const exclusive = new Set(constraints.exclusive_values ?? []);
  const max = constraints.max_items;
  const atMax = typeof max === "number" && selected.length >= max;

  // A colour multi_choice (its options map to project-owned colour swatches)
  // uses the compact grouped swatch selector; every other multi_choice uses the
  // accessible card grid. Detection is schema-driven, never a hard-coded id.
  const isColour = options.some((option) => colourSwatch(option.visual_key));
  if (isColour) {
    return (
      <fieldset
        className="field"
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
      >
        <legend className="field-label">{question.label}</legend>
        {help}
        <ColourSwatchGrid
          options={options}
          name={question.id}
          mode="multi"
          selected={selected}
          max={typeof max === "number" ? max : undefined}
          exclusiveValues={constraints.exclusive_values ?? []}
          customColours={palette}
          onChange={(next) => onChange(next)}
          onBlur={onBlur}
        />
        {errorMessage}
      </fieldset>
    );
  }

  const toggle = (optionValue: string, checked: boolean): void => {
    let next: string[];
    if (checked) {
      if (exclusive.has(optionValue)) {
        next = [optionValue];
      } else {
        next = [...selected.filter((entry) => !exclusive.has(entry)), optionValue];
      }
    } else {
      next = selected.filter((entry) => entry !== optionValue);
    }
    onChange(next);
  };

  // Prevent selecting past the maximum (server also rejects it), but never
  // disable an already-checked box.
  const disabledValues = new Set(
    atMax
      ? options
          .map((option) => option.value)
          .filter((v) => !selected.includes(v) && !exclusive.has(v))
      : [],
  );

  return (
    <fieldset
      className="field"
      aria-describedby={describedBy}
      aria-invalid={error ? true : undefined}
    >
      <legend className="field-label">{question.label}</legend>
      {help}
      {typeof max === "number" ? (
        // A live limit note, so a keyboard or screen-reader user learns they
        // have reached the maximum from the announcement rather than from
        // silently disabled cards.
        <p className="field-limit" role="status">
          {selected.length} of {max} chosen
        </p>
      ) : null}
      <ChoiceOptionGrid
        options={options}
        name={question.id}
        type="checkbox"
        selected={selected}
        disabledValues={disabledValues}
        onToggle={toggle}
        onBlur={onBlur}
      />
      {errorMessage}
    </fieldset>
  );
}
