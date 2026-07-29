// User inspiration uploads (Phase 16B, ADR 0018/0019).
//
// The load-bearing behaviours here are consent and honesty, not mechanics: the
// ADR 0019 provider exposure must be readable BEFORE the picker is usable, the
// affirmation must gate the upload, and every outcome must be announced rather
// than only drawn.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { InspirationUpload as Upload } from "@/lib/api";

import { InspirationUpload } from "./InspirationUpload";

const uploadImage = vi.fn();
const removeUpload = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    uploadInspirationImage: (...args: unknown[]) => uploadImage(...args),
    removeInspirationUpload: (...args: unknown[]) => removeUpload(...args),
  };
});

function made(id: string, position = 1): Upload {
  return {
    id,
    position,
    width: 900,
    height: 1200,
    rights_acknowledged_at: "2026-07-29T00:00:00Z",
    created_at: "2026-07-29T00:00:00Z",
  };
}

function file(name = "dress.jpg"): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type: "image/jpeg" });
}

function renderUpload(overrides: Partial<React.ComponentProps<typeof InspirationUpload>> = {}) {
  const onChange = vi.fn();
  const utils = render(
    <InspirationUpload
      designId="design-1"
      uploads={[]}
      slotsRemaining={3}
      onChange={onChange}
      {...overrides}
    />,
  );
  return { ...utils, onChange };
}

function chooser(): HTMLInputElement {
  return screen.getByLabelText(/Choose an image/i) as HTMLInputElement;
}

function acknowledge(): void {
  fireEvent.click(screen.getByRole("checkbox"));
}

beforeEach(() => {
  uploadImage.mockReset();
  removeUpload.mockReset();
});

describe("InspirationUpload — consent", () => {
  it("discloses the provider exposure before anything can be uploaded", () => {
    renderUpload();
    const disclosure = screen.getByText(/Before you upload, please read this/i).parentElement;
    expect(disclosure).toHaveTextContent(/perpetual, irrevocable licence/i);
    expect(disclosure).toHaveTextContent(/train and improve/i);
    expect(disclosure).toHaveTextContent(/no time limit/i);
    expect(disclosure).toHaveTextContent(/Replicate/i);
    expect(disclosure).toHaveTextContent(/cannot undo/i);
  });

  it("names the affirmation as the user's own claim, never as verified rights", () => {
    renderUpload();
    const label = screen.getByText(/I have the right to use these images/i);
    expect(label).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/rights verified/i);
    expect(document.body).not.toHaveTextContent(/rights.cleared/i);
  });

  it("ties the checkbox to the disclosure for assistive technology", () => {
    renderUpload();
    const box = screen.getByRole("checkbox");
    const describedBy = box.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy!)).toHaveTextContent(
      /perpetual, irrevocable licence/i,
    );
  });

  it("keeps the file picker disabled until the affirmation is given", () => {
    renderUpload();
    expect(chooser()).toBeDisabled();
    acknowledge();
    expect(chooser()).toBeEnabled();
  });

  it("re-disables the picker if the affirmation is withdrawn", () => {
    renderUpload();
    acknowledge();
    expect(chooser()).toBeEnabled();
    acknowledge();
    expect(chooser()).toBeDisabled();
  });

  it("never uploads without the affirmation", () => {
    renderUpload();
    fireEvent.change(chooser(), { target: { files: [file()] } });
    expect(uploadImage).not.toHaveBeenCalled();
  });

  it("refuses the upload even if the picker is re-enabled underneath it", () => {
    // The `disabled` attribute is a DOM property, and this gate decides whether
    // someone's photograph is handed to an external provider under a perpetual
    // licence. Strip the attribute and fire the event anyway: the handler's own
    // check must still refuse. Without that check this test fails while the one
    // above still passes.
    renderUpload();
    const input = chooser();
    input.removeAttribute("disabled");
    expect(input).toBeEnabled();

    fireEvent.change(input, { target: { files: [file()] } });

    expect(uploadImage).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("");
  });
});

