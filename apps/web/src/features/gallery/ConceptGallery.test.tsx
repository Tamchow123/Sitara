import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetCsrfTokenForTests, type DesignListVersion } from "@/lib/api";
import { axeViolations } from "@/test-utils/axe";

import { ConceptGallery, versionState } from "./ConceptGallery";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const IMAGES_BODY = {
  images: {
    original: {
      url: "https://signed.example/original",
      download_url: "https://signed.example/download",
      width: 800,
      height: 1000,
    },
    thumbnail: { url: "https://signed.example/thumb", width: 320, height: 400 },
    expires_at: "2026-03-04T11:00:00Z",
  },
};

// Taken from the generated contract rather than re-declared, so a change to
// the wire shape fails here instead of quietly leaving these fixtures
// describing a payload the server no longer sends.
type Version = DesignListVersion;

function version(overrides: Partial<Version> = {}): Version {
  return {
    id: `v-${overrides.version_number ?? 1}`,
    version_number: 1,
    is_demo: true,
    has_image: true,
    job_status: "succeeded",
    created_at: "2026-03-04T10:05:00Z",
    ...overrides,
  };
}

function design(overrides: Record<string, unknown> = {}) {
  return {
    id: "d-1",
    // `title` stays empty exactly as the real questionnaire leaves it, so these
    // fixtures cannot pass while the component reads the wrong field.
    title: "",
    display_title: "Ivory lehenga",
    status: "generated",
    created_at: "2026-03-04T10:00:00Z",
    updated_at: "2026-03-04T10:05:00Z",
    is_demo: true,
    version_count: 1,
    versions: [version()],
    ...overrides,
  };
}

/** Serves the list, and every per-card image request. */
function mockList(body: unknown, status = 200, options: { imagesFail?: boolean } = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/images/")) {
      return options.imagesFail
        ? json({ error: { code: "not_found", message: "no" } }, 404)
        : json(IMAGES_BODY);
    }
    return json(body, status);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  _resetCsrfTokenForTests();
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("versionState", () => {
  it("reads ready from a stored image rather than from the job status", () => {
    // has_image is the fact that matters: a job can say succeeded before the
    // picture has landed, and a link offered then goes nowhere.
    expect(versionState(version({ has_image: true, job_status: "running_image" }))).toEqual({
      label: "Ready",
      viewable: true,
    });
    expect(versionState(version({ has_image: false, job_status: "succeeded" }))).toEqual({
      label: "Finishing up",
      viewable: false,
    });
  });

  const UNFINISHED: [DesignListVersion["job_status"], string][] = [
    ["queued", "Still being made"],
    ["running_text", "Still being made"],
    ["running_image", "Still being made"],
    ["failed", "Did not finish"],
  ];

  it.each(UNFINISHED)("labels %s as %s and offers no link", (status, label) => {
    const state = versionState(version({ has_image: false, job_status: status }));
    expect(state).toEqual({ label, viewable: false });
  });

  it("claims nothing when there is no job status at all", () => {
    expect(versionState(version({ has_image: false, job_status: null }))).toEqual({
      label: "No picture available",
      viewable: false,
    });
  });
});

