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
  it("renders real radio inputs and an explanatory illustration when available", () => {
    const { container } = render(
      <QuestionField
        question={necklineQuestion}
        value={undefined}
        allowed={allOf(necklineQuestion)}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("radio", { name: /V-neck/ })).toBeInstanceOf(HTMLInputElement);
    // The v_neck option has a manifest illustration -> a decorative <img>.
    const image = container.querySelector('img[src*="v_neck.svg"]');
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

  it("expands a description through a real button with correct ARIA", () => {
    render(
      <QuestionField
        question={necklineQuestion}
        value={undefined}
        allowed={allOf(necklineQuestion)}
        onChange={vi.fn()}
      />,
    );
    const toggle = screen.getByRole("button", { name: /Details for V-neck/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/A V-shaped neckline of moderate depth/)).toBeVisible();
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
