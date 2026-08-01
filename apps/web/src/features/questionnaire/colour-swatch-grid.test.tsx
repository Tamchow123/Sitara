import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ColourSwatchGrid } from "./ColourSwatchGrid";
import type { QuestionOption } from "./types";
import { axeViolations } from "@/test-utils/axe";

// The colours screen is the one place a bride makes several ordered choices at
// once, so these cover what the handoff's flat palette has to keep true:
// selection order, the bounded limit, "Match the fabric" never pretending to be
// a colour, removal from the summary, and the fact that every swatch is a real
// radio or checkbox rather than a div wearing aria-pressed.
//
// The palette is ONE row with no category headings — that is the handoff's own
// design, and the tests assert the absence directly, because a stray fieldset
// reintroduced by a refactor would look reasonable in a diff.

function option(value: string, label: string, group: string): QuestionOption {
  return { value, label, group, visual_key: `colour_${value}` };
}

// Deliberately spans three manifest groups plus the non-colour "match" group,
// and is declared OUT of manifest order so the grouping test is not vacuous.
const OPTIONS: QuestionOption[] = [
  option("blush", "Blush", "pinks_roses"),
  option("scarlet", "Scarlet", "reds_maroons"),
  option("match_fabric", "Match the fabric", "match"),
  option("deep_maroon", "Deep maroon", "reds_maroons"),
];

function Harness({
  mode = "multi",
  max,
  initial = [],
  customColours = [],
  onAddCustomColour,
  exclusiveValues,
}: {
  mode?: "single" | "multi";
  max?: number;
  initial?: string[];
  customColours?: string[];
  onAddCustomColour?: (hex: string) => void;
  exclusiveValues?: string[];
}) {
  const [selected, setSelected] = useState<string[]>(initial);
  return (
    <ColourSwatchGrid
      options={OPTIONS}
      name="colours"
      mode={mode}
      selected={selected}
      max={max}
      exclusiveValues={exclusiveValues}
      customColours={customColours}
      onAddCustomColour={onAddCustomColour}
      onChange={setSelected}
    />
  );
}

function summary() {
  return screen.getByRole("list", { name: /selected colours, in order/i });
}

