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
  fetchRenderSendState: vi.fn(),
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
function pointerEventAt(
  target: Element,
  type: string,
  clientX: number,
  clientY: number,
  // 1 = a button is held, which is what a real browser reports throughout a mouse
  // drag and throughout touch or pen contact. The canvas uses it to tell a
  // continuing drag from a stray hover over a drag that was never closed off, so
  // a test that left it at 0 would exercise the abort path instead of the drag.
  buttons = 1,
) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "clientX", { value: clientX });
  Object.defineProperty(event, "clientY", { value: clientY });
  Object.defineProperty(event, "pointerId", { value: 1 });
  Object.defineProperty(event, "buttons", { value: buttons });
  fireEvent(target, event);
}

function pressAt(stage: HTMLElement, clientX: number, clientY: number) {
  pointerEventAt(stage, "pointerdown", clientX, clientY);
  pointerEventAt(stage, "pointerup", clientX, clientY);
}

/**
 * Give the stage a size, since jsdom performs no layout.
 *
 * Both the bounding rect AND `offsetWidth`/`offsetHeight` are stubbed, because
 * they are read for different jobs: the rect converts a pointer position into
 * normalised space, while the offsets are the UNSCALED size a pan limit is
 * computed from. A resize is then fired so the component re-measures against the
 * stub — without it the stage stays 0x0 as far as the pan is concerned, every
 * offset clamps to zero, and a pan test passes while asserting nothing.
 *
 * A LIMIT worth stating rather than discovering later: they are stubbed to the
 * SAME numbers, but in a real browser they deliberately diverge under zoom —
 * `getBoundingClientRect()` reports the TRANSFORMED box (so it scales with the
 * zoom and shifts with the pan) while `offsetWidth`/`offsetHeight` stay the
 * layout size. That divergence is exactly what makes pointer conversion work
 * unchanged under a transform, and jsdom applies no transform, so NO test in this
 * file exercises it. It was instead verified by measurement in a real browser: at
 * zoom 1.5625 with a 120px pan, a mark's on-screen position matched its stored
 * normalised coordinate multiplied through the transformed rect to the pixel.
 * `clampPan` takes the unscaled size as an explicit argument for the same reason —
 * so the one calculation that must NOT use the transformed box cannot silently
 * start doing so.
 */
function stubStageBox(stage: HTMLElement, width = 500, height = 1000) {
  stage.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width, height, right: width, bottom: height }) as DOMRect;
  Object.defineProperty(stage, "offsetWidth", { value: width, configurable: true });
  Object.defineProperty(stage, "offsetHeight", { value: height, configurable: true });
  fireEvent(window, new Event("resize"));
}

/** Press on `target`, move across the stage, release. One continuous gesture. */
function dragFrom(
  target: Element,
  stage: HTMLElement,
  from: { x: number; y: number },
  ...through: Array<{ x: number; y: number }>
) {
  pointerEventAt(target, "pointerdown", from.x, from.y);
  for (const step of through) pointerEventAt(stage, "pointermove", step.x, step.y);
  const last = through[through.length - 1] ?? from;
  // The button is already released by the time `pointerup` is delivered.
  pointerEventAt(stage, "pointerup", last.x, last.y, 0);
}

