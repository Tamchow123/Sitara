import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnnotationWorkspace } from "./AnnotationWorkspace";
import { axeViolations } from "@/test-utils/axe";
import type { AnnotationDocument, AnnotationItem } from "@/lib/api";

// The API module is mocked rather than fetch, so each test drives one named
// outcome (409, network failure, 429) instead of hand-building envelopes — and so
// a test can assert on exactly what was SENT, which is where the coalescing and
// the expected_revision contract actually live.
const api = vi.hoisted(() => ({
  fetchAnnotations: vi.fn(),
  saveAnnotations: vi.fn(),
  clearAnnotations: vi.fn(),
  sendRenderToAccount: vi.fn(),
  fetchDesignResult: vi.fn(),
  fetchDesignImageUrls: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

const auth = vi.hoisted(() => ({ user: { id: "u1", email: "bride@example.test" } as unknown }));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));

const IMAGE_WIDTH = 1000;
const IMAGE_HEIGHT = 2000;

function emptyDocument(overrides: Partial<AnnotationDocument> = {}): AnnotationDocument {
  return {
    schema_version: 1,
    image_width: IMAGE_WIDTH,
    image_height: IMAGE_HEIGHT,
    items: [],
    revision: 0,
    updated_at: null,
    ...overrides,
  };
}

function pin(order: number, note = ""): AnnotationItem {
  return {
    id: `id-${order}`,
    type: "pin",
    geometry: { point: { x: 0.5, y: 0.5 } },
    note,
    palette: "terracotta",
    created_order: order,
  } as AnnotationItem;
}

function renderWorkspace() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AnnotationWorkspace designId="design-1" versionId="version-1" />
    </QueryClientProvider>,
  );
}

/** Draw a mark by pressing and releasing on the stage. */
function drawOnStage(container: HTMLElement, from = { x: 300, y: 400 }, to = from) {
  const stage = container.querySelector(".annotation-stage") as HTMLElement;
  // jsdom reports a zero-sized box, so the rect is stubbed — the coordinate maths
  // itself is covered exhaustively in geometry.test.ts.
  stage.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 500, height: 1000, right: 500, bottom: 1000 }) as DOMRect;
  fireEvent.pointerDown(stage, { clientX: from.x, clientY: from.y, pointerId: 1 });
  if (to !== from) fireEvent.pointerMove(stage, { clientX: to.x, clientY: to.y, pointerId: 1 });
  fireEvent.pointerUp(stage, { clientX: to.x, clientY: to.y, pointerId: 1 });
}

/**
 * Press and release at real client coordinates.
 *
 * `fireEvent.pointerDown(node, { clientX })` does NOT work here: jsdom implements
 * no `PointerEvent`, so testing-library falls back to a plain `Event` and the
 * coordinates never reach `event.clientX` — they arrive as 0. Every mark drawn by
 * `drawOnStage` is therefore created at (0, 0), which is a perfectly valid pin, so
 * nothing has ever failed over it and no test has ever exercised the
 * client-to-normalised conversion through the component. Defining the properties
 * explicitly is what makes that path testable.
 */
function pressAt(stage: HTMLElement, clientX: number, clientY: number) {
  for (const type of ["pointerdown", "pointerup"]) {
    const event = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(event, "clientX", { value: clientX });
    Object.defineProperty(event, "clientY", { value: clientY });
    Object.defineProperty(event, "pointerId", { value: 1 });
    fireEvent(stage, event);
  }
}

async function loaded() {
  const view = renderWorkspace();
  await screen.findByRole("heading", { name: "Annotate this concept" });
  return view;
}

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { id: "u1", email: "bride@example.test" };
  api.fetchAnnotations.mockResolvedValue(emptyDocument());
  api.saveAnnotations.mockImplementation(async (_d, _v, body) => ({
    ok: true,
    document: { ...emptyDocument(), items: body.items, revision: body.expected_revision + 1 },
  }));
  api.clearAnnotations.mockResolvedValue({ ok: true });
  api.sendRenderToAccount.mockResolvedValue({ ok: true });
  api.fetchDesignResult.mockResolvedValue({
    ok: true,
    result: {
      title: "Ivory lehenga",
      version_number: 1,
      is_demo: true,
      image_alt_text: "A model in a lehenga.",
    },
  });
  api.fetchDesignImageUrls.mockResolvedValue({
    ok: true,
    images: {
      original: {
        url: "https://minio.local/signed-1",
        download_url: "https://minio.local/dl-1",
        width: IMAGE_WIDTH,
        height: IMAGE_HEIGHT,
      },
      expires_at: new Date(Date.now() + 600_000).toISOString(),
    },
  });
});