describe("ColourSwatchGrid — semantics", () => {
  it("renders a real checkbox per swatch in multi mode, each named by its colour", () => {
    render(<Harness />);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(OPTIONS.length);
    // The name comes from label text, not from the chip's background: the text
    // is hidden from sight on a circular swatch but never from the accessibility
    // tree, so the choice still never rests on colour alone.
    for (const label of ["Blush", "Scarlet", "Match the fabric", "Deep maroon"]) {
      expect(screen.getByRole("checkbox", { name: label })).toBeInTheDocument();
    }
  });

  it("offers the colour's name as a tooltip, since the swatch itself shows no text", () => {
    render(<Harness />);
    const scarlet = screen.getByRole("checkbox", { name: "Scarlet" }).closest("label")!;
    expect(scarlet).toHaveAttribute("title", "Scarlet");
  });

  it("renders real radios in single mode, so only one colour can be held", () => {
    render(<Harness mode="single" />);
    expect(screen.getAllByRole("radio")).toHaveLength(OPTIONS.length);
    fireEvent.click(screen.getByRole("radio", { name: "Scarlet" }));
    expect(screen.getByRole("radio", { name: "Scarlet" })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: "Blush" }));
    expect(screen.getByRole("radio", { name: "Blush" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Scarlet" })).not.toBeChecked();
  });

  it("shows one flat palette with no category headings", () => {
    const { container } = render(<Harness />);
    // The handoff has no colour categories at all. Any fieldset or heading here
    // would be a category by another name.
    expect(container.querySelectorAll("fieldset")).toHaveLength(0);
    expect(container.querySelectorAll("legend")).toHaveLength(0);
    expect(container.querySelectorAll(".swatch-grid")).toHaveLength(1);
  });

  it("orders the flat palette by the manifest's groups, not by declaration order", () => {
    render(<Harness />);
    // "match" precedes "reds_maroons" precedes "pinks_roses" in the manifest,
    // so the palette reads as a spectrum even though OPTIONS is declared with
    // Blush first. Grouping survives as ORDER only — never as a heading.
    const names = screen.getAllByRole("checkbox").map((box) => box.getAttribute("value"));
    expect(names).toEqual(["match_fabric", "scarlet", "deep_maroon", "blush"]);
  });
});

describe("ColourSwatchGrid — selection, order and limits", () => {
  it("keeps selection order and numbers each choice", () => {
    render(<Harness max={3} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Deep maroon" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Blush" }));
    const items = within(summary()).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("1. Deep maroon");
    expect(items[1]).toHaveTextContent("2. Blush");
  });

  it("does not reorder the remaining colours when one is deselected", () => {
    render(<Harness max={3} />);
    for (const name of ["Scarlet", "Deep maroon", "Blush"]) {
      fireEvent.click(screen.getByRole("checkbox", { name }));
    }
    fireEvent.click(screen.getByRole("checkbox", { name: "Deep maroon" }));
    const items = within(summary()).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      expect.stringContaining("1. Scarlet"),
      expect.stringContaining("2. Blush"),
    ]);
  });

  it("announces the running count and blocks selection beyond the limit", () => {
    render(<Harness max={2} />);
    expect(screen.getByRole("status")).toHaveTextContent("0 of 2 chosen");
    fireEvent.click(screen.getByRole("checkbox", { name: "Scarlet" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Blush" }));
    expect(screen.getByRole("status")).toHaveTextContent("2 of 2 chosen");
    // At the limit, an unchosen swatch is disabled — but an already-chosen one
    // must stay operable, or the selection could never be undone.
    expect(screen.getByRole("checkbox", { name: "Deep maroon" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Scarlet" })).toBeEnabled();
  });

  it("lets an exclusive value clear the rest, and stays selectable at the limit", () => {
    render(<Harness max={2} exclusiveValues={["match_fabric"]} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Scarlet" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Blush" }));
    // At max, yet an exclusive option is never disabled.
    const exclusive = screen.getByRole("checkbox", { name: "Match the fabric" });
    expect(exclusive).toBeEnabled();
    fireEvent.click(exclusive);
    const items = within(summary()).getAllByRole("listitem");
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent("Match the fabric");
  });

  it("removes a colour from the summary without touching the others", () => {
    render(<Harness max={3} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Scarlet" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Blush" }));
    // The remove control names the colour it removes, so a screen-reader user
    // hears which of several "Remove" buttons this is.
    fireEvent.click(screen.getByRole("button", { name: /remove scarlet/i }));
    expect(screen.getByRole("checkbox", { name: "Scarlet" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Blush" })).toBeChecked();
    expect(within(summary()).getAllByRole("listitem")).toHaveLength(1);
  });
});

describe("ColourSwatchGrid — Match the fabric and custom colours", () => {
  it("renders Match the fabric as a labelled pill, never as a coloured circle", () => {
    render(<Harness />);
    const label = screen.getByRole("checkbox", { name: "Match the fabric" }).closest("label")!;
    // No chip at all: there is no hue to show, so a circle would be a swatch
    // pretending to be a colour. The pill carries visible text instead.
    expect(label.querySelector(".swatch-chip")).toBeNull();
    expect(label).toHaveClass("swatch-pill");
    expect(label).toHaveTextContent("Match the fabric");

    const scarlet = screen.getByRole("checkbox", { name: "Scarlet" }).closest("label")!;
    expect(scarlet.querySelector(".swatch-chip")).not.toBeNull();
    expect(scarlet).not.toHaveClass("swatch-pill");
  });

  it("offers the design's own colours in single mode, where the backend accepts a hex", () => {
    render(<Harness mode="single" customColours={["#123456"]} />);
    // In the same flat palette as everything else — a custom colour is not a
    // category either.
    expect(screen.getByRole("radio", { name: "#123456" })).toBeInTheDocument();
  });

  it("does NOT offer custom colours in multi mode, where the backend would reject them", () => {
    // A multi_choice answer is validated against its declared options, so a
    // custom hex there is a swatch whose selection always fails.
    render(<Harness mode="multi" customColours={["#123456"]} />);
    expect(screen.queryByRole("checkbox", { name: "#123456" })).toBeNull();
  });

  it("mounts the bounded picker only when the question allows a custom colour", () => {
    const { unmount } = render(<Harness mode="single" />);
    expect(screen.queryByRole("button", { name: /any colour/i })).toBeNull();
    unmount();
    render(<Harness mode="single" onAddCustomColour={vi.fn()} />);
    expect(screen.getByRole("button", { name: /any colour/i })).toBeInTheDocument();
  });
});

describe("ColourSwatchGrid — accessibility", () => {
  it("has no axe violations empty, part-selected, and at the limit", async () => {
    const { container } = render(<Harness max={2} />);
    expect(await axeViolations(container)).toHaveNoViolations();
    fireEvent.click(screen.getByRole("checkbox", { name: "Scarlet" }));
    expect(await axeViolations(container)).toHaveNoViolations();
    fireEvent.click(screen.getByRole("checkbox", { name: "Blush" }));
    expect(await axeViolations(container)).toHaveNoViolations();
  });

  it("has no axe violations in single mode with the custom picker present", async () => {
    const { container } = render(
      <Harness mode="single" customColours={["#123456"]} onAddCustomColour={vi.fn()} />,
    );
    expect(await axeViolations(container)).toHaveNoViolations();
  });
});
