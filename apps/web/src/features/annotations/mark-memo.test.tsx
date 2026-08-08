import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AnnotationWorkspace } from "./AnnotationWorkspace";
import type { AnnotationDocument, AnnotationItem } from "@/lib/api";

// Its own file because it mocks `./MarkShapes`, which the main workspace suite
// needs real in order to inspect rendered geometry.
//
// It exists because a DOM-node-identity assertion — the obvious way to test this —
// proves nothing. React's keyed reconciliation preserves an unchanged sibling's
// host node whenever its key and element type are unchanged, whether or not `memo`
// bailed out of re-invoking it. `memo` decides whether the render FUNCTION runs;
// the reconciler decides whether the node survives, and they are not the same
// question. So the thing to assert is the memo's actual precondition: that the
// callback props handed to every mark are referentially stable across a render
// caused by a different mark. If they are not, the shallow compare fails for all
// of them and up to a hundred SVG groups are rebuilt per pointermove.

type SeenProps = { item: AnnotationItem; onSelect: unknown; onRequestEdit: unknown };

const seen = vi.hoisted(() => ({ props: [] as SeenProps[] }));

vi.mock("./MarkShapes", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./MarkShapes")>();
  return {
    ...actual,
    // Records what it was handed, and renders the least possible.
    MarkShape: (props: SeenProps) => {
      seen.props.push(props);
      return <g className="mark" data-mark-id={props.item.id} />;
    },
  };
});

const api = vi.hoisted(() => ({
  fetchAnnotations: vi.fn(),
  saveAnnotations: vi.fn(),
  clearAnnotations: vi.fn(),
  sendRenderToAccount: vi.fn(),
  fetchDesignResult: vi.fn(),
  fetchDesignImageUrls: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: null }) }));

const IMAGE_WIDTH = 1000;
const IMAGE_HEIGHT = 2000;

function pin(order: number): AnnotationItem {
  return {
    id: `id-${order}`,
    type: "pin",
    geometry: { point: { x: 0.5, y: 0.5 } },
    note: "",
    palette: "terracotta",
    created_order: order,
  } as AnnotationItem;
}

function document_(items: AnnotationItem[]): AnnotationDocument {
  return {
    schema_version: 1,
    image_width: IMAGE_WIDTH,
    image_height: IMAGE_HEIGHT,
    items,
    revision: items.length,
    updated_at: null,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  seen.props = [];
  api.fetchAnnotations.mockResolvedValue(document_([pin(1), pin(2), pin(3)]));
  api.saveAnnotations.mockImplementation(async (_d, _v, body) => ({
    ok: true,
    document: { ...document_([]), items: body.items, revision: body.expected_revision + 1 },
  }));
  api.fetchDesignResult.mockResolvedValue({
    ok: true,
    result: { title: "t", version_number: 1, is_demo: true, image_alt_text: "alt" },
  });
  api.fetchDesignImageUrls.mockResolvedValue({
    ok: true,
    images: {
      original: { url: "https://minio.local/x", download_url: "https://minio.local/d", width: IMAGE_WIDTH, height: IMAGE_HEIGHT },
      expires_at: new Date(Date.now() + 600_000).toISOString(),
    },
  });
});

describe("mark memoisation", () => {
  it("hands every mark the same callback references across an unrelated edit", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}>
        <AnnotationWorkspace designId="design-1" versionId="version-1" />
      </QueryClientProvider>,
    );
    await screen.findByRole("heading", { name: "Annotate this concept" });
    await waitFor(() => expect(seen.props.length).toBeGreaterThanOrEqual(3));

    const before = seen.props.filter((entry) => entry.item.id === "id-3").at(-1)!;

    // Nudge a DIFFERENT mark — the same dispatch a drag fires on every pointermove.
    fireEvent.click(screen.getByRole("button", { name: /^annotation 1,/i }));
    fireEvent.keyDown(screen.getByRole("button", { name: /^annotation 1,/i }), {
      key: "ArrowRight",
    });

    const after = seen.props.filter((entry) => entry.item.id === "id-3").at(-1)!;
    expect(after).not.toBe(before);

    // These are what memo compares. `onRequestEdit` was the one that regressed:
    // it depended on [state.items], so every items-changing dispatch produced a
    // new closure and defeated the memo for every mark on screen at once.
    expect(after.onRequestEdit).toBe(before.onRequestEdit);
    expect(after.onSelect).toBe(before.onSelect);
    // The untouched mark's own data is unchanged too, so with stable callbacks the
    // shallow compare has nothing left to fail on.
    expect(after.item).toBe(before.item);
  });
});
