// The reference step (Phase 16B ADR 0018/0019; the whole step since ADR 0025;
// the phone handoff alone since ADR 0026's amendment).
//
// The load-bearing behaviours here are honesty and what is ABSENT: this screen
// no longer offers a device-local way to add a photograph, and therefore no
// longer takes a rights affirmation of its own. The affirmation that matters is
// the phone's, taken from the person who chose the photograph — asserted in
// `../../app/r/page.test.tsx`, not here, because a screen that cannot upload is
// the wrong place to claim it.
//
// What remains is the budget line, the handoff panel, and removal — removal
// being the stylist's job, because the phone deliberately has no read or delete
// capability (ADR 0026).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { InspirationUpload as Upload } from "@/lib/api";

import { resetHandoffCoordination } from "./handoff-coordination";
import { InspirationUpload } from "./InspirationUpload";

const uploadImage = vi.fn();
const removeUpload = vi.fn();
// The reference step nests the phone-handoff panel (Phase 22, ADR 0026), which
// mints and revokes grants of its own — and now mints one as soon as it mounts.
// Stubbed here so this suite never reaches the network; the handoff has its own
// suite in phone-handoff.test.tsx.
const createGrant = vi.fn();
const revokeGrants = vi.fn();
const fetchReferences = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    uploadInspirationImage: (...args: unknown[]) => uploadImage(...args),
    removeInspirationUpload: (...args: unknown[]) => removeUpload(...args),
    createReferenceGrant: (...args: unknown[]) => createGrant(...args),
    revokeReferenceGrants: (...args: unknown[]) => revokeGrants(...args),
    fetchDesignReferences: (...args: unknown[]) => fetchReferences(...args),
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

beforeEach(() => {
  // The handoff's cross-mount coordination outlives a component on purpose,
  // so a test that stops a code would otherwise suppress the next test's.
  resetHandoffCoordination();
  uploadImage.mockReset();
  removeUpload.mockReset();
  createGrant.mockReset();
  revokeGrants.mockReset();
  fetchReferences.mockReset();
  revokeGrants.mockResolvedValue({ ok: true });
  createGrant.mockResolvedValue({
    ok: true,
    grant: {
      id: "g1",
      token: "a-plaintext-handoff-secret-value",
      expires_at: new Date(Date.now() + 900_000).toISOString(),
      slots_remaining: 3,
    },
  });
  fetchReferences.mockResolvedValue([]);
});

describe("InspirationUpload — the phone is the only way in", () => {
  it("offers no file picker and no camera on this device", () => {
    renderUpload();
    // Asserted as the absence of any file input at all, not as the absence of
    // two particular labels: a renamed control would slip past that.
    expect(document.querySelectorAll('input[type="file"]')).toHaveLength(0);
  });

  it("takes no rights affirmation on this device", () => {
    // Not a weakening. This checkbox only ever gated this screen's own picker
    // and camera; with those gone it gated nothing. The affirmation is taken on
    // the PHONE, per upload, from the person who chose the photograph — an
    // affirmation ticked here by whoever holds the shop's screen was never
    // allowed to satisfy that one (ADR 0026), and now cannot be mistaken for it.
    renderUpload();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("never calls the device-local upload endpoint", () => {
    renderUpload({ uploads: [made("u1")] });
    expect(uploadImage).not.toHaveBeenCalled();
  });

  it("shows the handoff panel rather than a control that reveals it", () => {
    renderUpload();
    expect(
      screen.getByRole("heading", { name: /send from your phone/i }),
    ).toBeInTheDocument();
  });
});

describe("InspirationUpload — the reference budget", () => {
  // The budget used to be SHARED with curated catalogue presets, so the count
  // was passed in already spent. ADR 0025 left uploads as the only thing
  // drawing on it, so this component owns the arithmetic — which is why these
  // vary `uploads` rather than a remaining count.
  it("says how many slots are free", () => {
    renderUpload({ uploads: [made("u1")] });
    expect(screen.getByText(/2 of 3 free/i)).toBeInTheDocument();
  });

  it("says so when the budget is spent", () => {
    renderUpload({ uploads: [made("u1"), made("u2", 2), made("u3", 3)] });
    expect(screen.getByText(/all slots are used/i)).toBeInTheDocument();
  });

  it("never reports a negative budget if the cap is lowered below what exists", () => {
    renderUpload({ max: 1, uploads: [made("u1"), made("u2", 2)] });
    expect(screen.getByText(/all slots are used/i)).toBeInTheDocument();
    expect(screen.queryByText(/-1/)).not.toBeInTheDocument();
  });
});

describe("InspirationUpload — before the draft exists", () => {
  it("explains itself rather than rendering nothing", () => {
    renderUpload({ designId: undefined });
    expect(screen.getByText(/your design has to exist/i)).toBeInTheDocument();
  });

  it("mints no handoff code for a design that does not exist yet", () => {
    renderUpload({ designId: undefined });
    expect(createGrant).not.toHaveBeenCalled();
  });
});

describe("InspirationUpload — previews and removal", () => {
  it("renders a preview per upload from the ownership-checked endpoint", () => {
    renderUpload({ uploads: [made("u1"), made("u2", 2)] });
    const previews = screen.getAllByAltText(/uploaded inspiration image/i);
    expect(previews).toHaveLength(2);
    expect(previews[0]).toHaveAttribute(
      "src",
      "/api/v1/designs/design-1/inspiration-uploads/u1/image/",
    );
    // Never next/image: these bytes must not be proxied or cached.
    expect(previews[0].getAttribute("src")).not.toMatch(/_next\/image/);
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

    await waitFor(() =>
      expect(
        screen.getByRole("status", { name: /changes to your photographs/i }),
      ).toHaveTextContent(/could not be/i),
    );
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

  it("puts the removal announcement in its own named live region", () => {
    // Two live regions on one screen — this one and the handoff panel's — leave
    // a screen-reader user unable to tell which just spoke unless both are
    // named.
    renderUpload({ uploads: [made("u1")] });
    expect(
      screen.getByRole("status", { name: /changes to your photographs/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("status", { name: /arriving from a phone/i }),
    ).toBeInTheDocument();
  });
});
