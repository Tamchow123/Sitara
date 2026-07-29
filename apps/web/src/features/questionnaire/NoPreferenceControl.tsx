"use client";

// An explicit, reversible "No preference" control for an OPTIONAL single_choice
// question. It is a real radio sharing the question's radiogroup name, so it is
// mutually exclusive with the real options: selecting it clears the persisted
// answer to absence (no preference), and selecting any real option deselects it
// automatically. It is understandable when the stored answer is empty (it shows
// as selected) and is never rendered for a required question.

export function NoPreferenceControl({
  name,
  active,
  onSelect,
  onBlur,
}: {
  name: string;
  active: boolean;
  onSelect: () => void;
  onBlur?: () => void;
}) {
  return (
    <label className={`no-preference${active ? " no-preference-active" : ""}`}>
      <input
        type="radio"
        name={name}
        checked={active}
        onChange={onSelect}
        onBlur={onBlur}
      />
      <span className="no-preference-body">No preference — let Sitara decide</span>
    </label>
  );
}