describe("ConceptGallery", () => {
  it("announces that it is loading before anything arrives", async () => {
    mockList({ designs: [], total: 0, limit: 20, offset: 0 });
    render(<ConceptGallery />);
    expect(screen.getByText(/loading your concepts/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText(/loading your concepts/i)).not.toBeInTheDocument(),
    );
  });

  it("offers a route into the questionnaire when there is nothing yet", async () => {
    mockList({ designs: [], total: 0, limit: 20, offset: 0 });
    render(<ConceptGallery />);
    expect(await screen.findByText(/you have not made a concept yet/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /start a design/i })).toHaveAttribute(
      "href",
      "/design/new",
    );
  });

  it("names each card by its own title and dates it", async () => {
    mockList({ designs: [design()], total: 1, limit: 20, offset: 0 });
    render(<ConceptGallery />);
    const heading = await screen.findByRole("heading", { name: "Ivory lehenga" });
    // The card takes its accessible name from that heading, so a reader moving
    // by region knows which concept they are in.
    const card = heading.closest("article");
    expect(card).toHaveAccessibleName("Ivory lehenga");

    // Asserted on the machine-readable attribute, NOT on the rendered string.
    // `madeOn` formats in the visitor's own locale on purpose — a stylist in
    // Dhaka should not be shown a US date order — so the rendered text differs
    // between machines. An earlier version of this test pinned "4 March 2026",
    // which passed under en-GB locally and failed on CI under en-US: a test that
    // asserts a locale is testing the runner, not the component.
    const time = within(card as HTMLElement).getByText(
      (_content, element) => element?.tagName === "TIME",
    );
    expect(time).toHaveAttribute("dateTime", "2026-03-04T10:00:00Z");
    // Something human-readable was rendered, whatever this machine calls it.
    expect(time.textContent?.trim()).not.toBe("");
  });

  it("names a card from display_title, never from the raw title field", async () => {
    // The defect this pins: `Design.title` is blank for every concept made
    // through the questionnaire, so a component reading it renders an empty
    // heading and an alt text of " — version 1". Caught by e2e, not by jsdom,
    // because a fixture that sets `title` hides it.
    mockList({
      designs: [design({ title: "", display_title: "Ivory lehenga" })],
      total: 1,
      limit: 20,
      offset: 0,
    });
    render(<ConceptGallery />);
    const heading = await screen.findByRole("heading", { level: 3 });
    expect(heading).toHaveAccessibleName("Ivory lehenga");
    expect(heading.textContent?.trim()).not.toBe("");
    expect(await screen.findByRole("img")).toHaveAttribute(
      "alt",
      "Ivory lehenga — version 1",
    );
  });

  it("labels demo and live concepts exactly as the result screen does", async () => {
    mockList({
      designs: [design({ id: "d-1", is_demo: true }), design({ id: "d-2", display_title: "Live one", is_demo: false })],
      total: 2,
      limit: 20,
      offset: 0,
    });
    render(<ConceptGallery />);
    expect(await screen.findByText("Demo concept")).toHaveClass("tag", "tag-accent-2");
    expect(screen.getByText("AI-generated concept")).toHaveClass("tag", "tag-accent");
  });

  it("asks the server for concepts only, not for every design", async () => {
    // The screen is "Your concepts". Drafts and failed generations are excluded
    // server-side rather than filtered here, so `total` counts the same set the
    // page shows.
    const fetchMock = mockList({ designs: [design()], total: 1, limit: 20, offset: 0 });
    render(<ConceptGallery />);
    await screen.findByRole("heading", { name: "Ivory lehenga" });
    const listUrl = fetchMock.mock.calls
      .map((call) => String(call[0]))
      .find((url) => !url.includes("/images/"));
    expect(listUrl).toContain("generated=true");
  });

  it("renders a version-less row defensively without inventing a demo or live label", async () => {
    // The gallery's own query cannot return this — a design with no rendered
    // version is excluded server-side. Kept as a guard because the server is the
    // only thing enforcing that, and the wire type still permits is_demo: null.
    // A card with no picture is a better failure than a crash.
    mockList({
      designs: [design({ is_demo: null, version_count: 0, versions: [] })],
      total: 1,
      limit: 20,
      offset: 0,
    });
    render(<ConceptGallery />);
    await screen.findByRole("heading", { name: "Ivory lehenga" });
    expect(screen.queryByText("Demo concept")).not.toBeInTheDocument();
    expect(screen.queryByText("AI-generated concept")).not.toBeInTheDocument();
    // No draft affordances: resuming an unfinished questionnaire is not what a
    // concepts gallery is for, so the card carries no "Continue" route.
    expect(screen.queryByRole("link", { name: /continue/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/no concept yet/i)).not.toBeInTheDocument();
  });

  it("links a ready version to its concept and its annotation workspace", async () => {
    mockList({ designs: [design()], total: 1, limit: 20, offset: 0 });
    render(<ConceptGallery />);
    const view = await screen.findByRole("link", { name: /view ivory lehenga, version 1/i });
    expect(view).toHaveAttribute("href", "/design/d-1/result/v-1");
    expect(
      screen.getByRole("link", { name: /annotate ivory lehenga, version 1/i }),
    ).toHaveAttribute("href", "/design/d-1/result/v-1/annotate");
  });

  it("groups versions in order and puts the version number in every link's name", async () => {
    // Two versions of one design: the original and the refinement made from
    // it. Grouped inside one card so they read as the same piece of work, and
    // each link still self-identifies for a reader who cannot see the grouping.
    mockList({
      designs: [
        design({
          version_count: 2,
          versions: [
            version({ version_number: 1, id: "v-1" }),
            version({ version_number: 2, id: "v-2" }),
          ],
        }),
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });
    render(<ConceptGallery />);
    await screen.findByRole("heading", { name: "Ivory lehenga" });
    const rows = screen.getAllByRole("listitem").filter((li) => li.className === "gallery-version");
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("Version 1"),
      expect.stringContaining("Version 2"),
    ]);
    expect(
      screen.getByRole("link", { name: /view ivory lehenga, version 2/i }),
    ).toHaveAttribute("href", "/design/d-1/result/v-2");
  });

  it("shows a generating refinement by state while still showing the concept that exists", async () => {
    mockList({
      designs: [
        design({
          version_count: 2,
          versions: [
            version({ version_number: 1, id: "v-1", has_image: true }),
            version({ version_number: 2, id: "v-2", has_image: false, job_status: "running_image" }),
          ],
        }),
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });
    render(<ConceptGallery />);
    expect(await screen.findByText("Still being made")).toBeInTheDocument();
    // Version 2 offers no link, because there is nothing to open.
    expect(
      screen.queryByRole("link", { name: /view ivory lehenga, version 2/i }),
    ).not.toBeInTheDocument();
    // The cover falls back to version 1, so an in-flight refinement does not
    // blank out the concept the stylist already has.
    const image = await screen.findByRole("img");
    expect(image).toHaveAttribute("alt", "Ivory lehenga — version 1");
  });

  it("shows a failed version as failed without dropping it from the list", async () => {
    mockList({
      designs: [
        design({ versions: [version({ has_image: false, job_status: "failed" })] }),
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });
    render(<ConceptGallery />);
    expect(await screen.findByText("Did not finish")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ivory lehenga" })).toBeInTheDocument();
  });

  it("mints each card's thumbnail through the ownership-checked images endpoint", async () => {
    const fetchMock = mockList({ designs: [design()], total: 1, limit: 20, offset: 0 });
    render(<ConceptGallery />);
    await waitFor(() => expect(screen.getByRole("img")).toBeInTheDocument());
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls).toContain("/api/v1/designs/d-1/versions/v-1/images/");
    // The list itself must never be asked to carry a signed URL.
    expect(urls).toContain("/api/v1/designs/?limit=20&generated=true");
  });

  it("keeps a card usable when its thumbnail cannot be loaded", async () => {
    mockList({ designs: [design()], total: 1, limit: 20, offset: 0 }, 200, { imagesFail: true });
    render(<ConceptGallery />);
    expect(await screen.findByText(/preview unavailable/i)).toBeInTheDocument();
    // Everything that makes the card useful is still there.
    expect(screen.getByRole("heading", { name: "Ivory lehenga" })).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /view ivory lehenga, version 1/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("falls back to the placeholder when a signed URL expires between fetch and decode", async () => {
    mockList({ designs: [design()], total: 1, limit: 20, offset: 0 });
    render(<ConceptGallery />);
    const image = await screen.findByRole("img");
    fireEvent.error(image);
    expect(await screen.findByText(/preview unavailable/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ivory lehenga" })).toBeInTheDocument();
  });

  it("says the list failed rather than showing an empty gallery", async () => {
    // The dangerous failure mode: a 500 rendered as "you have no concepts".
    mockList({ error: { code: "unavailable", message: "down" } }, 503);
    render(<ConceptGallery />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not be loaded/i);
    expect(screen.queryByText(/you have not made a concept yet/i)).not.toBeInTheDocument();
  });

  it("retries the list from the error state", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/images/")) return json(IMAGES_BODY);
        calls += 1;
        return calls === 1
          ? json({ error: { code: "unavailable", message: "down" } }, 503)
          : json({ designs: [design()], total: 1, limit: 20, offset: 0 });
      }),
    );
    render(<ConceptGallery />);
    fireEvent.click(await screen.findByRole("button", { name: /try again/i }));
    expect(await screen.findByRole("heading", { name: "Ivory lehenga" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("treats a malformed list body as a failure, not as an empty gallery", async () => {
    mockList({ designs: [{ id: "d-1" }], total: 1, limit: 20, offset: 0 });
    render(<ConceptGallery />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be loaded/i);
  });

  // Each of these bodies is valid in every respect EXCEPT the one field named,
  // which is what the previous test cannot prove: a body missing nearly
  // everything fails for many reasons at once, so it would still pass if the
  // guard stopped checking any single field. A row that reached the screen with
  // `display_title` absent would render the literal string "undefined" as a
  // heading, as alt text, and inside every link's accessible name.
  it.each([
    ["display_title", { display_title: undefined }],
    ["title", { title: undefined }],
    ["is_demo", { is_demo: "yes" }],
    ["version_count", { version_count: "1" }],
    ["versions", { versions: "not an array" }],
  ])("refuses a list row whose %s is missing or the wrong type", async (_field, broken) => {
    const row: Record<string, unknown> = { ...design(), ...broken };
    if (broken && Object.values(broken)[0] === undefined) {
      delete row[Object.keys(broken)[0]];
    }
    mockList({ designs: [row], total: 1, limit: 20, offset: 0 });
    render(<ConceptGallery />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be loaded/i);
    expect(screen.queryByRole("heading", { level: 3 })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("undefined");
  });

  it("refuses a list row whose version is the wrong shape", async () => {
    mockList({
      designs: [design({ versions: [{ ...version(), has_image: "yes" }] })],
      total: 1,
      limit: 20,
      offset: 0,
    });
    render(<ConceptGallery />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be loaded/i);
  });

  it("states how many of the total are shown when there are more", async () => {
    mockList({ designs: [design()], total: 34, limit: 20, offset: 0 });
    render(<ConceptGallery />);
    expect(await screen.findByText(/showing your 1 most recent concepts of 34/i)).toBeInTheDocument();
  });

  it("says nothing about a total when everything is on screen", async () => {
    mockList({ designs: [design()], total: 1, limit: 20, offset: 0 });
    render(<ConceptGallery />);
    await screen.findByRole("heading", { name: "Ivory lehenga" });
    expect(screen.queryByText(/showing your/i)).not.toBeInTheDocument();
  });

  it("has no axe violations with cards on screen", async () => {
    mockList({
      designs: [
        design({
          version_count: 2,
          versions: [
            version({ version_number: 1, id: "v-1" }),
            version({ version_number: 2, id: "v-2", has_image: false, job_status: "failed" }),
          ],
        }),
        design({
          id: "d-2",
          display_title: "Nothing made yet",
          is_demo: null,
          version_count: 0,
          versions: [],
        }),
      ],
      total: 2,
      limit: 20,
      offset: 0,
    });
    const { container } = render(<ConceptGallery />);
    await screen.findByRole("heading", { name: "Ivory lehenga" });
    expect(await axeViolations(container)).toHaveNoViolations();
  });

  it("has no axe violations in the empty and failed states", async () => {
    mockList({ designs: [], total: 0, limit: 20, offset: 0 });
    const { container, unmount } = render(<ConceptGallery />);
    await screen.findByText(/you have not made a concept yet/i);
    expect(await axeViolations(container)).toHaveNoViolations();
    unmount();

    mockList({ error: { code: "unavailable", message: "down" } }, 503);
    const failed = render(<ConceptGallery />);
    await screen.findByRole("alert");
    expect(await axeViolations(failed.container)).toHaveNoViolations();
  });

  it("keeps the gallery out of browser storage", async () => {
    mockList({ designs: [design()], total: 1, limit: 20, offset: 0 });
    render(<ConceptGallery />);
    await screen.findByRole("img");
    // The thumbnail URL is a temporary bearer URL; nothing about this screen
    // may outlive the tab.
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});