afterEach(() => {
  vi.useRealTimers();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

// ---------------------------------------------------------------------------
// Tools and mark creation
// ---------------------------------------------------------------------------

describe("tools and mark creation", () => {
  it("starts in select mode with the empty state showing", async () => {
    await loaded();
    expect(screen.getByRole("button", { name: /select or pan/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByText("Nothing marked yet")).toBeInTheDocument();
  });

  it("creates a pin, numbers it, and opens its note editor", async () => {
    const { container } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);

    expect(screen.getByRole("heading", { name: /annotations · 1/i })).toBeInTheDocument();
    // §11's empty state promises every mark takes a short note, so a new mark
    // lands in the editor rather than needing a second click to find it.
    expect(screen.getByLabelText("Note for annotation 1")).toBeInTheDocument();
  });

  it("selects a tool by keyboard shortcut", async () => {
    await loaded();
    fireEvent.keyDown(window, { key: "r" });
    expect(screen.getByRole("button", { name: /rectangle \(r\)/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("does not create a mark from a degenerate drag", async () => {
    // A mis-click with the rectangle tool should feel like a mis-click, not
    // produce a zero-area mark the server would reject on save.
    const { container } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: /rectangle \(r\)/i }));
    drawOnStage(container, { x: 100, y: 100 }, { x: 100, y: 101 });

    expect(screen.getByRole("heading", { name: /annotations · 0/i })).toBeInTheDocument();
  });

  it("refuses to pass the mark ceiling and says so", async () => {
    api.fetchAnnotations.mockResolvedValue(
      emptyDocument({
        items: Array.from({ length: 100 }, (_, index) => pin(index + 1)),
        revision: 3,
      }),
    );
    const { container } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);

    expect(screen.getByText(/most marks one concept can hold/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /annotations · 100/i })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// The list: editing, palette, deletion, selection sync
// ---------------------------------------------------------------------------

describe("the annotation list", () => {
  beforeEach(() => {
    api.fetchAnnotations.mockResolvedValue(
      emptyDocument({ items: [pin(1, "Hem too narrow"), pin(2)], revision: 2 }),
    );
  });

  it("announces number, type and note — and says 'no note' when there is none", async () => {
    await loaded();
    expect(
      screen.getByRole("button", { name: "Annotation 1, pin: Hem too narrow" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Annotation 2, pin, no note" })).toBeInTheDocument();
  });

  it("gives every row a positional description in words", async () => {
    // The overlay cannot be the only way to know where a mark is.
    await loaded();
    expect(
      screen.getAllByText("centred at 50% across, 50% from the top").length,
    ).toBeGreaterThan(0);
  });

  it("edits a note and keeps a live counter", async () => {
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: /edit note for annotation 2/i }));
    const field = screen.getByLabelText("Note for annotation 2");
    fireEvent.change(field, { target: { value: "Raise the neckline" } });

    expect(screen.getByText("18/140")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText("Raise the neckline")).toBeInTheDocument();
  });

  it("cancels an edit without changing the note", async () => {
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: /edit note for annotation 1/i }));
    fireEvent.change(screen.getByLabelText("Note for annotation 1"), {
      target: { value: "discarded" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByText("Hem too narrow")).toBeInTheDocument();
    expect(screen.queryByText("discarded")).not.toBeInTheDocument();
  });

  it("selects a palette through a labelled control, never colour alone", async () => {
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: /edit note for annotation 1/i }));

    const sage = screen.getByRole("radio", { name: "Sage" });
    expect(sage).toBeInTheDocument();
    fireEvent.click(sage);
    expect(sage).toBeChecked();
  });

  it("deletes a mark and renumbers the rest", async () => {
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: /delete annotation 1/i }));

    expect(screen.getByRole("heading", { name: /annotations · 1/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Annotation 1, pin, no note" })).toBeInTheDocument();
  });

  it("keeps row selection and canvas selection in step", async () => {
    const { container } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: /annotation 2, pin, no note/i }));

    const marks = container.querySelectorAll(".mark");
    // The second mark carries the selected class and its aria-pressed is true,
    // which is what the overlay and the list have to agree on.
    expect(marks[1]).toHaveClass("is-selected");
    expect(marks[1]).toHaveAttribute("aria-pressed", "true");
    expect(marks[0]).not.toHaveClass("is-selected");
  });

  it("selects from the canvas and reflects it in the list", async () => {
    const { container } = await loaded();
    const marks = container.querySelectorAll(".mark");
    fireEvent.pointerDown(marks[1]!, { pointerId: 1 });

    expect(screen.getByRole("button", { name: /annotation 2, pin, no note/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("clears everything only after a confirmation", async () => {
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: /clear all/i }));

    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveAccessibleName("Clear all 2 annotations?");
    fireEvent.click(within(dialog).getByRole("button", { name: /keep them/i }));
    expect(screen.getByRole("heading", { name: /annotations · 2/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /clear all/i }));
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: /^clear all$/i }),
    );
    await waitFor(() => expect(api.clearAnnotations).toHaveBeenCalled());
  });
});

