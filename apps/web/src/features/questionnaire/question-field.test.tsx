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

// A bounded multi-colour question. Schema v4 has no such question of its own —
// its colours are single-choice — but the grid still serves both modes, so the
// multi semantics stay covered.
const colourQuestion: Question = {
  id: "colour_palette",
  type: "multi_choice",
  label: "Colours",
  required: true,
  options: [
    { value: "scarlet", label: "Scarlet", visual_key: "colour_scarlet", group: "reds_maroons" },
    { value: "marigold", label: "Marigold", visual_key: "colour_marigold", group: "golds_ivories" },
    { value: "emerald", label: "Emerald", visual_key: "colour_emerald", group: "greens" },
  ],
  constraints: { min_items: 1, max_items: 2 },
};

// The v4 dupatta colour: single choice, "Match the fabric" first, custom
// colours allowed.
const dupattaColourQuestion: Question = {
  id: "dupatta_colour",
  type: "colour_choice",
  label: "The dupatta",
  required: false,
  options: [
    {
      value: "match_fabric",
      label: "Match the fabric",
      visual_key: "colour_match_fabric",
      group: "match",
    },
    { value: "scarlet", label: "Scarlet", visual_key: "colour_scarlet", group: "reds_maroons" },
    { value: "pearl", label: "Pearl", visual_key: "colour_pearl", group: "silvers_pastels" },
  ],
  constraints: { allow_custom: true },
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
    expect(screen.getByRole("checkbox", { name: /Scarlet/ })).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByText("Reds & maroons")).toBeInTheDocument();
    expect(screen.getByText(/0 of 2 selected/)).toBeInTheDocument();
  });

  it("preserves selection order and enforces the maximum", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <QuestionField
        question={colourQuestion}
        value={["scarlet"]}
        allowed={allOf(colourQuestion)}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: /Marigold/ }));
    expect(onChange).toHaveBeenCalledWith(["scarlet", "marigold"]);

    rerender(
      <QuestionField
        question={colourQuestion}
        value={["scarlet", "marigold"]}
        allowed={allOf(colourQuestion)}
        onChange={onChange}
      />,
    );
    // At the maximum, an unselected swatch is disabled; selected ones are not.
    expect(screen.getByRole("checkbox", { name: /Emerald/ })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /Scarlet/ })).not.toBeDisabled();
    // Ordered summary is pinned above the grid.
    const summary = screen.getByRole("list", { name: /Selected colours/ });
    expect(within(summary).getByText(/1\. Scarlet/)).toBeInTheDocument();
    expect(within(summary).getByText(/2\. Marigold/)).toBeInTheDocument();
  });

  it("deselects without reordering the remaining colours", () => {
    const onChange = vi.fn();
    render(
      <QuestionField
        question={colourQuestion}
        value={["scarlet", "marigold"]}
        allowed={allOf(colourQuestion)}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: /Scarlet/ }));
    expect(onChange).toHaveBeenCalledWith(["marigold"]);
  });

  it("applies exclusive_values with the same semantics as other multi_choice", () => {
    const exclusiveColour: Question = {
      ...colourQuestion,
      constraints: { min_items: 1, max_items: 4, exclusive_values: ["pearl"] },
      options: [
        ...(colourQuestion.options ?? []),
        { value: "pearl", label: "Pearl", visual_key: "colour_pearl", group: "silvers_pastels" },
      ],
    };
    const onChange = vi.fn();
    const { rerender } = render(
      <QuestionField
        question={exclusiveColour}
        value={["scarlet", "marigold"]}
        allowed={allOf(exclusiveColour)}
        onChange={onChange}
      />,
    );
    // Selecting the exclusive colour clears everything else.
    fireEvent.click(screen.getByRole("checkbox", { name: /Pearl/ }));
    expect(onChange).toHaveBeenCalledWith(["pearl"]);

    rerender(
      <QuestionField
        question={exclusiveColour}
        value={["pearl"]}
        allowed={allOf(exclusiveColour)}
        onChange={onChange}
      />,
    );
    // Selecting a normal colour removes the exclusive one.
    fireEvent.click(screen.getByRole("checkbox", { name: /Scarlet/ }));
    expect(onChange).toHaveBeenLastCalledWith(["scarlet"]);
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <QuestionField
        question={colourQuestion}
        value={["scarlet"]}
        allowed={allOf(colourQuestion)}
        onChange={vi.fn()}
      />,
    );
    expect(await axe(container, AXE_CONFIG)).toHaveNoViolations();
  });
});

