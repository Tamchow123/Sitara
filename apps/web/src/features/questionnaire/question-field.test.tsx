import { fireEvent, render, screen, within } from "@testing-library/react";
import { axe, toHaveNoViolations } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { QuestionField } from "./QuestionField";
import type { Question } from "./types";

expect.extend(toHaveNoViolations);

const AXE_CONFIG = { rules: { "color-contrast": { enabled: false } } };

const necklineQuestion: Question = {
  id: "neckline_style",
  type: "single_choice",
  label: "Which neckline?",
  required: false,
  options: [
    {
      value: "v_neck",
      label: "V-neck",
      description: "A V-shaped neckline of moderate depth.",
      visual_key: "neckline_v_neck",
      group: "necklines",
    },
    { value: "high_neck", label: "High neck", visual_key: "neckline_high_neck", group: "necklines" },
    { value: "mystery", label: "Mystery", visual_key: "neckline_not_in_manifest", group: "necklines" },
  ],
};

const colourQuestion: Question = {
  id: "colour_palette",
  type: "multi_choice",
  label: "Colours",
  required: true,
  options: [
    { value: "ruby", label: "Ruby", visual_key: "colour_ruby", group: "reds" },
    { value: "gold", label: "Gold", visual_key: "colour_gold", group: "yellows_metallics" },
    { value: "emerald", label: "Emerald", visual_key: "colour_emerald", group: "greens" },
  ],
  constraints: { min_items: 1, max_items: 2 },
};

const allOf = (q: Question) => new Set((q.options ?? []).map((o) => o.value));

describe("QuestionField single_choice with visuals and no-preference", () => {
  it("renders real radio inputs and an explanatory photograph when available", () => {
    const { container } = render(
      <QuestionField
        question={necklineQuestion}
        value={undefined}
        allowed={allOf(necklineQuestion)}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("radio", { name: /V-neck/ })).toBeInstanceOf(HTMLInputElement);
    // The v_neck option has a manifest photograph -> a decorative <img>.
    const image = container.querySelector(
      'img[src="/questionnaire-visuals/necklines/neckline_v_neck.webp"]',
    );
    expect(image).not.toBeNull();
    expect(image).toHaveAttribute("alt", "");
    expect(image).toHaveAttribute("loading", "lazy");
  });

  it("falls back to text (no image) for an unknown visual key", () => {
    render(
      <QuestionField
        question={necklineQuestion}
        value={undefined}
        allowed={allOf(necklineQuestion)}
        onChange={vi.fn()}
      />,
    );
    const mystery = screen.getByRole("radio", { name: /Mystery/ }).closest("label");
    expect(mystery?.querySelector("img")).toBeNull();
  });

  it("offers a reversible No preference control for an optional question", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <QuestionField
        question={necklineQuestion}
        value={undefined}
        allowed={allOf(necklineQuestion)}
        onChange={onChange}
      />,
    );
    const noPref = screen.getByRole("radio", { name: /No preference/ });
    expect(noPref).toBeChecked();

    fireEvent.click(screen.getByRole("radio", { name: /V-neck/ }));
    expect(onChange).toHaveBeenCalledWith("v_neck");

    rerender(
      <QuestionField
        question={necklineQuestion}
        value="v_neck"
        allowed={allOf(necklineQuestion)}
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("radio", { name: /No preference/ })).not.toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: /No preference/ }));
    // No preference clears to absence ("") which the wizard drops.
    expect(onChange).toHaveBeenLastCalledWith("");
  });

  it("never shows No preference for a required question", () => {
    render(
      <QuestionField
        question={{ ...necklineQuestion, required: true }}
        value={undefined}
        allowed={allOf(necklineQuestion)}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByRole("radio", { name: /No preference/ })).toBeNull();
  });

  it("explains an option in the info drawer without changing the answer", () => {
    const onChange = vi.fn();
    render(
      <QuestionField
        question={necklineQuestion}
        value={undefined}
        allowed={allOf(necklineQuestion)}
        onChange={onChange}
      />,
    );
    // The trigger is a sibling of the card's <label>, never a descendant: a
    // button inside a label also activates that label's control, which would
    // silently answer the question on an info request.
    const trigger = screen.getByRole("button", { name: /More about V-neck/ });
    expect(trigger.closest("label")).toBeNull();

    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleName("V-neck");
    expect(within(dialog).getByText(/A V-shaped neckline of moderate depth/)).toBeVisible();
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("radio", { name: /V-neck/ })).not.toBeChecked();

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("returns focus to the info trigger even when the click never focused it", () => {
    render(
      <QuestionField
        question={necklineQuestion}
        value={undefined}
        allowed={allOf(necklineQuestion)}
        onChange={vi.fn()}
      />,
    );
    // Deliberately NOT focused first: Safari does not focus a <button> on a
    // bare mouse press, so the drawer must not depend on the browser having
    // done it. The trigger focuses itself before opening.
    const trigger = screen.getByRole("button", { name: /More about V-neck/ });
    expect(document.activeElement).not.toBe(trigger);
    fireEvent.click(trigger);

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("offers no info trigger for an option with no description", () => {
    render(
      <QuestionField
        question={necklineQuestion}
        value={undefined}
        allowed={allOf(necklineQuestion)}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /More about High neck/ })).toBeNull();
  });

  it("frames every card in the question with one aspect from the manifest", () => {
    const { container } = render(
      <QuestionField
        question={necklineQuestion}
        value={undefined}
        allowed={allOf(necklineQuestion)}
        onChange={vi.fn()}
      />,
    );
    // The neckline visuals are square, so the whole grid frames 720/720 — a
    // per-question value, never a per-card one.
    const grid = container.querySelector(".choice-grid-visual");
    expect(grid).toHaveStyle({ "--choice-card-aspect": "720 / 720" });
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <QuestionField
        question={necklineQuestion}
        value="v_neck"
        allowed={allOf(necklineQuestion)}
        onChange={vi.fn()}
      />,
    );
    expect(await axe(container, AXE_CONFIG)).toHaveNoViolations();
  });
});

