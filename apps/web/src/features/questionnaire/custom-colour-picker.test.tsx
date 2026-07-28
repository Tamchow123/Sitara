import { fireEvent, render, screen } from "@testing-library/react";
import { axe, toHaveNoViolations } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { CustomColourPicker } from "./CustomColourPicker";

expect.extend(toHaveNoViolations);

const AXE_CONFIG = { rules: { "color-contrast": { enabled: false } } };

const open = () => {
  fireEvent.click(screen.getByRole("button", { name: /Any colour/ }));
  return screen.getByLabelText("Pick a colour");
};

describe("CustomColourPicker", () => {
  it("toggles the panel through a real disclosure", () => {
    render(<CustomColourPicker colours={[]} max={8} onAdd={vi.fn()} />);
    const pill = screen.getByRole("button", { name: /Any colour/ });
    expect(pill).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText("Pick a colour")).toBeNull();

    fireEvent.click(pill);
    expect(pill).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Pick a colour")).toBeInTheDocument();
  });

  it("shows the chosen value as text and adds it as six-digit lower-case hex", () => {
    const onAdd = vi.fn();
    render(<CustomColourPicker colours={[]} max={8} onAdd={onAdd} />);
    const input = open();
    // A browser may report the value in upper case; the answer never carries it.
    fireEvent.change(input, { target: { value: "#7F2B4A" } });
    expect(screen.getByText("#7f2b4a")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onAdd).toHaveBeenCalledWith("#7f2b4a");
    // Adding closes the panel.
    expect(screen.getByRole("button", { name: /Any colour/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("refuses a colour already in the palette", () => {
    const onAdd = vi.fn();
    render(<CustomColourPicker colours={["#7f2b4a"]} max={8} onAdd={onAdd} />);
    const input = open();
    fireEvent.change(input, { target: { value: "#7f2b4a" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(onAdd).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("already in your colours");
  });

  it("cancels without adding anything", () => {
    const onAdd = vi.fn();
    render(<CustomColourPicker colours={[]} max={8} onAdd={onAdd} />);
    const input = open();
    fireEvent.change(input, { target: { value: "#123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onAdd).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Pick a colour")).toBeNull();
  });

  it("closes and announces once the palette is full", () => {
    render(
      <CustomColourPicker
        colours={["#111111", "#222222"]}
        max={2}
        onAdd={vi.fn()}
      />,
    );
    // The bound comes from the schema's colour_list max_items, never a
    // hard-coded number here.
    expect(screen.getByRole("button", { name: /Any colour/ })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("maximum of 2 colours");
  });

  it("has no axe violations with the panel open", async () => {
    const { container } = render(<CustomColourPicker colours={[]} max={8} onAdd={vi.fn()} />);
    open();
    expect(await axe(container, AXE_CONFIG)).toHaveNoViolations();
  });
});

describe("CustomColourPicker without a declared bound", () => {
  it("stays usable when no maximum is supplied", () => {
    // An absent bound means unbounded here, never "already full": defaulting to
    // the palette's own length would disable adding at every size, including
    // zero, and read as a real limit rather than a bug.
    render(<CustomColourPicker colours={[]} onAdd={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Any colour/ })).not.toBeDisabled();
    expect(screen.queryByRole("status")).toBeNull();
  });
});