/** Where the list says the first mark is, e.g. "50% across". */
function reportedPosition() {
  return screen.getByText(/across/).textContent ?? "";
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
  api.fetchRenderSendState.mockResolvedValue({
    used: 0,
    limit: 3,
    suggestedFilename: "Ivory lehenga",
  });
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

    // The DELETE WAITS for the open PUT instead of racing it. The two are
    // independent requests and the server may handle them in either order; if the
    // DELETE went first it would remove nothing and the PUT would then write the
    // marks back, so overlapping them at all is the defect.
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.clearAnnotations).not.toHaveBeenCalled();

    // Now the save lands, and only then does the clear go out.
    await act(async () => {
      release?.();
    });
    await waitFor(() => expect(api.clearAnnotations).toHaveBeenCalledTimes(1));
    // The late confirmation names a revision the clear has retired, so applying it
    // would put the marks back on a document the server says is empty.
    expect(await screen.findByRole("heading", { name: /annotations · 0/i })).toBeInTheDocument();
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

    // The DELETE goes out despite the revision still reading 0 — but only once the
    // creating PUT has landed, so the server cannot end up handling the DELETE
    // first (deleting nothing) and then accepting that PUT's `expected_revision:
    // 0` as a legitimate first-ever save of the marks just cleared.
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.clearAnnotations).not.toHaveBeenCalled();

    await act(async () => {
      release?.();
    });
    await waitFor(() => expect(api.clearAnnotations).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("heading", { name: /annotations · 0/i })).toBeInTheDocument();
  });

  it("never has a save and a clear in flight at the same time", async () => {
    // The invariant behind both tests above, asserted directly on the request
    // ordering rather than on its symptoms. Two concurrent writes to one document
    // is the whole defect: whichever order the server picks, one of them is
    // operating on a document state its caller never saw.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1)], revision: 2 }));
    const order: string[] = [];
    let release: (() => void) | null = null;
    api.saveAnnotations.mockImplementationOnce(
      (_d: string, _v: string, body: { expected_revision: number; items: AnnotationItem[] }) => {
        order.push("save:start");
        return new Promise((resolve) => {
          release = () => {
            order.push("save:end");
            resolve({
              ok: true,
              document: {
                ...emptyDocument(),
                items: body.items,
                revision: body.expected_revision + 1,
              },
            });
          };
        });
      },
    );
    api.clearAnnotations.mockImplementation(async () => {
      order.push("clear:start");
      return { ok: true };
    });
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
    await act(async () => {
      release?.();
    });
    await waitFor(() => expect(api.clearAnnotations).toHaveBeenCalledTimes(1));

    // The clear starts only after the save has finished — never between its start
    // and its end.
    expect(order).toEqual(["save:start", "save:end", "clear:start"]);
  });

  it("does not report a conflict caused by the user's own clear", async () => {
    // A save that 409s because the clear removed the row underneath it describes a
    // write the clear made irrelevant. Showing "Newer notes elsewhere" there blames
    // another session for the user's own completed action.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1)], revision: 3 }));
    let release: (() => void) | null = null;
    api.saveAnnotations.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () =>
            resolve({ ok: false, kind: "conflict", message: "Newer notes elsewhere." });
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
    await act(async () => {
      release?.();
    });
    await waitFor(() => expect(api.clearAnnotations).toHaveBeenCalledTimes(1));

    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(screen.queryByText(/newer notes elsewhere/i)).not.toBeInTheDocument();
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
  /** Open the naming prompt. Since Phase 21 the press is never the send. */
  async function openPrompt() {
    fireEvent.click(screen.getByRole("button", { name: /send to account/i }));
    return screen.findByRole("dialog", { name: /name this file/i });
  }

  const nameField = () => screen.getByLabelText(/file name/i);
  const confirm = () => screen.getByRole("button", { name: /send to my email/i });

  /** The whole two-step gesture: open, optionally retype, confirm. */
  async function sendWithName(typed?: string) {
    await openPrompt();
    if (typed !== undefined) fireEvent.change(nameField(), { target: { value: typed } });
    fireEvent.click(confirm());
  }

  it("flashes a confirmation naming the account's own address", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await loaded();
    await sendWithName();

    await waitFor(() =>
      expect(api.sendRenderToAccount).toHaveBeenCalledWith(
        "design-1",
        "version-1",
        "annotated",
        "Ivory lehenga",
      ),
    );
    expect(await screen.findByText(/sent to your email/i)).toBeInTheDocument();
    expect(
      screen
        .getAllByRole("status")
        .map((node) => node.textContent)
        .join(" "),
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
    await sendWithName();
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
    await sendWithName();

    expect(await screen.findByText(/sent this many concepts for now/i)).toBeInTheDocument();
  });

  it("disables the action for an anonymous owner and says to sign in", async () => {
    auth.user = null;
    await loaded();

    expect(screen.getByRole("button", { name: /send to account/i })).toBeDisabled();
    expect(screen.getByText(/sign in to send this to your email/i)).toBeInTheDocument();
    expect(api.sendRenderToAccount).not.toHaveBeenCalled();
    // Nothing to read either: an account-less visitor has no allowance to show, and
    // asking would be a request that can only 409.
    expect(api.fetchRenderSendState).not.toHaveBeenCalled();
  });

  it("reports a closed capability gate honestly", async () => {
    api.sendRenderToAccount.mockResolvedValue({
      ok: false,
      kind: "disabled",
      message: "Emailing your concept is not available at the moment.",
    });
    await loaded();
    await sendWithName();

    expect(await screen.findByText(/not available at the moment/i)).toBeInTheDocument();
  });

  it("warns that sending takes the image out of Sitara", async () => {
    // §8.5 requires this said in the UI before the first send, not buried in a
    // policy page.
    await loaded();
    expect(screen.getByText(/may keep a copy outside Sitara/i)).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Naming the file (Phase 21)
  // -------------------------------------------------------------------------

  it("asks for a name before sending anything", async () => {
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: /send to account/i }));

    expect(await screen.findByRole("dialog", { name: /name this file/i })).toBeInTheDocument();
    // The press opened a prompt. It did NOT send.
    expect(api.sendRenderToAccount).not.toHaveBeenCalled();
  });

  it("pre-fills the field from the server's suggestion and focuses it", async () => {
    await loaded();
    await openPrompt();

    const field = nameField();
    expect(field).toHaveValue("Ivory lehenga");
    expect(field).toHaveFocus();
  });

  it("sends the name the user typed instead of the suggestion", async () => {
    await loaded();
    await sendWithName("Autumn mehndi look");

    await waitFor(() =>
      expect(api.sendRenderToAccount).toHaveBeenCalledWith(
        "design-1",
        "version-1",
        "annotated",
        "Autumn mehndi look",
      ),
    );
  });

  it("bounds the field at the length the attachment will actually keep", async () => {
    await loaded();
    await openPrompt();
    // 60, the server's base cap — not its 200-character abuse ceiling. A field that
    // accepted 200 would let someone type 140 characters they never see again.
    expect(nameField()).toHaveAttribute("maxlength", "60");
  });

  it("says the name goes into the email headers before the user types", async () => {
    // ADR 0021 records this as an ACCEPTED exposure. The wording must never
    // suggest it has been removed or mitigated.
    await loaded();
    const dialog = await openPrompt();

    const hint = within(dialog).getByText(/goes into the email/i);
    expect(hint.textContent).toMatch(/headers/i);
    expect(hint.textContent).toMatch(/your mail provider and your inbox keep it/i);
    expect(hint.textContent).toMatch(/outside sitara/i);
    // The field is described BY it, so it is announced rather than merely present.
    expect(nameField()).toHaveAccessibleDescription(/goes into the email/i);
  });

  it("shows how many sends are left before the ceiling is reached", async () => {
    api.fetchRenderSendState.mockResolvedValue({
      used: 2,
      limit: 3,
      suggestedFilename: "Ivory lehenga",
    });
    await loaded();

    expect(await screen.findByText(/1 of 3 emails left for this image/i)).toBeInTheDocument();
  });

  it("refuses and explains once the allowance is spent, without asking for a name", async () => {
    api.fetchRenderSendState.mockResolvedValue({
      used: 3,
      limit: 3,
      suggestedFilename: "Ivory lehenga",
    });
    await loaded();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /send to account/i })).toBeDisabled(),
    );
    expect(screen.getByText(/emailed this 3 times, which is the maximum/i)).toBeInTheDocument();
    // No prompt, and nothing sent: the ceiling is not a name problem.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.sendRenderToAccount).not.toHaveBeenCalled();
  });

  it("cannot hold the characters that make a refusal likely in the first place", async () => {
    // Worth pinning rather than assuming. A single-line <input> discards CR and LF
    // outright — typed OR pasted — so the header-injection name this field is most
    // often imagined carrying is not expressible here at all. That does not make
    // the server's refusal redundant; it makes it defence in depth, which is the
    // only reason it is safe for this dialog to treat a refused name as a
    // correctable typo rather than an attack.
    await loaded();
    await openPrompt();

    fireEvent.change(nameField(), { target: { value: "Autumn\r\nBcc: attacker@evil.test" } });

    expect((nameField() as HTMLInputElement).value).not.toMatch(/[\r\n]/);
  });

  it("keeps the dialog open and explains when the server refuses the name", async () => {
    // "///" is refused server-side (it sanitises to nothing) AND is typeable here,
    // unlike a CR/LF name — so this exercises the refusal path through a gesture a
    // real stylist could actually make.
    api.sendRenderToAccount.mockResolvedValue({
      ok: false,
      kind: "name_refused",
      message: "This name cannot be used. Please try a simpler name.",
    });
    await loaded();
    await sendWithName("///");

    // Closing would throw away what they wrote and leave them guessing.
    expect(await screen.findByRole("alert")).toHaveTextContent(/cannot be used/i);
    expect(screen.getByRole("dialog", { name: /name this file/i })).toBeInTheDocument();
    expect(nameField()).toHaveValue("///");
    expect(nameField()).toHaveAttribute("aria-invalid", "true");
    // And the hint is still described, so the error did not silence the one thing
    // the user has to know.
    expect(nameField()).toHaveAccessibleDescription(/goes into the email/i);
  });

  it("clears the refusal as soon as the name is edited", async () => {
    api.sendRenderToAccount.mockResolvedValue({
      ok: false,
      kind: "name_refused",
      message: "This name cannot be used. Please try a simpler name.",
    });
    await loaded();
    await sendWithName("///");
    await screen.findByRole("alert");

    fireEvent.change(nameField(), { target: { value: "A better name" } });

    expect(screen.queryByText(/cannot be used/i)).not.toBeInTheDocument();
    expect(nameField()).not.toHaveAttribute("aria-invalid");
  });

  it("cancelling sends nothing and returns focus to the button", async () => {
    await loaded();
    const trigger = screen.getByRole("button", { name: /send to account/i });
    trigger.focus();
    await openPrompt();

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.sendRenderToAccount).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /send to account/i })).toHaveFocus();
  });

  it("Escape closes the prompt without sending", async () => {
    await loaded();
    const dialog = await openPrompt();

    fireEvent.keyDown(dialog, { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.sendRenderToAccount).not.toHaveBeenCalled();
  });

  it("Enter in the field sends rather than doing nothing", async () => {
    await loaded();
    await openPrompt();
    fireEvent.change(nameField(), { target: { value: "Sangeet look" } });

    fireEvent.keyDown(nameField(), { key: "Enter" });

    await waitFor(() =>
      expect(api.sendRenderToAccount).toHaveBeenCalledWith(
        "design-1",
        "version-1",
        "annotated",
        "Sangeet look",
      ),
    );
  });

  it("opens the prompt even when the send state cannot be read", async () => {
    // The suggestion and the count are conveniences. A concept the user can see
    // must not become unsendable because a convenience read failed.
    api.fetchRenderSendState.mockResolvedValue(null);
    await loaded();
    await sendWithName("Named anyway");

    await waitFor(() =>
      expect(api.sendRenderToAccount).toHaveBeenCalledWith(
        "design-1",
        "version-1",
        "annotated",
        "Named anyway",
      ),
    );
  });

  it("takes the suggestion only from the server, with a note in scope on the same screen", async () => {
    // What this DOES prove: the client never derives a name itself. `fetchAnnotations`
    // is mocked with a note, `fetchRenderSendState` with a title, and the field takes
    // the second — so a future client-side fallback that reached for the document
    // would fail here. That is worth guarding on this surface specifically, because
    // this is the one where the note is already loaded and one property access away.
    //
    // What it does NOT prove: that the server never derives a suggestion from a
    // note. It cannot — the server is mocked. That guarantee (CLAUDE.md §7: a note
    // is the most personal free text in the product, and a file name travels in the
    // message headers) is held and tested where it lives, in
    // designs/tests/test_render_send_api.py::test_the_send_state_never_suggests_a_note.
    // Stated plainly rather than left implied, because a test that looks like it
    // covers a privacy rule and does not is worse than no test at all.
    api.fetchAnnotations.mockResolvedValue(
      emptyDocument({ items: [pin(1, "The neckline is far too low")], revision: 1 }),
    );
    await loaded();
    // The note really is on screen, so "the client had it and did not use it" is a
    // claim about an available value rather than an absent one.
    expect(screen.getByText("The neckline is far too low")).toBeInTheDocument();
    const dialog = await openPrompt();

    expect(nameField()).toHaveValue("Ivory lehenga");
    expect(dialog.textContent).not.toMatch(/neckline/i);
  });

  it("writes nothing to browser storage while naming and sending", async () => {
    await loaded();
    await sendWithName("Autumn mehndi look");
    await waitFor(() => expect(api.sendRenderToAccount).toHaveBeenCalled());

    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("has no axe violations with the naming prompt open", async () => {
    const { container } = await loaded();
    await openPrompt();
    expect(await axeViolations(container)).toHaveNoViolations();
  });

  it("has no axe violations with the prompt showing a refused name", async () => {
    api.sendRenderToAccount.mockResolvedValue({
      ok: false,
      kind: "name_refused",
      message: "This name cannot be used. Please try a simpler name.",
    });
    const { container } = await loaded();
    await sendWithName("///");
    await screen.findByRole("alert");

    expect(await axeViolations(container)).toHaveNoViolations();
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

  it("has no axe violations with the help disclosure open", async () => {
    // Scanned OPEN, because that is the state no other scan reaches: axe respects
    // the hidden state of a closed <details>, so the grouped lists, the term and
    // description structure and the key chips inside it were never audited by the
    // scans above. A disclosure that is fine closed and has barriers open is still
    // a barrier.
    const { container } = await loaded();
    fireEvent.click(screen.getByText("Gestures and shortcuts"));
    expect(
      (container.querySelector("details.annotation-help") as HTMLDetailsElement).open,
    ).toBe(true);

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

describe("the gestures and shortcuts help", () => {
  it("stays out of the way until it is asked for", async () => {
    // The point of the redesign: a returning stylist should not pay vertical space
    // to be told what they already know. The reference is present and reachable,
    // but closed.
    const { container } = await loaded();
    const help = container.querySelector("details.annotation-help") as HTMLDetailsElement;

    expect(help).toBeTruthy();
    expect(help.open).toBe(false);
    expect(screen.getByText("Gestures and shortcuts")).toBeVisible();
  });

  it("opens to a grouped reference rather than a paragraph", async () => {
    const { container } = await loaded();
    const help = container.querySelector("details.annotation-help") as HTMLDetailsElement;
    // Native <details>: clicking the summary is what a user does, and jsdom
    // implements the click toggle (it does NOT implement Space or Enter on a
    // summary, so a keyboard test here would be testing jsdom's missing default
    // action rather than this code).
    fireEvent.click(screen.getByText("Gestures and shortcuts"));

    expect(help.open).toBe(true);

    // The exact set of terms, not a count with slack. A floor of 8 against 9 rows
    // tolerated silently losing one — and asserting the terms rather than the
    // number also catches a rename or a reorder, and says in the test what the
    // reference is supposed to contain.
    const terms = Array.from(help.querySelectorAll(".annotation-help-row dt")).map((term) =>
      (term.textContent ?? "").replace(/\s+/g, " ").trim(),
    );
    expect(terms).toEqual([
      "Draw",
      "Move a mark",
      "Pan",
      "V P A R F",
      "Arrow keys",
      "Enter Esc",
      "H",
      "+ − 0",
      "Ctrl+Z",
    ]);
    expect(screen.getByText(/drag it straight from where it sits/i)).toBeVisible();
  });

  it("names the undo modifier the same way the tool rail does", async () => {
    // Both read the same helper, so they cannot disagree — one saying ⌘ while the
    // other said Ctrl on the same machine reads as a bug in the app.
    //
    // Asserted as a LITERAL on both sides rather than by deriving the expected
    // value from the rail. Deriving it was circular: the derivation fell back to
    // "Ctrl" whenever ⌘ was absent, so corrupting the rail's label template to drop
    // the modifier entirely still produced "Ctrl" and still matched. jsdom's
    // userAgent is win32, so "Ctrl" is the correct expectation here; the ⌘ branch
    // is covered in platform.test.ts, which can vary the userAgent.
    await loaded();
    expect(screen.getByRole("button", { name: /^undo/i })).toHaveAttribute(
      "aria-label",
      "Undo (Ctrl+Z)",
    );

    fireEvent.click(screen.getByText("Gestures and shortcuts"));

    const keys = Array.from(document.querySelectorAll(".annotation-help kbd")).map(
      (key) => key.textContent,
    );
    expect(keys).toContain("Ctrl");
    expect(keys).not.toContain("⌘");
  });

  it("no longer carries the permanent instructions paragraph", async () => {
    // The actual request was that this stop taking space on every visit. Without a
    // negative assertion, leaving the old paragraph in place beside the new
    // disclosure would satisfy every other test here while costing the same space
    // it was removed to reclaim.
    const { container } = await loaded();

    expect(container.querySelector(".annotation-instructions")).toBeNull();
    expect(screen.queryByText(/Shift\+arrow moves further/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/H hides and shows every mark/i)).not.toBeInTheDocument();
  });

  it("does not keep the basics only behind the disclosure", async () => {
    // A newcomer must not have to find a collapsed panel to learn the first step.
    // The empty-state card over the render carries it, and disappears once it is
    // no longer true — which is why the help can be closed by default at all.
    await loaded();
    expect(screen.getByText(/pick a tool on the left, then click anywhere/i)).toBeVisible();
  });
});

describe("moving a mark with the pointer", () => {
  // The stage is stubbed 500x1000, so a mark at the normalised centre sits at
  // client (250, 500) and 100px of travel is 0.2 across.
  const withOnePin = async () => {
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1)], revision: 1 }));
    const view = await loaded();
    const stage = view.container.querySelector(".annotation-stage") as HTMLElement;
    stubStageBox(stage);
    return { ...view, stage, mark: view.container.querySelector(".mark") as Element };
  };

  it("moves a mark in a single gesture, with no click to select it first", async () => {
    // THE point of this change. It used to take two separate gestures: a click to
    // select, then a drag — and the drag had to start on empty canvas rather than
    // on the mark, which is the opposite of what the cursor implied.
    const { stage, mark } = await withOnePin();
    expect(reportedPosition()).toMatch(/50% across/);

    dragFrom(mark, stage, { x: 250, y: 500 }, { x: 350, y: 500 });

    expect(reportedPosition()).toMatch(/70% across/);
  });

  it("selects without moving when the press barely travels", async () => {
    // A click carries a pixel or two of hand tremor. Acting on it would mean
    // opening a mark to read its note silently edited the document and wrote it
    // back to the server.
    //
    // Asserted on whether a SAVE happened, not on the reported position: a 1px
    // move on a 500px stage is 0.002 normalised, which still rounds to "50%
    // across". The first version of this test checked the position and passed with
    // the threshold removed entirely — it could not see the bug it existed to
    // catch. Dirtiness is the signal that actually distinguishes the two.
    const { stage, mark } = await withOnePin();
    vi.useFakeTimers({ shouldAdvanceTime: true });

    dragFrom(mark, stage, { x: 250, y: 500 }, { x: 251, y: 501 });
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });

    expect(api.saveAnnotations).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /annotation 1, pin/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("does not move the selected mark when the press lands on empty canvas", async () => {
    // The old behaviour: with a mark selected, a press ANYWHERE on the canvas
    // dragged it — usually from somewhere nowhere near the pointer, and there was
    // then no way to deselect by clicking away. Empty space now means "deselect".
    const { stage, mark } = await withOnePin();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    dragFrom(mark, stage, { x: 250, y: 500 });
    expect(screen.getByRole("button", { name: /annotation 1, pin/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    dragFrom(stage, stage, { x: 40, y: 900 }, { x: 140, y: 900 });
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });

    // Both axes exactly, plus the save guard — a stray move of a pixel or two
    // still renders as "50% across", so the write is the assertion that cannot be
    // fooled by rounding.
    expect(reportedPosition()).toBe("centred at 50% across, 50% from the top");
    expect(api.saveAnnotations).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /annotation 1, pin/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("takes one undo to put a dragged mark back, not one per pointermove", async () => {
    const { stage, mark } = await withOnePin();

    dragFrom(
      mark,
      stage,
      { x: 250, y: 500 },
      { x: 280, y: 500 },
      { x: 310, y: 500 },
      { x: 350, y: 500 },
    );
    expect(reportedPosition()).toMatch(/70% across/);

    fireEvent.keyDown(document.body, { key: "z", ctrlKey: true });

    expect(reportedPosition()).toMatch(/50% across/);
  });

  it("keeps moving a mark after the pointer leaves it", async () => {
    // A pin is a few pixels wide, so the pointer is off it almost immediately. The
    // moves are dispatched at the STAGE here, which is what pointer capture
    // delivers in a real browser — if the drag only tracked events on the mark
    // itself it would stop the instant it started working.
    const { stage, mark } = await withOnePin();

    dragFrom(mark, stage, { x: 250, y: 500 }, { x: 450, y: 900 });

    expect(reportedPosition()).toMatch(/90% across/);
    expect(reportedPosition()).toMatch(/90% from the top/);
  });

  it("still drags when pointer capture is refused", async () => {
    // `setPointerCapture` throws NotFoundError for a pointer id the browser does
    // not consider active. Unwrapped, that exception escaped the React event
    // handler. Contained, it must also not cost the gesture: capture only helps a
    // drag SURVIVE leaving the stage, so dragging has to work without it — as it
    // does in jsdom, which has no such method at all.
    const { stage, mark } = await withOnePin();
    (stage as HTMLElement & { setPointerCapture: (id: number) => void }).setPointerCapture =
      () => {
        throw new DOMException("no such pointer", "NotFoundError");
      };

    dragFrom(mark, stage, { x: 250, y: 500 }, { x: 350, y: 500 });

    expect(reportedPosition()).toMatch(/70% across/);
  });

  it("clears a drag left armed by an earlier gesture on the next press", async () => {
    // The other way back from a drag that never received its `pointerup`: a fresh
    // press owns the drag state, so a stale one cannot make the new press move a
    // mark that is nowhere near it.
    const { stage, mark } = await withOnePin();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    pointerEventAt(mark, "pointerdown", 250, 500);
    // Abandoned with no pointerup, as a release over the sidebar would be.

    // A new press on empty canvas, well away from the mark, then a drag.
    dragFrom(stage, stage, { x: 60, y: 900 }, { x: 200, y: 900 });
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });

    // Both axes, and — the assertion that has no blind spot — that nothing was
    // saved. A position match alone tolerates a stray move of a pixel or two,
    // because 0.002 still rounds to "50%"; any movement at all dirties the
    // document and would show up here as a write.
    expect(reportedPosition()).toBe("centred at 50% across, 50% from the top");
    expect(api.saveAnnotations).not.toHaveBeenCalled();
  });

  it("does not treat a hover as a drag when nothing is held", async () => {
    // The failure this prevents: a release that lands outside the stage leaves the
    // drag armed, and because an armed drag is acted on before anything else, every
    // later movement over the canvas moved the mark and saved it — marks wandering
    // under the cursor with no gesture behind them.
    const { stage, mark } = await withOnePin();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    pointerEventAt(mark, "pointerdown", 250, 500);
    // No pointerup: the gesture is abandoned mid-flight, as it would be by a
    // release over the sidebar.

    pointerEventAt(stage, "pointermove", 400, 700, 0);
    pointerEventAt(stage, "pointermove", 450, 750, 0);
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });

    // As above: the save assertion is the one that a sub-percent stray move
    // cannot slip past.
    expect(reportedPosition()).toBe("centred at 50% across, 50% from the top");
    expect(api.saveAnnotations).not.toHaveBeenCalled();
  });

  it("disarms a drag that the browser cancels", async () => {
    const { stage, mark } = await withOnePin();
    pointerEventAt(mark, "pointerdown", 250, 500);
    pointerEventAt(stage, "pointermove", 300, 500);
    pointerEventAt(stage, "pointercancel", 300, 500, 0);
    const atCancel = reportedPosition();

    // Moves after the cancel belong to nobody.
    pointerEventAt(stage, "pointermove", 450, 500);

    expect(reportedPosition()).toBe(atCancel);
  });

  it("does not move marks with a drawing tool active", async () => {
    // With a drawing tool a press on a mark still only selects it, which is what it
    // did before. Dragging marks belongs to Select.
    const { stage, mark } = await withOnePin();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(screen.getByRole("button", { name: /^pin \(P\)$/i }));

    dragFrom(mark, stage, { x: 250, y: 500 }, { x: 350, y: 500 });
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });

    // As above: exact on both axes, and no write. This also pins that pressing an
    // existing mark with a drawing tool does not CREATE a second mark on top of it.
    expect(reportedPosition()).toBe("centred at 50% across, 50% from the top");
    expect(api.saveAnnotations).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: /annotations · 1/i })).toBeInTheDocument();
  });
});