// ---------------------------------------------------------------------------
// Keyboard behaviour (§13)
// ---------------------------------------------------------------------------

describe("keyboard behaviour", () => {
  beforeEach(() => {
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1)], revision: 1 }));
  });

  it("Enter on a row opens its note editor", async () => {
    await loaded();
    const row = screen
      .getByRole("button", { name: "Annotation 1, pin, no note" })
      .closest("li")!;
    fireEvent.keyDown(row, { key: "Enter" });
    expect(screen.getByLabelText("Note for annotation 1")).toBeInTheDocument();
  });

  it("nudges with the arrow keys, and further with Shift", async () => {
    const { container } = await loaded();
    const row = screen
      .getByRole("button", { name: "Annotation 1, pin, no note" })
      .closest("li")!;

    fireEvent.keyDown(row, { key: "ArrowRight" });
    const afterFine = container.querySelector(".mark-fill")!.getAttribute("points");

    fireEvent.keyDown(row, { key: "ArrowRight", shiftKey: true });
    const afterCoarse = container.querySelector(".mark-fill")!.getAttribute("points");

    expect(afterFine).not.toBe(afterCoarse);
    expect(screen.getByText(/across/)).toBeInTheDocument();
  });

  it("Delete removes a selected mark, but never from inside a text field", async () => {
    await loaded();
    const row = screen
      .getByRole("button", { name: "Annotation 1, pin, no note" })
      .closest("li")!;

    fireEvent.click(screen.getByRole("button", { name: /edit note for annotation 1/i }));
    // Backspace inside the note must delete a character, not the mark being
    // annotated.
    fireEvent.keyDown(screen.getByLabelText("Note for annotation 1"), { key: "Backspace" });
    expect(screen.getByRole("heading", { name: /annotations · 1/i })).toBeInTheDocument();

    fireEvent.keyDown(row, { key: "Delete" });
    expect(screen.getByRole("heading", { name: /annotations · 0/i })).toBeInTheDocument();
  });

  it("Escape closes the editor first, then clears the selection, then leaves the tool", async () => {
    await loaded();
    fireEvent.keyDown(window, { key: "p" });
    fireEvent.click(screen.getByRole("button", { name: /edit note for annotation 1/i }));

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByLabelText("Note for annotation 1")).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" }); // drops the selection
    fireEvent.keyDown(window, { key: "Escape" }); // returns to select mode
    expect(screen.getByRole("button", { name: /select or pan/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("hides and shows every mark with H, without deleting any", async () => {
    const { container } = await loaded();
    fireEvent.keyDown(window, { key: "h" });

    expect(container.querySelector(".annotation-overlay")).not.toBeInTheDocument();
    expect(screen.getByText(/overlays hidden/i)).toBeInTheDocument();
    // Still one mark, still in the list — hidden is not deleted.
    expect(screen.getByRole("heading", { name: /annotations · 1/i })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "h" });
    expect(container.querySelector(".annotation-overlay")).toBeInTheDocument();
  });

  it("undoes and redoes with the platform modifier", async () => {
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: /delete annotation 1/i }));
    expect(screen.getByRole("heading", { name: /annotations · 0/i })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    expect(screen.getByRole("heading", { name: /annotations · 1/i })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "z", ctrlKey: true, shiftKey: true });
    expect(screen.getByRole("heading", { name: /annotations · 0/i })).toBeInTheDocument();
  });

  it("moves focus within the toolbar with the arrow keys", async () => {
    await loaded();
    const select = screen.getByRole("button", { name: /select or pan/i });
    select.focus();
    fireEvent.keyDown(screen.getByRole("toolbar"), { key: "ArrowDown" });
    expect(document.activeElement).not.toBe(select);
  });
});

// ---------------------------------------------------------------------------
// Autosave (§14)
// ---------------------------------------------------------------------------

