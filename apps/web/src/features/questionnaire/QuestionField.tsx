"use client";

// One accessible question control, rendered entirely from the schema. Choice
// groups use a semantic <fieldset>/<legend> with real radio/checkbox inputs;
// text uses a labelled <textarea>. Help text and errors are associated with
// the control via aria-describedby. Only currently-allowed options are shown,
// so active restrictions (e.g. garment-specific silhouettes) are reflected
// immediately. No option label or limit is hard-coded here.

import { useId } from "react";

import type { AnswerValue, Question } from "./types";

type Props = {
  question: Question;
  value: AnswerValue | undefined;
  error?: string;
  allowed: Set<string>;
  onChange: (value: AnswerValue) => void;
  onBlur?: () => void;
};

export function QuestionField({ question, value, error, allowed, onChange, onBlur }: Props) {
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

  const options = (question.options ?? []).filter((option) => allowed.has(option.value));

  if (question.type === "single_choice") {
    const current = typeof value === "string" ? value : "";
    return (
      <fieldset className="field" aria-describedby={describedBy} aria-invalid={error ? true : undefined}>
        <legend className="field-label">{question.label}</legend>
        {help}
        <div className="option-list" role="radiogroup" aria-label={question.label}>
          {options.map((option) => (
            <label key={option.value} className="option">
              <input
                type="radio"
                name={question.id}
                value={option.value}
                checked={current === option.value}
                onChange={() => onChange(option.value)}
                onBlur={onBlur}
              />
              <span className="option-body">
                <span className="option-title">{option.label}</span>
                {option.description ? (
                  <span className="option-description">{option.description}</span>
                ) : null}
              </span>
            </label>
          ))}
        </div>
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

  const toggle = (optionValue: string, checked: boolean): void => {
    let next: string[];
    if (checked) {
      if (exclusive.has(optionValue)) {
        // An exclusive value clears everything else.
        next = [optionValue];
      } else {
        // Selecting a normal value removes any exclusive value, and preserves
        // selection order by appending.
        next = [...selected.filter((entry) => !exclusive.has(entry)), optionValue];
      }
    } else {
      next = selected.filter((entry) => entry !== optionValue);
    }
    onChange(next);
  };

  return (
    <fieldset className="field" aria-describedby={describedBy} aria-invalid={error ? true : undefined}>
      <legend className="field-label">{question.label}</legend>
      {help}
      <div className="option-list">
        {options.map((option) => {
          const checked = selected.includes(option.value);
          // Prevent selecting past the maximum (server also rejects it), but
          // never disable an already-checked box.
          const disabled = !checked && atMax && !exclusive.has(option.value);
          return (
            <label key={option.value} className={`option${disabled ? " option-disabled" : ""}`}>
              <input
                type="checkbox"
                name={question.id}
                value={option.value}
                checked={checked}
                disabled={disabled}
                onChange={(event) => toggle(option.value, event.target.checked)}
                onBlur={onBlur}
              />
              <span className="option-body">
                <span className="option-title">{option.label}</span>
                {option.description ? (
                  <span className="option-description">{option.description}</span>
                ) : null}
              </span>
            </label>
          );
        })}
      </div>
      {errorMessage}
    </fieldset>
  );
}
