import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { InspirationPicker } from "./InspirationPicker";
import type { PublicAsset } from "./types";

function asset(id: string, overrides: Partial<PublicAsset> = {}): PublicAsset {
  return {
    id,
    title: `Look ${id}`,
    alt_text: `Alt for ${id}`,
    garment_type: "lehenga",
    cultural_context: "Broad Pakistani bridal styling.",
    attribution: `Photo by Studio ${id}`,
    image_url: `/api/v1/inspiration-assets/${id}/image/`,
    thumbnail_url: `/api/v1/inspiration-assets/${id}/thumbnail/`,
    ...overrides,
  };
}

const four = [asset("a"), asset("b"), asset("c"), asset("d")];

describe("InspirationPicker", () => {
  it("renders keyboard-operable cards with attribution and accessible names", () => {
    render(
      <InspirationPicker assets={[asset("a")]} selection={[]} max={3} onChange={vi.fn()} />,
    );
    const card = screen.getByRole("button", { name: /Look a/i });
    expect(card).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Photo by Studio a")).toBeInTheDocument();
    expect(screen.getByAltText("Alt for a")).toBeInTheDocument();
  });

  it("blocks selecting a fourth inspiration", () => {
    const onChange = vi.fn();
    render(
      <InspirationPicker
        assets={four}
        selection={["a", "b", "c"]}
        max={3}
        onChange={onChange}
      />,
    );
    const fourth = screen.getByRole("button", { name: /Look d/i });
    expect(fourth).toBeDisabled();
    fireEvent.click(fourth);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("toggles selection and preserves order", () => {
    const onChange = vi.fn();
    render(
      <InspirationPicker
        assets={[asset("a"), asset("b")]}
        selection={["a"]}
        max={3}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Look b/i }));
    expect(onChange).toHaveBeenCalledWith(["a", "b"]);
  });

  it("shows a neutral placeholder for an unavailable previous selection and can remove it", () => {
    const onChange = vi.fn();
    render(
      <InspirationPicker
        assets={[asset("a")]}
        selection={["gone", "a"]}
        max={3}
        onChange={onChange}
      />,
    );
    expect(screen.getByText(/no longer available/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(onChange).toHaveBeenCalledWith(["a"]);
  });

  it("renders a valid empty state when the catalogue is empty", () => {
    render(<InspirationPicker assets={[]} selection={[]} max={3} onChange={vi.fn()} />);
    expect(screen.getByText(/No inspiration images are available yet/i)).toBeInTheDocument();
  });

  it("explains the metadata-only influence and associates it with the picker", () => {
    const { container } = render(
      <InspirationPicker assets={[asset("a")]} selection={[]} max={3} onChange={vi.fn()} />,
    );
    const help = document.getElementById("inspiration-help");
    expect(help).toHaveTextContent(/optional/i);
    expect(help).toHaveTextContent(/staff-written description/i);
    expect(help).toHaveTextContent(/questionnaire answers remain authoritative/i);
    expect(help).toHaveTextContent(/not sent to the ai models/i);
    expect(help).toHaveTextContent(/will not be an exact copy/i);
    const grid = container.querySelector("ul.inspiration-grid");
    expect(grid).toHaveAttribute("aria-describedby", "inspiration-help");
  });
});