describe("autosave", () => {
  it("debounces, then saves with the revision it holds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1)], revision: 4 }));
    const { container } = await loaded();

    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);
    expect(api.saveAnnotations).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    await waitFor(() => expect(api.saveAnnotations).toHaveBeenCalledTimes(1));

    const body = api.saveAnnotations.mock.calls[0]![2];
    expect(body.expected_revision).toBe(4);
    expect(body.image_width).toBe(IMAGE_WIDTH);
    expect(body.items).toHaveLength(2);
  });

  it("keeps one save in flight and coalesces later edits into one follow-up", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let release: ((value: unknown) => void) | null = null;
    api.saveAnnotations.mockImplementationOnce(
      (_d: string, _v: string, body: { expected_revision: number; items: AnnotationItem[] }) =>
        new Promise((resolve) => {
          release = () =>
            resolve({
              ok: true,
              document: {
                ...emptyDocument(),
                items: body.items,
                revision: body.expected_revision + 1,
              },
            });
        }),
    );
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1)], revision: 1 }));
    const { container } = await loaded();

    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container, { x: 100, y: 100 });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    await waitFor(() => expect(api.saveAnnotations).toHaveBeenCalledTimes(1));

    // Three more edits while the first request is still open. Each debounce tick
    // must NOT start its own request.
    drawOnStage(container, { x: 150, y: 150 });
    drawOnStage(container, { x: 200, y: 200 });
    drawOnStage(container, { x: 250, y: 250 });
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(api.saveAnnotations).toHaveBeenCalledTimes(1);

    await act(async () => {
      release?.(null);
    });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });

    // Exactly ONE follow-up, carrying all three later marks at once.
    await waitFor(() => expect(api.saveAnnotations).toHaveBeenCalledTimes(2));
    expect(api.saveAnnotations.mock.calls[1]![2].items).toHaveLength(5);
  });

  it("reports a failure and retries only when asked", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.saveAnnotations.mockResolvedValueOnce({
      ok: false,
      kind: "failed",
      status: 0,
      code: "unavailable",
      message: "Your notes could not be saved.",
    });
    const { container } = await loaded();

    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    await screen.findByText(/couldn’t save/i);

    // No automatic retry loop: a flaky connection must not re-fire on every tick.
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(api.saveAnnotations).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(api.saveAnnotations).toHaveBeenCalledTimes(2));
    await screen.findByText("Saved");
  });

  it("saves on editor commit, carrying the text that was just typed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1)], revision: 3 }));
    await loaded();

    fireEvent.click(screen.getByRole("button", { name: /edit note for annotation 1/i }));
    fireEvent.change(screen.getByLabelText("Note for annotation 1"), {
      target: { value: "Raise the neckline" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // Immediately, without the debounce: committing an editor is a deliberate
    // act. And the payload has to contain the note — a `dispatch` is not visible
    // to the same synchronous call, so a flush that reads the hook's own snapshot
    // ref would either no-op or send the PRE-edit marks.
    await waitFor(() => expect(api.saveAnnotations).toHaveBeenCalledTimes(1));
    expect(api.saveAnnotations.mock.calls[0]![2].items[0]!.note).toBe("Raise the neckline");
  });

  it("pushes the debounce out on each edit rather than firing after the first", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { container } = await loaded();

    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container, { x: 100, y: 100 });
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    drawOnStage(container, { x: 200, y: 200 });

    // 900ms after the FIRST edit but only 400ms after the second. A debounce keyed
    // on a derived "is dirty" boolean never re-armed — it stayed true from the
    // first keystroke on, so the timer set at t=0 fired here and kept re-firing
    // while the user was still working.
    await act(async () => {
      vi.advanceTimersByTime(400);
    });
    expect(api.saveAnnotations).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    await waitFor(() => expect(api.saveAnnotations).toHaveBeenCalledTimes(1));
    expect(api.saveAnnotations.mock.calls[0]![2].items).toHaveLength(2);
  });

  it("does not let a save that was already in flight undo a clear", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1)], revision: 4 }));
    let release: (() => void) | null = null;
    api.saveAnnotations.mockImplementationOnce(
      (_d: string, _v: string, body: { expected_revision: number; items: AnnotationItem[] }) =>
        new Promise((resolve) => {
          release = () =>
            resolve({
              ok: true,
              document: {
                ...emptyDocument(),
                items: body.items,
                revision: body.expected_revision + 1,
              },
            });
        }),
    );
    const { container } = await loaded();

    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    await waitFor(() => expect(api.saveAnnotations).toHaveBeenCalledTimes(1));

    // Cleared while that PUT is still open. The trigger and the dialog's confirm
    // share a label, so the confirm is scoped to the dialog.
    fireEvent.click(screen.getByRole("button", { name: /clear all/i }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /^clear all$/i }));
    await waitFor(() => expect(api.clearAnnotations).toHaveBeenCalledTimes(1));
    await screen.findByRole("heading", { name: /annotations · 0/i });

    // Now it lands. Its confirmation names a revision the clear has retired, so
    // applying it would put two marks back on a document the server says is empty
    // — and then 409 on the next write.
    await act(async () => {
      release?.();
    });
    expect(screen.getByRole("heading", { name: /annotations · 0/i })).toBeInTheDocument();
    // And the pill tells the truth about it. Comparing only against what was sent
    // would leave it claiming unsaved changes over a document that is in step.
    expect(await screen.findByText("Saved")).toBeInTheDocument();

    // The next write must go out at revision 0, because the DELETE removed the row
    // and the server accepts only 0 in that state. Adopting the stale request's
    // revision here would 409 against a document nobody else has touched, and both
    // recovery actions end at the server's copy — so the mark drawn below would be
    // silently discarded.
    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container, { x: 120, y: 160 });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    await waitFor(() => expect(api.saveAnnotations).toHaveBeenCalledTimes(2));
    expect(api.saveAnnotations.mock.calls[1]![2].expected_revision).toBe(0);
  });

  it("drops a debounce that has not fired yet when the marks are cleared", async () => {
    // The OTHER half of the clear-versus-save race, and the one `cancelPending`
    // exists for. The in-flight test above advances past the debounce first, so by
    // the time Clear is pressed the timer has already fired and there is nothing
    // left to cancel — this one presses Clear with the timer still counting down.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1)], revision: 4 }));
    const { container } = await loaded();

    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);
    // Deliberately NOT advanced: the write is pending, not sent.
    expect(api.saveAnnotations).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /clear all/i }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /^clear all$/i }));
    await waitFor(() => expect(api.clearAnnotations).toHaveBeenCalledTimes(1));

    // Well past the debounce. A surviving timer would PUT the pre-clear marks
    // after the DELETE, restoring what the user just removed at a revision the
    // server has retired — and then 409 on every later write.
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(api.saveAnnotations).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: /annotations · 0/i })).toBeInTheDocument();
  });

  it("tells the server about a clear even when the first save has not landed yet", async () => {
    // The clear fast path used to be chosen on `revision === 0` alone. That stays 0
    // for as long as a first-ever save is in flight, so a clear pressed in that
    // window skipped the DELETE entirely and never told the server about the
    // document the open write was in the act of creating — the marks survived
    // server-side, invisible, and returned on the next visit.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ revision: 0 }));
    let release: (() => void) | null = null;
    api.saveAnnotations.mockImplementationOnce(
      (_d: string, _v: string, body: { items: AnnotationItem[] }) =>
        new Promise((resolve) => {
          release = () =>
            resolve({
              ok: true,
              document: { ...emptyDocument(), items: body.items, revision: 1 },
            });
        }),
    );
    const { container } = await loaded();

    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    await waitFor(() => expect(api.saveAnnotations).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: /clear all/i }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /^clear all$/i }));

    // The DELETE goes out despite the revision still reading 0.
    await waitFor(() => expect(api.clearAnnotations).toHaveBeenCalledTimes(1));

    // And when the first save finally lands, its marks do not come back.
    await act(async () => {
      release?.();
    });
    expect(screen.getByRole("heading", { name: /annotations · 0/i })).toBeInTheDocument();
  });

  it("never writes annotation state to browser storage", async () => {
    // All three stores, because the requirement is that the document stays
    // memory-only. jsdom has no IndexedDB, so rather than assert that absence —
    // which is a fact about jsdom and would pass no matter what this code did — a
    // spy stands in for the API and is asserted never to have been opened.
    const localSet = vi.spyOn(Storage.prototype, "setItem");
    const indexedDbOpen = vi.fn();
    vi.stubGlobal("indexedDB", { open: indexedDbOpen, databases: vi.fn(), deleteDatabase: vi.fn() });

    const { container } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);
    fireEvent.change(screen.getByLabelText("Note for annotation 1"), {
      target: { value: "private note" },
    });

    expect(localSet).not.toHaveBeenCalled();
    expect(indexedDbOpen).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(JSON.stringify(window.localStorage)).not.toContain("private note");
    vi.unstubAllGlobals();
  });
});

