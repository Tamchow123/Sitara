import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Home from "./page";

const READY_BODY = {
  status: "ok",
  checks: { database: "ok", redis: "ok", auth_cache: "ok", storage: "ok" },
};
const CONFIG_BODY = {
  demo_mode: true,
  generation_enabled: false,
  max_inspiration_images: 3,
  max_refinements: 1,
};

function mockFetchOk() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("/config/public") ? CONFIG_BODY : READY_BODY;
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Sitara foundation page", () => {
  it("shows the loading state first", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<Home />);
    expect(screen.getByText(/checking backend status/i)).toBeInTheDocument();
  });

  it("renders readiness and public config from the backend", async () => {
    mockFetchOk();
    render(<Home />);
    await waitFor(() =>
      expect(screen.getByText("connected")).toBeInTheDocument(),
    );
    expect(screen.getByText("Database")).toBeInTheDocument();
    expect(screen.getByText("Queue (Redis)")).toBeInTheDocument();
    expect(screen.getByText("Authentication protection")).toBeInTheDocument();
    expect(screen.getByText("Private storage")).toBeInTheDocument();
    expect(screen.getAllByText("ok")).toHaveLength(4);
    expect(screen.getByText(/paid generation/i)).toBeInTheDocument();
    expect(screen.getByText("disabled")).toBeInTheDocument();
  });

  it("shows the demo-mode badge when demo mode is on", async () => {
    mockFetchOk();
    render(<Home />);
    const badge = await screen.findByText("Demo mode on");
    expect(badge).toHaveAccessibleName("Demo mode on");
    expect(screen.getByText(/no paid ai calls/i)).toBeInTheDocument();
  });

  it("shows a clear error state when the backend is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    render(<Home />);
    await waitFor(() =>
      expect(screen.getByText(/backend unavailable/i)).toBeInTheDocument(),
    );
  });

  it("times out half-open requests and shows the unavailable state", async () => {
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
    expect(screen.getByText(/checking backend status/i)).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(6000);
    vi.useRealTimers();
    await waitFor(() =>
      expect(screen.getByText(/backend unavailable/i)).toBeInTheDocument(),
    );
    // No stack trace or exception text leaks into the page.
    expect(document.body.textContent).not.toMatch(/AbortError|stack|Error:/);
  });

  it("treats a malformed JSON response as backend unavailable", async () => {
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
      expect(screen.getByText(/backend unavailable/i)).toBeInTheDocument(),
    );
  });

  it("has an accessible heading and a polite status region", async () => {
    mockFetchOk();
    render(<Home />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Sitara" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(
      screen.getByText(/concept visualisation only/i),
    ).toBeInTheDocument();
  });
});
