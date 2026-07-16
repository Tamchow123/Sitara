import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Home from "./page";

const READY_BODY = {
  status: "ok",
  checks: { database: "ok", redis: "ok", storage: "ok" },
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
    expect(screen.getByText("Private storage")).toBeInTheDocument();
    expect(screen.getAllByText("ok")).toHaveLength(3);
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