// ---------------------------------------------------------------------------
// Conflict (§14)
// ---------------------------------------------------------------------------

describe("a 409 conflict", () => {
  beforeEach(() => {
    api.saveAnnotations.mockResolvedValue({
      ok: false,
      kind: "conflict",
      revision: 9,
      message: "These notes were changed somewhere else since your last save.",
    });
  });

  it("opens a dialog that discards neither copy, and suspends autosave", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { container } = await loaded();

    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);
    await act(async () => {
      vi.advanceTimersByTime(900);
    });

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveAccessibleName("Newer notes elsewhere");
    // The local mark is still there — nothing was thrown away to show this.
    expect(screen.getByRole("heading", { name: /annotations · 1/i })).toBeInTheDocument();

    // Autosave is suspended: a flaky connection must not re-fire a doomed write
    // on every tick and keep re-opening this dialog mid-edit.
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(api.saveAnnotations).toHaveBeenCalledTimes(1);
  });

  it("does not claim another tab, which it cannot know", async () => {
    // An ordinary single-tab lost-response retry produces the identical 409.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { container } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);
    await act(async () => {
      vi.advanceTimersByTime(900);
    });

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).not.toMatch(/another tab|two minutes ago/i);
    expect(dialog.textContent).toMatch(/annotated somewhere else/i);
  });

  it("reloads the server's copy when asked", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { container } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    await screen.findByRole("alertdialog");

    api.fetchAnnotations.mockResolvedValue(
      emptyDocument({ items: [pin(1), pin(2), pin(3)], revision: 9 }),
    );
    fireEvent.click(screen.getByRole("button", { name: /reload latest/i }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /annotations · 3/i })).toBeInTheDocument(),
    );
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("ends at the server's copy when the user discards instead", async () => {
    // Both buttons must end here. Keeping the local copy and overwriting the
    // other one is precisely what "nothing is overwritten silently" rules out, so
    // "Discard my changes" is the honest name for what reloading does — and it
    // needs its own test, not an assumption that it shares a handler.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { container } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    await screen.findByRole("alertdialog");

    // The server's copy is given a note the local mark does not have. Asserting on
    // the COUNT would have been worthless: one local mark and one server mark both
    // read "Annotations · 1", so a Discard handler that merely closed the dialog
    // and kept the local copy would have passed.
    api.fetchAnnotations.mockResolvedValue(
      emptyDocument({ items: [pin(7, "the server's own note")], revision: 9 }),
    );
    fireEvent.click(screen.getByRole("button", { name: /discard my changes/i }));

    // A second read of the document is what "ends at the server's copy" means.
    await waitFor(() => expect(api.fetchAnnotations).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("the server's own note")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /annotations · 1/i })).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("has no axe violations while the conflict dialog is open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { container } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    await screen.findByRole("alertdialog");

    vi.useRealTimers();
    expect(await axeViolations(container)).toHaveNoViolations();
  });
});