describe("QuestionField bounded multi_choice", () => {
  const fabricQuestion: Question = {
    id: "fabrics",
    type: "multi_choice",
    label: "What should it be made of?",
    required: false,
    options: [
      { value: "silk", label: "Silk", visual_key: "fabric_silk", group: "fabrics" },
      { value: "velvet", label: "Velvet", visual_key: "fabric_velvet", group: "fabrics" },
      { value: "organza", label: "Organza", visual_key: "fabric_organza", group: "fabrics" },
    ],
    constraints: { max_items: 2 },
  };

  it("announces the limit and the running count through a live region", () => {
    const { rerender } = render(
      <QuestionField
        question={fabricQuestion}
        value={[]}
        allowed={allOf(fabricQuestion)}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("0 of 2 chosen");

    rerender(
      <QuestionField
        question={fabricQuestion}
        value={["silk", "velvet"]}
        allowed={allOf(fabricQuestion)}
        onChange={vi.fn()}
      />,
    );
    // At the limit the note is the announcement; the untaken option is also
    // disabled, and an already-taken one never is.
    expect(screen.getByRole("status")).toHaveTextContent("2 of 2 chosen");
    expect(screen.getByRole("checkbox", { name: /Organza/ })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /Silk/ })).not.toBeDisabled();
  });

  it("shows no limit note when the question is unbounded", () => {
    render(
      <QuestionField
        question={{ ...fabricQuestion, constraints: undefined }}
        value={[]}
        allowed={allOf(fabricQuestion)}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("QuestionField colour swatch selector", () => {
  it("renders grouped swatches as real checkboxes with visible labels", () => {
    render(
      <QuestionField
        question={colourQuestion}
        value={[]}
        allowed={allOf(colourQuestion)}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("checkbox", { name: /Ruby/ })).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByText("Reds & warm")).toBeInTheDocument();
    expect(screen.getByText(/0 of 2 selected/)).toBeInTheDocument();
  });

  it("preserves selection order and enforces the maximum", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <QuestionField
        question={colourQuestion}
        value={["ruby"]}
        allowed={allOf(colourQuestion)}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: /Gold/ }));
    expect(onChange).toHaveBeenCalledWith(["ruby", "gold"]);

    rerender(
      <QuestionField
        question={colourQuestion}
        value={["ruby", "gold"]}
        allowed={allOf(colourQuestion)}
        onChange={onChange}
      />,
    );
    // At the maximum, an unselected swatch is disabled; selected ones are not.
    expect(screen.getByRole("checkbox", { name: /Emerald/ })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /Ruby/ })).not.toBeDisabled();
    // Ordered summary is pinned above the grid.
    const summary = screen.getByRole("list", { name: /Selected colours/ });
    expect(within(summary).getByText(/1\. Ruby/)).toBeInTheDocument();
    expect(within(summary).getByText(/2\. Gold/)).toBeInTheDocument();
  });

  it("deselects without reordering the remaining colours", () => {
    const onChange = vi.fn();
    render(
      <QuestionField
        question={colourQuestion}
        value={["ruby", "gold"]}
        allowed={allOf(colourQuestion)}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: /Ruby/ }));
    expect(onChange).toHaveBeenCalledWith(["gold"]);
  });

  it("applies exclusive_values with the same semantics as other multi_choice", () => {
    const exclusiveColour: Question = {
      ...colourQuestion,
      constraints: { min_items: 1, max_items: 4, exclusive_values: ["multicolour"] },
      options: [
        ...(colourQuestion.options ?? []),
        { value: "multicolour", label: "Multicolour", visual_key: "colour_multicolour", group: "neutrals" },
      ],
    };
    const onChange = vi.fn();
    const { rerender } = render(
      <QuestionField
        question={exclusiveColour}
        value={["ruby", "gold"]}
        allowed={allOf(exclusiveColour)}
        onChange={onChange}
      />,
    );
    // Selecting the exclusive colour clears everything else.
    fireEvent.click(screen.getByRole("checkbox", { name: /Multicolour/ }));
    expect(onChange).toHaveBeenCalledWith(["multicolour"]);

    rerender(
      <QuestionField
        question={exclusiveColour}
        value={["multicolour"]}
        allowed={allOf(exclusiveColour)}
        onChange={onChange}
      />,
    );
    // Selecting a normal colour removes the exclusive one.
    fireEvent.click(screen.getByRole("checkbox", { name: /Ruby/ }));
    expect(onChange).toHaveBeenLastCalledWith(["ruby"]);
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <QuestionField
        question={colourQuestion}
        value={["ruby"]}
        allowed={allOf(colourQuestion)}
        onChange={vi.fn()}
      />,
    );
    expect(await axe(container, AXE_CONFIG)).toHaveNoViolations();
  });
});
