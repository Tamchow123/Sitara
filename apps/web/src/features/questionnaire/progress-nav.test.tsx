import { fireEvent, render, screen, within } from "@testing-library/react";
import { axe, toHaveNoViolations } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { ProgressNav } from "./ProgressNav";
import type { ProgressCategory } from "./ProgressNav";

expect.extend(toHaveNoViolations);

const AXE_CONFIG = { rules: { "color-contrast": { enabled: false } } };

const categories: ProgressCategory[] = [
  { id: "occasion", label: "Occasion", step: 0, complete: true, locked: false },
  { id: "heritage", label: "Heritage", step: 1, complete: false, locked: false },
  { id: "colour", label: "Colour & cloth", step: 2, complete: false, locked: true },
];

const renderNav = (props: Partial<Parameters<typeof ProgressNav>[0]> = {}) =>
  render(
    <ProgressNav
      categories={categories}
      activeIndex={1}
      onNavigate={vi.fn()}
      {...props}
    />,
  );

describe("ProgressNav", () => {
  it("renders one button per category, exactly once", () => {
    renderNav();
    const nav = screen.getByRole("navigation", { name: "Questionnaire progress" });
    // Both presentations share one list, so no step is duplicated in the
    // accessibility tree at any width. The "All steps" toggle is the only
    // other button in the nav.
    expect(within(nav).getAllByRole("button")).toHaveLength(categories.length + 1);
    for (const category of categories) {
      expect(within(nav).getByRole("button", { name: new RegExp(category.label) })).toBeVisible();
    }
  });

  it("marks the active category with aria-current and ticks completed ones", () => {
    renderNav();
    expect(screen.getByRole("button", { name: /Heritage/ })).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(screen.getByRole("button", { name: /Occasion/ })).not.toHaveAttribute("aria-current");
    expect(within(screen.getByRole("button", { name: /Occasion/ })).getByText("✓")).toBeVisible();
  });

  it("navigates to an unlocked category's step", () => {
    const onNavigate = vi.fn();
    renderNav({ onNavigate });
    fireEvent.click(screen.getByRole("button", { name: /Occasion/ }));
    expect(onNavigate).toHaveBeenCalledWith(0);
  });

  it("disables a locked category and never navigates to it", () => {
    const onNavigate = vi.fn();
    renderNav({ onNavigate });
    const locked = screen.getByRole("button", { name: /Colour & cloth/ });
    expect(locked).toBeDisabled();
    expect(locked).toHaveTextContent("not yet available");
    fireEvent.click(locked);
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("exposes position through a progressbar", () => {
    renderNav();
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuemin", "1");
    expect(bar).toHaveAttribute("aria-valuemax", "3");
    expect(bar).toHaveAttribute("aria-valuenow", "2");
    expect(bar).toHaveAttribute("aria-valuetext", "Step 2 of 3: Heritage");
  });

  it("clamps the announced position when the active index is out of range", () => {
    renderNav({ activeIndex: 9 });
    const bar = screen.getByRole("progressbar");
    // Never announce a value outside the range the same element declares.
    expect(bar).toHaveAttribute("aria-valuenow", "3");
    expect(bar).toHaveAttribute("aria-valuetext", "Step 3 of 3");
  });

  it("toggles the step list through the All steps disclosure", () => {
    const { container } = renderNav();
    const toggle = screen.getByRole("button", { name: "All steps" });
    const list = container.querySelector(".progress-steps");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", list?.id);
    // The narrow layout collapses the list purely through this attribute; the
    // wide layout ignores it in CSS, which is why nothing is unmounted here.
    expect(list).toHaveAttribute("data-open", "false");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(list).toHaveAttribute("data-open", "true");
  });

  it("has no axe violations", async () => {
    const { container } = renderNav();
    expect(await axe(container, AXE_CONFIG)).toHaveNoViolations();
  });
});