// ---------------------------------------------------------------------------
// Leaving with unsaved work, and the image
// ---------------------------------------------------------------------------

describe("leaving and the image", () => {
  it("warns before following the back link with unsaved edits", async () => {
    const { container } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);

    fireEvent.click(screen.getByRole("link", { name: /concept/i }));
    const popover = screen.getByRole("alertdialog", { name: "Unsaved changes" });
    expect(popover).toHaveTextContent(/haven’t saved yet/i);

    fireEvent.click(within(popover).getByRole("button", { name: "Stay" }));
    expect(screen.queryByRole("alertdialog", { name: "Unsaved changes" })).not.toBeInTheDocument();
    // "Leave anyway" is a real link, so it navigates rather than being trapped.
    fireEvent.click(screen.getByRole("link", { name: /concept/i }));
    expect(
      within(screen.getByRole("alertdialog", { name: "Unsaved changes" })).getByRole("link", {
        name: /leave anyway/i,
      }),
    ).toHaveAttribute("href", "/design/design-1/result/version-1");
  });

  it("does not warn when everything is saved", async () => {
    await loaded();
    fireEvent.click(screen.getByRole("link", { name: /concept/i }));
    expect(screen.queryByRole("alertdialog", { name: "Unsaved changes" })).not.toBeInTheDocument();
  });

  it("asks the browser to confirm a close only while there are unsaved edits", async () => {
    // The in-app link has its own popover; this covers the tab-close and reload
    // path, which no popover can intercept. `preventDefault` is what actually
    // triggers the browser's own prompt, so that is what is asserted — not merely
    // that a listener exists.
    const { container } = await loaded();

    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);

    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });

  it("leaves an untouched mark's rendered output alone when another is nudged", async () => {
    // Deliberately NOT a memoisation test. An earlier version of this claimed to be
    // one, asserting DOM-node identity — which proves nothing about `memo`, because
    // React's keyed reconciliation preserves an unchanged sibling's host node
    // whether or not the render function was skipped. The memo's real precondition
    // is callback-reference stability, and that is asserted in `mark-memo.test.tsx`.
    //
    // What this does prove is worth keeping on its own: nudging one mark does not
    // disturb another's rendered geometry.
    api.fetchAnnotations.mockResolvedValue(
      emptyDocument({ items: [pin(1), pin(2), pin(3)], revision: 3 }),
    );
    const { container } = await loaded();

    const groups = () => Array.from(container.querySelectorAll(".annotation-overlay .mark"));
    expect(groups()).toHaveLength(3);
    const before = groups()[2]!.innerHTML;

    fireEvent.click(screen.getByRole("button", { name: /^annotation 1,/i }));
    fireEvent.keyDown(screen.getByRole("button", { name: /^annotation 1,/i }), {
      key: "ArrowRight",
    });

    expect(groups()[2]!.innerHTML).toBe(before);
    // ...and the nudged one DID move, so the assertion above is not vacuous.
    expect(groups()[0]!.innerHTML).not.toBe(before);
  });

  it("re-measures the stage on every press, so a resize between them is honoured", async () => {
    // The bounds are cached between presses for the observer's benefit. If a press
    // trusted that cache, a zoom or a panel opening in between would convert the
    // next gesture against a stale box and put the mark somewhere the user did not
    // click — with nothing visibly wrong until the render was reloaded.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { container } = await loaded();
    const stage = container.querySelector(".annotation-stage") as HTMLElement;
    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));

    const rect = (width: number, height: number) =>
      ({ left: 0, top: 0, width, height, right: width, bottom: height }) as DOMRect;

    stage.getBoundingClientRect = () => rect(500, 1000);
    pressAt(stage, 250, 500);

    // The box halves. The same client coordinates are now a different fraction of
    // it, and the second mark must record that.
    stage.getBoundingClientRect = () => rect(250, 500);
    pressAt(stage, 250, 500);

    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    await waitFor(() => expect(api.saveAnnotations).toHaveBeenCalled());

    // Asserted on what was SENT rather than on the spoken description, because the
    // stored coordinate is the thing that has to be right.
    const items = api.saveAnnotations.mock.calls.at(-1)![2].items;
    expect(items).toHaveLength(2);
    expect(items[0]!.geometry).toEqual({ point: { x: 0.5, y: 0.5 } });
    // The far corner of the halved box, not the 0.5/0.5 it would have recorded
    // against the stale 500x1000 one.
    expect(items[1]!.geometry).toEqual({ point: { x: 1, y: 1 } });
  });

  it("keeps marks and unsaved notes alive when the image fails to load", async () => {
    // A failed image must not destroy work that has not been saved yet.
    const { container } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);
    fireEvent.change(screen.getByLabelText("Note for annotation 1"), {
      target: { value: "survives" },
    });

    fireEvent.error(container.querySelector(".annotation-image")!);

    expect(screen.getByText(/could not be loaded/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /annotations · 1/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Note for annotation 1")).toHaveValue("survives");
  });

  it("recovers from a failed image-URL fetch instead of loading for ever", async () => {
    // The defect ARCH-001 fixed, which the test above does NOT cover: that one
    // fires the <img> element's own onError, which can only happen once a src
    // exists. When the URL FETCH fails there is no src at all, so the <img> never
    // mounts, its onError never fires, and the canvas sat on "Loading…"
    // indefinitely — indistinguishable from a slow network and unrecoverable
    // without a full reload.
    api.fetchDesignImageUrls.mockRejectedValue(new Error("image_unavailable"));
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1, "kept")], revision: 1 }));
    await loaded();

    const alert = await screen.findByText(/could not be fetched/i);
    expect(alert).toBeInTheDocument();
    expect(screen.queryByText(/loading your concept image/i)).not.toBeInTheDocument();
    // The marks and notes are untouched by an image failure. The row is not in
    // edit mode here, so the note is static text rather than a labelled field.
    expect(screen.getByRole("heading", { name: /annotations · 1/i })).toBeInTheDocument();
    expect(screen.getByText("kept")).toBeInTheDocument();

    // And there is a way out. A retry that succeeds shows the image.
    api.fetchDesignImageUrls.mockResolvedValue({
      ok: true,
      images: {
        original: {
          url: "https://minio.local/signed-after-retry",
          download_url: "https://minio.local/dl-after-retry",
          width: IMAGE_WIDTH,
          height: IMAGE_HEIGHT,
        },
        expires_at: new Date(Date.now() + 600_000).toISOString(),
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /retry image/i }));

    await waitFor(() => expect(api.fetchDesignImageUrls).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(document.querySelector(".annotation-image")).toHaveAttribute(
        "src",
        "https://minio.local/signed-after-retry",
      ),
    );
  });

  it("does not move marks when the signed image URL is refreshed", async () => {
    // The overlay is independent of the <img> element's src, so a fresh URL
    // cannot shift a mark relative to the garment.
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1)], revision: 1 }));
    const { container } = await loaded();
    const before = container.querySelector(".mark-fill")!.getAttribute("points");

    const image = container.querySelector(".annotation-image") as HTMLImageElement;
    image.setAttribute("src", "https://minio.local/signed-2-different");
    fireEvent.load(image);

    expect(container.querySelector(".mark-fill")!.getAttribute("points")).toBe(before);
  });

  it("sizes the image box from the version's own dimensions, never cover", async () => {
    const { container } = await loaded();
    const stage = container.querySelector(".annotation-stage") as HTMLElement;
    expect(stage.style.aspectRatio).toBe(`${IMAGE_WIDTH} / ${IMAGE_HEIGHT}`);

    const overlay = container.querySelector(".annotation-overlay")!;
    expect(overlay).toHaveAttribute("viewBox", `0 0 ${IMAGE_WIDTH} ${IMAGE_HEIGHT}`);
    expect(overlay).toHaveAttribute("preserveAspectRatio", "xMidYMid meet");
  });

});