describe("InspirationUpload — states", () => {
  it("announces progress then success, and reports the new upload", async () => {
    uploadImage.mockResolvedValue({ ok: true, upload: made("u1") });
    const { onChange } = renderUpload();
    acknowledge();

    fireEvent.change(chooser(), { target: { files: [file("lehenga.jpg")] } });

    expect(await screen.findByText(/Uploading lehenga.jpg/i)).toBeInTheDocument();
    await waitFor(() => expect(onChange).toHaveBeenCalledWith([made("u1")]));
    expect(await screen.findByText(/Image added to your design/i)).toBeInTheDocument();
  });

  it("puts every announcement in a live region", async () => {
    uploadImage.mockResolvedValue({ ok: true, upload: made("u1") });
    renderUpload();
    acknowledge();
    fireEvent.change(chooser(), { target: { files: [file()] } });
    const status = await screen.findByRole("status");
    await waitFor(() => expect(status).toHaveTextContent(/Image added/i));
  });

  it("shows the backend's reason for a rejection", async () => {
    uploadImage.mockResolvedValue({
      ok: false,
      status: 400,
      code: "invalid_image",
      message: "That file could not be read as a JPEG, PNG or single-frame WebP image.",
    });
    const { onChange } = renderUpload();
    acknowledge();
    fireEvent.change(chooser(), { target: { files: [file("notes.txt")] } });
    expect(await screen.findByText(/could not be read as a JPEG/i)).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("turns a transport failure into a stated failure, never a silent one", async () => {
    uploadImage.mockRejectedValue(new Error("network"));
    const { onChange } = renderUpload();
    acknowledge();
    fireEvent.change(chooser(), { target: { files: [file()] } });
    expect(await screen.findByText(/did not finish/i)).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("lets the same file be retried after a failure", async () => {
    uploadImage.mockResolvedValueOnce({
      ok: false,
      status: 503,
      code: "storage_unavailable",
      message: "The image could not be stored. Please try again.",
    });
    uploadImage.mockResolvedValueOnce({ ok: true, upload: made("u1") });
    const { onChange } = renderUpload();
    acknowledge();
    const input = chooser();
    const same = file("same.jpg");

    fireEvent.change(input, { target: { files: [same] } });
    expect(await screen.findByText(/could not be stored/i)).toBeInTheDocument();
    // The input is cleared after each selection, so re-choosing the SAME file
    // still fires a change event — otherwise a retry would appear to do nothing.
    expect(input.value).toBe("");

    fireEvent.change(input, { target: { files: [same] } });
    await waitFor(() => expect(onChange).toHaveBeenCalledWith([made("u1")]));
  });
});

describe("InspirationUpload — the shared budget", () => {
  it("says how many slots are free", () => {
    renderUpload({ slotsRemaining: 2 });
    expect(screen.getByText(/2 of your inspiration slots are free/i)).toBeInTheDocument();
  });

  it("uses the singular for one remaining slot", () => {
    renderUpload({ slotsRemaining: 1 });
    expect(screen.getByText(/1 of your inspiration slots is free/i)).toBeInTheDocument();
  });

  it("closes the picker when the shared budget is spent, even with the affirmation given", () => {
    renderUpload({ slotsRemaining: 0 });
    acknowledge();
    expect(chooser()).toBeDisabled();
    expect(screen.getByText(/used all of your inspiration slots/i)).toBeInTheDocument();
  });
});

describe("InspirationUpload — previews and removal", () => {
  it("renders a preview per upload from the ownership-checked endpoint", () => {
    renderUpload({ uploads: [made("u1"), made("u2", 2)] });
    const images = screen.getAllByRole("img");
    expect(images).toHaveLength(2);
    expect(images[0]).toHaveAttribute(
      "src",
      "/api/v1/designs/design-1/inspiration-uploads/u1/image/",
    );
    // Never next/image: these bytes must not be proxied or cached.
    expect(images[0].getAttribute("src")).not.toMatch(/_next\/image/);
  });

  it("gives each preview a distinguishing accessible name", () => {
    renderUpload({ uploads: [made("u1"), made("u2", 2)] });
    expect(screen.getByAltText(/uploaded inspiration image 1/i)).toBeInTheDocument();
    expect(screen.getByAltText(/uploaded inspiration image 2/i)).toBeInTheDocument();
  });

  it("removes an upload and announces it", async () => {
    removeUpload.mockResolvedValue({ ok: true });
    const { onChange } = renderUpload({ uploads: [made("u1"), made("u2", 2)] });

    fireEvent.click(screen.getByRole("button", { name: /Remove image 1/i }));

    await waitFor(() => expect(onChange).toHaveBeenCalledWith([made("u2", 2)]));
    expect(await screen.findByText(/Image removed from your design/i)).toBeInTheDocument();
  });

  it("keeps the upload listed when removal fails", async () => {
    removeUpload.mockResolvedValue({
      ok: false,
      status: 503,
      code: "storage_unavailable",
      message: "The image could not be stored. Please try again.",
    });
    const { onChange } = renderUpload({ uploads: [made("u1")] });

    fireEvent.click(screen.getByRole("button", { name: /Remove image 1/i }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/could not be/i));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("disables only the button being removed", async () => {
    let settle: (value: { ok: true }) => void = () => {};
    removeUpload.mockReturnValue(
      new Promise<{ ok: true }>((resolve) => {
        settle = resolve;
      }),
    );
    renderUpload({ uploads: [made("u1"), made("u2", 2)] });

    fireEvent.click(screen.getByRole("button", { name: /Remove image 1/i }));

    expect(await screen.findByRole("button", { name: /Removing/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Remove image 2/i })).toBeEnabled();
    settle({ ok: true });
  });

  it("restores previously persisted uploads without re-uploading", () => {
    renderUpload({ uploads: [made("u1"), made("u2", 2)] });
    expect(screen.getAllByRole("img")).toHaveLength(2);
    expect(uploadImage).not.toHaveBeenCalled();
  });
});