describe("panning a zoomed render", () => {
  const zoomedIn = async () => {
    const view = await loaded();
    const stage = view.container.querySelector(".annotation-stage") as HTMLElement;
    stubStageBox(stage);
    // 1.25x hides (1.25 - 1) * 500 / 2 = 62.5px of width on each side, and 125px
    // of height.
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    return { ...view, stage };
  };

  it("cannot be panned at all while the render is fitted", async () => {
    // Nothing is hidden at 1x, so an offset could only push the concept out of its
    // own frame.
    const { container } = await loaded();
    const stage = container.querySelector(".annotation-stage") as HTMLElement;
    stubStageBox(stage);

    dragFrom(stage, stage, { x: 100, y: 100 }, { x: 300, y: 300 });

    expect(stage.style.transform).toBe("");
  });

  it("pans by dragging the background once zoomed in", async () => {
    const { stage } = await zoomedIn();

    dragFrom(stage, stage, { x: 100, y: 100 }, { x: 140, y: 130 });

    expect(stage.style.transform).toBe("translate(40px, 30px) scale(1.25)");
  });

  it("holds the pan at the edge of the region the zoom hid", async () => {
    // Unclamped, a flick would send the concept off-frame with nothing on screen
    // explaining where it went.
    const { stage } = await zoomedIn();

    dragFrom(stage, stage, { x: 100, y: 100 }, { x: 9000, y: 9000 });

    expect(stage.style.transform).toBe("translate(62.5px, 125px) scale(1.25)");
  });

  it("returns the render to the centre when it is fitted again", async () => {
    const { stage } = await zoomedIn();
    dragFrom(stage, stage, { x: 100, y: 100 }, { x: 150, y: 150 });
    expect(stage.style.transform).toContain("translate(50px, 50px)");

    fireEvent.click(screen.getByRole("button", { name: "Fit to view" }));

    // Not merely back to 1x: an offset left behind would hold the render
    // off-centre in a view with no hidden region to justify it.
    expect(stage.style.transform).toBe("");
  });

  it("re-centres what a zoom-out no longer hides", async () => {
    // Panned to the limit at 1.25x, then zoomed out. The old limit is larger than
    // the new one, so the offset has to shrink with it.
    const { stage } = await zoomedIn();
    dragFrom(stage, stage, { x: 0, y: 0 }, { x: 9000, y: 0 });
    expect(stage.style.transform).toContain("translate(62.5px");

    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));

    expect(stage.style.transform).toBe("");
  });

  it("pans with the arrow keys when no mark is selected", async () => {
    // A zoom that can only be explored by dragging is not keyboard-operable.
    const { stage } = await zoomedIn();

    fireEvent.keyDown(document.body, { key: "ArrowRight" });

    // The viewport travels right, which slides the image left under it.
    expect(stage.style.transform).toBe("translate(-40px, 0px) scale(1.25)");

    fireEvent.keyDown(document.body, { key: "ArrowLeft", shiftKey: true });
    expect(stage.style.transform).toBe("translate(62.5px, 0px) scale(1.25)");
  });

  it("leaves the arrow keys to the selected mark rather than panning", async () => {
    // With a mark selected the arrows belong to that mark. Panning instead would
    // move the wrong thing, with no way to tell which was going to happen.
    api.fetchAnnotations.mockResolvedValue(emptyDocument({ items: [pin(1)], revision: 1 }));
    const { container } = await loaded();
    const stage = container.querySelector(".annotation-stage") as HTMLElement;
    stubStageBox(stage);
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    dragFrom(container.querySelector(".mark") as Element, stage, { x: 250, y: 500 });

    fireEvent.keyDown(document.body, { key: "ArrowRight" });

    expect(stage.style.transform).toBe("translate(0px, 0px) scale(1.25)");
  });
});
