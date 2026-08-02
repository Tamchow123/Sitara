import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Home from "./page";
import heroes from "./landing-hero.json";
import { axeViolations } from "@/test-utils/axe";

// The landing page is the one screen a bride sees before she trusts us with
// anything, so these tests are as much about what is NOT on it — infrastructure
// readiness, a diagnostics panel, an unexplained icon — as about what is.

const CONFIG_DEMO = {
  demo_mode: true,
  generation_enabled: false,
  max_inspiration_images: 3,
  max_refinements: 1,
};
const CONFIG_LIVE = { ...CONFIG_DEMO, demo_mode: false, generation_enabled: true };

function mockConfig(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    ),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("landing page", () => {
  it("leads with the handoff's headline and the primary call to action", async () => {
    mockConfig(CONFIG_DEMO);
    render(<Home />);

    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("Your bridal vision, sketched by Sitara");
    expect(screen.getByText(/ai-assisted bridalwear concepts/i)).toBeInTheDocument();

    // Two ways in, top and bottom, both to the questionnaire.
    const starts = screen.getAllByRole("link", { name: "Start designing" });
    expect(starts).toHaveLength(2);
    for (const link of starts) expect(link).toHaveAttribute("href", "/design/new");
    expect(screen.getByText(/about 5 minutes/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("status")).not.toHaveTextContent(/checking/i));
  });

  it("shows the brand lockup as a labelled Home link and offers a skip link", () => {
    mockConfig(CONFIG_DEMO);
    render(<Home />);
    const home = screen.getByRole("link", { name: "Sitara — Home" });
    expect(home).toHaveAttribute("href", "/");
    // Not an unexplained icon: the wordmark is real text inside the link.
    expect(within(home).getByText("Sitara")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /skip to main content/i })).toHaveAttribute(
      "href",
      "#main-content",
    );
  });

  it("renders both hero photographs with the alt text the build script recorded", () => {
    mockConfig(CONFIG_DEMO);
    render(<Home />);
    for (const hero of [heroes["hero-1"], heroes["hero-2"]]) {
      const image = screen.getByAltText(hero.alt);
      expect(image).toHaveAttribute("src", hero.path);
      // Intrinsic size is declared so the hero cannot shift the page as it
      // loads, and the hero is never lazy — it is above the fold everywhere.
      expect(image).toHaveAttribute("width", String(hero.width));
      expect(image).toHaveAttribute("height", String(hero.height));
      expect(image).toHaveAttribute("loading", "eager");
    }
  });

  it("makes an intentional alt-text decision: described, never empty, never the filename", () => {
    mockConfig(CONFIG_DEMO);
    render(<Home />);
    for (const hero of [heroes["hero-1"], heroes["hero-2"]]) {
      expect(hero.alt.length).toBeGreaterThan(20);
      expect(hero.alt).not.toMatch(/\.webp|image-\d+|hero-\d/i);
    }
  });

  it("carries no infrastructure readiness detail whatsoever", async () => {
    mockConfig(CONFIG_DEMO);
    render(<Home />);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/demo mode/i));
    // The old page reported these to the customer. Acceptance criterion 3.
    for (const leak of [
      /database/i,
      /redis/i,
      /queue/i,
      /private storage/i,
      /authentication protection/i,
      /backend connection/i,
      /platform status/i,
    ]) {
      expect(document.body.textContent).not.toMatch(leak);
    }
  });

  it("discloses demo mode honestly, without calling it a fresh render", async () => {
    mockConfig(CONFIG_DEMO);
    render(<Home />);
    const status = screen.getByRole("status");
    await waitFor(() => expect(status).toHaveTextContent(/demo mode/i));
    expect(status).toHaveTextContent(/pre-generated/i);
    expect(status).toHaveTextContent(/no AI service is paid to run/i);
  });

  it("says concepts are generated for you when demo mode is off", async () => {
    mockConfig(CONFIG_LIVE);
    render(<Home />);
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/generated for your answers/i),
    );
  });

  it("states the concept-visualisation limit in plain words", () => {
    mockConfig(CONFIG_DEMO);
    render(<Home />);
    const notes = screen.getByText(/not a sewing pattern/i);
    expect(notes).toHaveTextContent(/concept visualisation/i);
    expect(notes).toHaveTextContent(/not a promise that a garment can be constructed/i);
  });

  it("keeps the page usable when the config read fails, in one bounded line", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    render(<Home />);
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/could not confirm/i),
    );
    // Degraded, not broken: the way in still works, and no exception text or
    // stack leaks into the page.
    expect(screen.getAllByRole("link", { name: "Start designing" })).toHaveLength(2);
    expect(document.body.textContent).not.toMatch(/TypeError|stack|Error:/);
  });

  it("treats a malformed response the same as an unreachable one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response("<html>proxy error</html>", {
            status: 200,
            headers: { "Content-Type": "text/html" },
          }),
      ),
    );
    render(<Home />);
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/could not confirm/i),
    );
  });

  it("times out a half-open request rather than waiting forever", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("The operation was aborted.", "AbortError")),
            );
          }),
      ),
    );
    render(<Home />);
    expect(screen.getByRole("status")).toHaveTextContent(/checking how concepts/i);
    await vi.advanceTimersByTimeAsync(6000);
    vi.useRealTimers();
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/could not confirm/i),
    );
    expect(document.body.textContent).not.toMatch(/AbortError|stack|Error:/);
  });

  it("has exactly one h1 and a sensible heading order beneath it", () => {
    mockConfig(CONFIG_DEMO);
    render(<Home />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    const h2s = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(h2s).toEqual(["How it works", "Ready to see your vision take shape?", "Before you start"]);
  });

  it("has no axe violations once the public config has resolved", async () => {
    mockConfig(CONFIG_DEMO);
    const { container } = render(<Home />);
    await waitFor(() => expect(screen.getByRole("status")).not.toHaveTextContent(/checking/i));
    expect(await axeViolations(container)).toHaveNoViolations();
  });

  it("has no axe violations in the bounded config-unavailable state", async () => {
    // The state a bride actually hits when the backend is down, so it is worth
    // asserting separately: a notice that appears late must not arrive with a
    // broken label or an orphaned live region.
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));
    const { container } = render(<Home />);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/could not confirm/i));
    expect(await axeViolations(container)).toHaveNoViolations();
  });
});
