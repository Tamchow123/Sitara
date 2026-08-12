// The reference step (Phase 16B ADR 0018/0019; the whole step since ADR 0025).
//
// The load-bearing behaviours here are consent and honesty, not mechanics: the
// ADR 0019 provider exposure must be readable BEFORE any way of adding a
// photograph is usable, the affirmation must gate the upload, and every outcome
// must be announced rather than only drawn.

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
      max={3}
      onChange={onChange}
      {...overrides}
    />,
  );
  return { ...utils, onChange };
}

function chooser(): HTMLInputElement {
  return screen.getByLabelText(/Choose a file/i) as HTMLInputElement;
}

function camera(): HTMLInputElement {
  return screen.getByLabelText(/Take a photo/i) as HTMLInputElement;
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
    const disclosure = screen.getByText(/Before you add a photograph, please read this/i)
      .parentElement;
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

  it("says the affirmation covers every image, not just the first", () => {
    // It stays ticked between uploads, so a second photo is covered by a box
    // the user ticked while thinking about the first one.
    renderUpload();
    expect(screen.getByText(/applies to every image you add/i)).toBeInTheDocument();
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

    // Progress is announced without echoing the chosen filename. It never
    // reached the server (the storage key is server-generated and the model
    // carries no filename), and the two ways in that arrive later — a camera
    // capture and a photograph handed over from a phone — have no name worth
    // reading back on a screen a shop and a customer are both looking at.
    expect(await screen.findByText(/Adding your photograph/i)).toBeInTheDocument();
    expect(screen.queryByText(/lehenga\.jpg/i)).not.toBeInTheDocument();
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

describe("InspirationUpload — the camera", () => {
  it("asks the device for its rear camera", () => {
    renderUpload();
    expect(camera()).toHaveAttribute("capture", "environment");
  });

  it("accepts the same narrow formats as the file picker, never image/*", () => {
    // On iOS the accept list is what makes the camera hand back a JPEG rather
    // than the HEIC the device stores natively — and HEIC is a format the
    // server's sanitiser refuses. `image/*` here would turn "take a photo"
    // into "take a photo and be told it is not an image".
    renderUpload();
    expect(camera()).toHaveAttribute("accept", "image/jpeg,image/png,image/webp");
    expect(camera().getAttribute("accept")).not.toContain("*");
    expect(camera().getAttribute("accept")).toBe(chooser().getAttribute("accept"));
  });

  it("uploads a captured photograph through exactly the same path as a chosen file", async () => {
    // `capture` is a hint. A browser that ignores it opens an ordinary picker,
    // so the control has to behave correctly for a file that did not come from
    // a camera at all — which it does, because both inputs run one handler.
    uploadImage.mockResolvedValue({ ok: true, upload: made("u1") });
    const { onChange } = renderUpload();
    acknowledge();

    fireEvent.change(camera(), { target: { files: [file("IMG_0001.jpg")] } });

    await waitFor(() => expect(onChange).toHaveBeenCalledWith([made("u1")]));
    expect(uploadImage).toHaveBeenCalledWith("design-1", expect.any(File), true);
  });

  it("is gated by the affirmation exactly like the file picker", () => {
    renderUpload();
    expect(camera()).toBeDisabled();
    acknowledge();
    expect(camera()).toBeEnabled();
  });

  it("surfaces a server refusal of an oversized photograph verbatim", async () => {
    // The server's message names what to change; the client must not flatten
    // it into a generic failure, because a customer holding a camera can act
    // on the first and not the second.
    uploadImage.mockResolvedValue({
      ok: false,
      message:
        "That photograph is larger than 40 megapixels. Your camera is probably " +
        "set to its highest resolution — lower it and take the photo again, or " +
        "send a smaller copy of it.",
    });
    renderUpload();
    acknowledge();

    fireEvent.change(camera(), { target: { files: [file("huge.jpg")] } });

    expect(await screen.findByText(/larger than 40 megapixels/i)).toBeInTheDocument();
    expect(screen.getByText(/lower it and take the photo again/i)).toBeInTheDocument();
  });
});

describe("InspirationUpload — the reference budget", () => {
  // The budget used to be SHARED with curated catalogue presets, so the count
  // was passed in already spent. ADR 0025 left uploads as the only thing
  // drawing on it, so this component owns the arithmetic — which is why these
  // now vary `uploads` rather than a remaining count.
  it("says how many slots are free", () => {
    renderUpload({ uploads: [made("u1")] });
    expect(screen.getByText(/2 of your 3 reference slots are free/i)).toBeInTheDocument();
  });

  it("uses the singular for one remaining slot", () => {
    renderUpload({ uploads: [made("u1"), made("u2", 2)] });
    expect(screen.getByText(/1 of your 3 reference slots is free/i)).toBeInTheDocument();
  });

  it("closes the ways in when the budget is spent, even with the affirmation given", () => {
    renderUpload({ uploads: [made("u1"), made("u2", 2), made("u3", 3)] });
    acknowledge();
    expect(chooser()).toBeDisabled();
    expect(camera()).toBeDisabled();
    expect(screen.getByText(/used all of your reference slots/i)).toBeInTheDocument();
  });

  it("never reports a negative budget if the cap is lowered below what exists", () => {
    renderUpload({ max: 1, uploads: [made("u1"), made("u2", 2)] });
    expect(screen.getByText(/used all of your reference slots/i)).toBeInTheDocument();
    expect(chooser()).toBeDisabled();
  });
});

describe("InspirationUpload — before the draft exists", () => {
  it("explains itself and refuses to add rather than rendering nothing", () => {
    renderUpload({ designId: undefined });
    // The disclosure is still readable: someone should be able to understand
    // what this step will do before they have answered anything.
    expect(screen.getByText(/perpetual, irrevocable licence/i)).toBeInTheDocument();
    acknowledge();
    expect(chooser()).toBeDisabled();
    expect(screen.getByText(/your design has to exist/i)).toBeInTheDocument();
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
