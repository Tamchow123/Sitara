import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DemoBanner } from "./DemoBanner";

const mocks = vi.hoisted(() => ({
  fetchPublicConfig: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchPublicConfig: mocks.fetchPublicConfig,
  };
});

function renderBanner() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <DemoBanner />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DemoBanner", () => {
  it("shows the demo banner when generation_mode is demo", async () => {
    mocks.fetchPublicConfig.mockResolvedValue({
      demo_mode: true,
      generation_enabled: false,
      generation_mode: "demo",
      max_inspiration_images: 3,
      max_refinements: 1,
    });
    renderBanner();
    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent(/demo mode/i);
    expect(banner).toHaveTextContent(/no paid ai services are being called/i);
  });

  it("renders nothing when generation_mode is live", async () => {
    mocks.fetchPublicConfig.mockResolvedValue({
      demo_mode: false,
      generation_enabled: true,
      generation_mode: "live",
      max_inspiration_images: 3,
      max_refinements: 1,
    });
    renderBanner();
    await waitFor(() => expect(mocks.fetchPublicConfig).toHaveBeenCalled());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText(/demo mode/i)).not.toBeInTheDocument();
  });

  it("renders nothing when generation_mode is unavailable", async () => {
    mocks.fetchPublicConfig.mockResolvedValue({
      demo_mode: true,
      generation_enabled: false,
      generation_mode: "unavailable",
      max_inspiration_images: 3,
      max_refinements: 1,
    });
    renderBanner();
    await waitFor(() => expect(mocks.fetchPublicConfig).toHaveBeenCalled());
    expect(screen.queryByText(/demo mode/i)).not.toBeInTheDocument();
  });
});