describe("QuestionField colour_choice (schema v4)", () => {
  const renderDupatta = (props: Record<string, unknown> = {}) =>
    render(
      <QuestionField
        question={dupattaColourQuestion}
        value={undefined}
        allowed={allOf(dupattaColourQuestion)}
        onChange={vi.fn()}
        customColours={[]}
        customColourMax={8}
        {...props}
      />,
    );

  it("renders one radio group of grouped swatches, matching first", () => {
    renderDupatta();
    expect(screen.getByRole("radio", { name: /Match the fabric/ })).toBeInstanceOf(
      HTMLInputElement,
    );
    expect(screen.getByText("Matching")).toBeInTheDocument();
    expect(screen.getByText("Reds & maroons")).toBeInTheDocument();
    // Single choice: no running count, because there is no limit to run to.
    expect(screen.queryByText(/selected$/)).toBeNull();
  });

  it("stores a chosen swatch as its option value", () => {
    const onChange = vi.fn();
    renderDupatta({ onChange });
    fireEvent.click(screen.getByRole("radio", { name: /Scarlet/ }));
    expect(onChange).toHaveBeenCalledWith("scarlet");
  });

  it("clears to absence through No preference", () => {
    const onChange = vi.fn();
    renderDupatta({ value: "scarlet", onChange });
    fireEvent.click(screen.getByRole("radio", { name: /No preference/ }));
    expect(onChange).toHaveBeenLastCalledWith("");
  });

  it("offers the design's own colours as swatches and stores the hex itself", () => {
    const onChange = vi.fn();
    renderDupatta({ customColours: ["#7f2b4a"], onChange });
    expect(screen.getByText("Your colours")).toBeInTheDocument();
    const custom = screen.getByRole("radio", { name: "#7f2b4a" });
    fireEvent.click(custom);
    // The backend accepts a colour_choice hex only when it is in the sibling
    // colour_list answer, so the stored value is the hex verbatim.
    expect(onChange).toHaveBeenCalledWith("#7f2b4a");
  });

  it("offers the colour picker only when the question allows custom colours", () => {
    renderDupatta({ onAddCustomColour: vi.fn() });
    expect(screen.getByRole("button", { name: /Any colour/ })).toBeInTheDocument();

    render(
      <QuestionField
        question={{ ...dupattaColourQuestion, id: "fixed", constraints: {} }}
        value={undefined}
        allowed={allOf(dupattaColourQuestion)}
        onChange={vi.fn()}
        customColours={[]}
        onAddCustomColour={vi.fn()}
      />,
    );
    // Two fields are mounted; the second one must not have added a picker.
    expect(screen.getAllByRole("button", { name: /Any colour/ })).toHaveLength(1);
  });

  it("renders nothing for the palette question itself", () => {
    const { container } = render(
      <QuestionField
        question={{
          id: "custom_colours",
          type: "colour_list",
          label: "Your own colours",
          required: false,
          constraints: { max_items: 8 },
        }}
        value={["#7f2b4a"]}
        allowed={new Set()}
        onChange={vi.fn()}
      />,
    );
    // The palette has no field of its own — it is edited through each colour
    // question's picker.
    expect(container).toBeEmptyDOMElement();
  });

  it("has no axe violations", async () => {
    const { container } = renderDupatta({
      value: "scarlet",
      customColours: ["#7f2b4a"],
      onAddCustomColour: vi.fn(),
    });
    expect(await axe(container, AXE_CONFIG)).toHaveNoViolations();
  });
});

describe("QuestionField colour custom-palette boundaries", () => {
  it("offers no picker when the question forbids custom colours, even if a handler is passed", () => {
    render(
      <QuestionField
        question={{ ...dupattaColourQuestion, constraints: { allow_custom: false } }}
        value={undefined}
        allowed={allOf(dupattaColourQuestion)}
        onChange={vi.fn()}
        customColours={[]}
        customColourMax={8}
        onAddCustomColour={vi.fn()}
      />,
    );
    // The schema's allow_custom is the gate — not whether a caller happened to
    // supply a handler.
    expect(screen.queryByRole("button", { name: /Any colour/ })).toBeNull();
  });

  it("never offers the design's own colours on a multi-select colour question", () => {
    render(
      <QuestionField
        question={colourQuestion}
        value={[]}
        allowed={allOf(colourQuestion)}
        onChange={vi.fn()}
        customColours={["#7f2b4a"]}
      />,
    );
    // A multi_choice answer is checked against its DECLARED options, so a raw
    // hex there is always rejected by the backend; the swatch must not exist.
    expect(screen.queryByText("Your colours")).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "#7f2b4a" })).toBeNull();
  });
});
