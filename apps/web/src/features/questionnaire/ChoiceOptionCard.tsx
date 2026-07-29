"use client";

// One choice option rendered as the handoff's option card. The card is a
// <label> wrapping a REAL radio or checkbox input (never a div pretending to be
// one); the input is visually hidden, so the card's border, tinted ground and
// tick glyph carry the selected state visually while the native control keeps
// carrying it semantically. Selection is therefore never signalled by colour
// alone — a checked card gains a tick mark and a heavier border as well as the
// accent tint. An approved project-owned photograph is shown when the option's
// visual_key maps to one; otherwise the card falls back to text. Hidden or
// restricted options are never rendered by the parent, so their images are
// never fetched.
//
// The "i" trigger is a SIBLING of the label, not a descendant: a <button>
// inside a <label> also activates that label's control, so an info request
// would silently change the answer.

import { useState } from "react";

import { optionVisual } from "./visuals/manifest";
import type { QuestionOption } from "./types";

type Props = {
  option: QuestionOption;
  name: string;
  type: "radio" | "checkbox";
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
  onBlur?: () => void;
  // Opens the info drawer for this option. The grid owns the drawer so only one
  // is ever mounted; a card with no description never gets a trigger.
  onShowInfo?: (option: QuestionOption) => void;
};

export function ChoiceOptionCard({
  option,
  name,
  type,
  checked,
  disabled,
  onChange,
  onBlur,
  onShowInfo,
}: Props) {
  // If the photograph fails to load at runtime, degrade to the same text-only
  // presentation used when an option has no visual at all.
  const [imageFailed, setImageFailed] = useState(false);
  const visual = imageFailed ? null : optionVisual(option.visual_key);
  const className = [
    "choice-card",
    checked ? "choice-card-selected" : "",
    disabled ? "choice-card-disabled" : "",
  ]
    .filter(Boolean)
    .join(" ");
  // A card with no photograph inside a photograph-led grid must not be stretched
  // to the frame height its neighbours get — it would render as a tall empty
  // box with a label floating at the top. The modifier goes on the wrap (the
  // grid's direct child, so `align-self` applies to it) and is driven by the
  // resolved `visual`, not by `option.visual_key`, so the runtime image-failure
  // path above is covered too.
  const wrapClassName = visual ? "choice-card-wrap" : "choice-card-wrap choice-card-wrap-textonly";
  return (
    <div className={wrapClassName}>
      <label className={className}>
        <input
          className="visually-hidden"
          type={type}
          name={name}
          value={option.value}
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
          onBlur={onBlur}
        />
        {visual ? (
          // Decorative here: the adjacent title fully communicates the choice,
          // so the alt is empty to avoid double-announcing. Fixed intrinsic size
          // reserves space so lazy loading never shifts layout. Plain <img>,
          // never next/image, matching the questionnaire's other local-asset
          // usage.
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
          {checked ? (
            <span className="choice-card-tick" aria-hidden="true">
              ✓
            </span>
          ) : null}
        </span>
      </label>
      {option.description && onShowInfo ? (
        <button
          type="button"
          className="choice-card-info"
          // Focus the trigger explicitly before opening: Safari does not focus
          // a <button> on a bare mouse press, and the drawer returns focus to
          // whatever was focused when it mounted. Without this, closing the
          // drawer would drop focus to the document body for those users.
          onClick={(event) => {
            event.currentTarget.focus();
            onShowInfo(option);
          }}
          disabled={disabled}
        >
          <span aria-hidden="true">i</span>
          <span className="visually-hidden">More about {option.label}</span>
        </button>
      ) : null}
    </div>
  );
}
