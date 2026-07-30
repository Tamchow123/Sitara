import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InfoDrawer } from "./InfoDrawer";
import { axeViolations } from "@/test-utils/axe";

// jsdom's fireEvent.click does not move focus the way a real pointer press
// does, so each test focuses the trigger explicitly first. Without that the
// "focus returns to the trigger" assertions would be vacuous — the drawer would
// be capturing <body>, not the button.
function openDrawer() {
  const trigger = screen.getByRole("button", { name: "Explain V-neck" });
  trigger.focus();
  fireEvent.click(trigger);
  return trigger;
}

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Explain V-neck
      </button>
      {open ? (
        <InfoDrawer
          title="V-neck"
          body="A V-shaped neckline of moderate depth."
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}

describe("InfoDrawer", () => {
  it("is a modal dialog labelled by its title and described by its body", () => {
    render(<Harness />);
    openDrawer();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName("V-neck");
    expect(dialog).toHaveAccessibleDescription("A V-shaped neckline of moderate depth.");
  });

  it("moves focus into the drawer on open", () => {
    render(<Harness />);
    openDrawer();
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
  });

  it("closes on Escape and returns focus to the trigger", () => {
    render(<Harness />);
    const trigger = openDrawer();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("closes on the Close button and returns focus to the trigger", () => {
    render(<Harness />);
    const trigger = openDrawer();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("traps Tab and Shift+Tab inside the panel", () => {
    render(<Harness />);
    openDrawer();
    const dialog = screen.getByRole("dialog");
    const close = screen.getByRole("button", { name: "Close" });

    // Close is the panel's only focusable element, so both directions must
    // cycle straight back to it rather than escaping to the page behind.
    const forward = fireEvent.keyDown(dialog, { key: "Tab" });
    expect(forward).toBe(false); // default prevented
    expect(close).toHaveFocus();

    const backward = fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(backward).toBe(false);
    expect(close).toHaveFocus();
  });

  it("traps Tab from the panel itself, not just from its focusable children", () => {
    render(<Harness />);
    openDrawer();
    const dialog = screen.getByRole("dialog");
    const close = screen.getByRole("button", { name: "Close" });

    // Pressing the drawer's title, body or handle focuses the panel (it carries
    // tabIndex -1). Tab from THERE must still be trapped — otherwise focus
    // walks out to the page behind a dialog announced as modal.
    dialog.focus();
    expect(dialog).toHaveFocus();
    expect(fireEvent.keyDown(dialog, { key: "Tab" })).toBe(false);
    expect(close).toHaveFocus();

    dialog.focus();
    expect(fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true })).toBe(false);
    expect(close).toHaveFocus();
  });

  it("closes when the scrim is pressed but not when the panel is", () => {
    const { container } = render(<Harness />);
    openDrawer();
    const scrim = container.querySelector(".info-scrim");
    expect(scrim).not.toBeNull();

    fireEvent.mouseDown(screen.getByRole("dialog"));
    expect(screen.queryByRole("dialog")).not.toBeNull();

    fireEvent.mouseDown(scrim!);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("has no axe violations while open", async () => {
    const { container } = render(<Harness />);
    openDrawer();
    expect(await axeViolations(container)).toHaveNoViolations();
  });
});