// ---------------------------------------------------------------------------
// What the page says about itself
// ---------------------------------------------------------------------------

describe("the workspace's own claims", () => {
  it("says the concept itself cannot change", async () => {
    await loaded();
    expect(screen.getByText(/the concept itself never changes/i)).toBeInTheDocument();
    expect(screen.getByText(/not sent back to the AI/i)).toBeInTheDocument();
  });

  it("carries the version identity and the demo label through", async () => {
    await loaded();
    expect(await screen.findByText("Version 1")).toBeInTheDocument();
    expect(screen.getByText("Demo concept")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Send to account (§14)
// ---------------------------------------------------------------------------

describe("send to account", () => {
  it("flashes a confirmation naming the account's own address", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: /send to account/i }));

    await waitFor(() =>
      expect(api.sendRenderToAccount).toHaveBeenCalledWith("design-1", "version-1", "annotated"),
    );
    expect(await screen.findByText(/sent to your email/i)).toBeInTheDocument();
    expect(
      screen.getAllByRole("status").map((node) => node.textContent).join(" "),
    ).toContain("bride@example.test");

    await act(async () => {
      vi.advanceTimersByTime(2500);
    });
    expect(screen.getByRole("button", { name: /send to account/i })).toBeInTheDocument();
  });

  it("cannot be pressed again while it still reads as sent", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await loaded();
    const button = () => screen.getByRole("button", { name: /send to account|sent to your email/i });
    fireEvent.click(button());
    await waitFor(() => expect(api.sendRenderToAccount).toHaveBeenCalledTimes(1));

    // A press on a button that says "Sent to your email ✓" is a mis-click, and the
    // endpoint would answer it with an already-terminal 202 the user could not
    // tell apart from a second real send.
    await waitFor(() => expect(button()).toBeDisabled());
    fireEvent.click(button());
    expect(api.sendRenderToAccount).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(2500);
    });
    expect(button()).toBeEnabled();
  });

  it("shows the throttle message rather than failing silently", async () => {
    api.sendRenderToAccount.mockResolvedValue({
      ok: false,
      kind: "throttled",
      message: "You have sent this many concepts for now. Please try again later.",
    });
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: /send to account/i }));

    expect(await screen.findByText(/sent this many concepts for now/i)).toBeInTheDocument();
  });

  it("disables the action for an anonymous owner and says to sign in", async () => {
    auth.user = null;
    await loaded();

    expect(screen.getByRole("button", { name: /send to account/i })).toBeDisabled();
    expect(screen.getByText(/sign in to send this to your email/i)).toBeInTheDocument();
    expect(api.sendRenderToAccount).not.toHaveBeenCalled();
  });

  it("reports a closed capability gate honestly", async () => {
    api.sendRenderToAccount.mockResolvedValue({
      ok: false,
      kind: "disabled",
      message: "Emailing your concept is not available at the moment.",
    });
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: /send to account/i }));

    expect(await screen.findByText(/not available at the moment/i)).toBeInTheDocument();
  });

  it("warns that sending takes the image out of Sitara", async () => {
    // §8.5 requires this said in the UI before the first send, not buried in a
    // policy page.
    await loaded();
    expect(screen.getByText(/may keep a copy outside Sitara/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Ownership and accessibility
// ---------------------------------------------------------------------------

describe("ownership and accessibility", () => {
  it("shows an indistinguishable not-available message for a design it cannot read", async () => {
    api.fetchAnnotations.mockRejectedValue(new Error("404"));
    renderWorkspace();
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be opened/i);
  });

  it("has no axe violations with nothing marked yet", async () => {
    // Genuinely empty — the empty-state card is its own layout, and seeding a mark
    // here (as this used to) meant the state the card appears in was never scanned.
    const { container } = await loaded();
    expect(screen.getByRole("heading", { name: /nothing marked yet/i })).toBeInTheDocument();
    expect(await axeViolations(container)).toHaveNoViolations();
  });

  it("has no axe violations with marks, while editing, or with overlays hidden", async () => {
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1, "Hem")], revision: 1 }));
    const { container } = await loaded();
    expect(await axeViolations(container)).toHaveNoViolations();

    fireEvent.click(screen.getByRole("button", { name: /edit note for annotation 1/i }));
    expect(await axeViolations(container)).toHaveNoViolations();

    fireEvent.click(screen.getByRole("button", { name: /clear all/i }));
    expect(await axeViolations(container)).toHaveNoViolations();
    fireEvent.click(screen.getByRole("button", { name: /keep them/i }));

    fireEvent.keyDown(window, { key: "h" });
    expect(await axeViolations(container)).toHaveNoViolations();
  });

  it("has no axe violations on the unsaved-leave popover", async () => {
    const { container } = await loaded();
    fireEvent.click(screen.getByRole("button", { name: /^pin \(p\)$/i }));
    drawOnStage(container);
    fireEvent.click(screen.getByRole("link", { name: /concept/i }));

    expect(await axeViolations(container)).toHaveNoViolations();
  });

  it("traps focus inside the clear-all dialog and restores it on close", async () => {
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1)], revision: 1 }));
    await loaded();
    const trigger = screen.getByRole("button", { name: /clear all/i });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = screen.getByRole("alertdialog");
    // Focus lands on the least destructive action.
    expect(document.activeElement).toHaveAccessibleName("Keep them");

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(trigger);
  });
});
