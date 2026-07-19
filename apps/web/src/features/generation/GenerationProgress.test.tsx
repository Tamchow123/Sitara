import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GenerationProgress } from "./GenerationProgress";
import { GenerationJobNotFoundError, GenerationJobUnavailableError } from "@/lib/api";
import type { GenerationJob } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  fetchGenerationJob: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
  // Captures the exact options object GenerationProgress.tsx's useQuery call
  // passes through, so tests can assert on options the rendered DOM cannot
  // otherwise reveal (e.g. refetchIntervalInBackground).
  capturedQueryOptions: null as Record<string, unknown> | null,
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchGenerationJob: mocks.fetchGenerationJob,
  };
});

vi.mock("@tanstack/react-query", async () => {
  const actual =
    await vi.importActual<typeof import("@tanstack/react-query")>("@tanstack/react-query");
  return {
    ...actual,
    useQuery: (options: Record<string, unknown>, ...rest: unknown[]) => {
      mocks.capturedQueryOptions = options;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return (actual.useQuery as any)(options, ...rest);
    },
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
  useParams: () => ({}),
}));

function job(overrides: Partial<GenerationJob> = {}): GenerationJob {
  return {
    id: "j1",
    design_id: "d1",
    design_version_id: null,
    status: "queued",
    error_code: null,
    created_at: new Date().toISOString(),
    updated_at: "t",
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

function renderProgress(designId = "d1", jobId = "j1") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <GenerationProgress designId={designId} jobId={jobId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.capturedQueryOptions = null;
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("GenerationProgress", () => {
  it("renders the queued state", async () => {
    mocks.fetchGenerationJob.mockResolvedValue(job({ status: "queued" }));
    renderProgress();
    expect(await screen.findByText(/Preparing your concept/i)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/waiting to start/i);
  });

  it("renders the running_text state", async () => {
    mocks.fetchGenerationJob.mockResolvedValue(job({ status: "running_text" }));
    renderProgress();
    expect(await screen.findByText(/Creating your design brief/i)).toBeInTheDocument();
  });

  it("renders the running_image state", async () => {
    mocks.fetchGenerationJob.mockResolvedValue(job({ status: "running_image" }));
    renderProgress();
    expect(await screen.findByText(/Creating your visual concept/i)).toBeInTheDocument();
    expect(screen.getByText(/never made public/i)).toBeInTheDocument();
  });

  it("marks the active stage with aria-current=step", async () => {
    mocks.fetchGenerationJob.mockResolvedValue(job({ status: "running_text" }));
    renderProgress();
    await screen.findByText(/Creating your design brief/i);
    const active = screen.getByText("Design brief").closest("li");
    expect(active).toHaveAttribute("aria-current", "step");
    const complete = screen.getByText(/Preparing \(complete\)/i);
    expect(complete.closest("li")).not.toHaveAttribute("aria-current");
  });

  it("redirects to the result route on succeeded with a version id", async () => {
    mocks.fetchGenerationJob.mockResolvedValue(
      job({ status: "succeeded", design_version_id: "v1" }),
    );
    renderProgress("d1", "j1");
    await vi.waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/design/d1/result/v1"));
  });

  it("shows a controlled invalid-state message when succeeded has no version id", async () => {
    mocks.fetchGenerationJob.mockResolvedValue(
      job({ status: "succeeded", design_version_id: null }),
    );
    renderProgress();
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not confirm the result/i);
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("renders the failed state with the friendly error message", async () => {
    mocks.fetchGenerationJob.mockResolvedValue(
      job({ status: "failed", error_code: "image_ingest_failed" }),
    );
    renderProgress();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/safely save your visual concept/i);
  });

  it("shows an editable link back to the questionnaire for design_incomplete", async () => {
    mocks.fetchGenerationJob.mockResolvedValue(
      job({ status: "failed", error_code: "design_incomplete" }),
    );
    renderProgress("d1", "j1");
    const link = await screen.findByRole("link", { name: /questionnaire/i });
    expect(link).toHaveAttribute("href", "/design/d1");
  });

  it("does not show a questionnaire link for a purely technical failure", async () => {
    mocks.fetchGenerationJob.mockResolvedValue(
      job({ status: "failed", error_code: "internal_generation_error" }),
    );
    renderProgress();
    await screen.findByRole("alert");
    expect(screen.queryByRole("link", { name: /questionnaire/i })).not.toBeInTheDocument();
  });

  it("shows a temporary fetch outage state distinct from a terminal failure, within a bounded number of attempts", async () => {
    vi.useFakeTimers();
    mocks.fetchGenerationJob.mockRejectedValue(new GenerationJobUnavailableError());
    renderProgress();
    // Exhaust the bounded retry/backoff (1s + 2s + 4s) before the query
    // settles into its error state.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/temporarily unavailable/i);
    expect(alert).not.toHaveTextContent(/could not confirm the result/i);
    // Regression guard for the fixed bug where refetchInterval stacked an
    // extra polling layer on top of retry/retryDelay while there was no
    // data yet: 1 initial attempt + at most 3 retries, never double that.
    expect(mocks.fetchGenerationJob.mock.calls.length).toBeLessThanOrEqual(4);
  });

  it("shows an indistinguishable not-found state on a 404", async () => {
    mocks.fetchGenerationJob.mockRejectedValue(new GenerationJobNotFoundError());
    renderProgress();
    expect(await screen.findByText(/Generation not found/i)).toBeInTheDocument();
  });

  it("treats a route/job design_id mismatch as not found, never redirecting", async () => {
    mocks.fetchGenerationJob.mockResolvedValue(
      job({ design_id: "someone-elses-design", status: "succeeded", design_version_id: "v1" }),
    );
    renderProgress("d1", "j1");
    expect(await screen.findByText(/Generation not found/i)).toBeInTheDocument();
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("a manual Try again action refetches the same job", async () => {
    vi.useFakeTimers();
    mocks.fetchGenerationJob.mockRejectedValue(new GenerationJobUnavailableError());
    renderProgress();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    screen.getByRole("alert");
    expect(mocks.fetchGenerationJob.mock.calls.length).toBeLessThanOrEqual(4);
    mocks.fetchGenerationJob.mockReset();
    mocks.fetchGenerationJob.mockResolvedValue(job({ status: "queued" }));
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText(/Preparing your concept/i)).toBeInTheDocument();
  });

  it("does not repeatedly announce the same unchanged status text", async () => {
    vi.useFakeTimers();
    mocks.fetchGenerationJob.mockResolvedValue(job({ status: "queued" }));
    renderProgress();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const status = screen.getByText(/waiting to start/i);
    const callsBeforeSecondPoll = mocks.fetchGenerationJob.mock.calls.length;
    // Advance past a real polling interval so a genuine second poll (with
    // identical data) actually happens — a vacuous "no re-render occurred at
    // all" assertion would not prove anything about repeated announcements.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(mocks.fetchGenerationJob.mock.calls.length).toBeGreaterThan(callsBeforeSecondPoll);
    // The aria-live region's own DOM node must be the SAME node across the
    // unchanged-data re-fetch (React updates it in place), not remounted —
    // a remount is what would cause assistive tech to re-announce it.
    const statusAfterSecondPoll = screen.getByText(/waiting to start/i);
    expect(statusAfterSecondPoll).toBe(status);
  });

  it("disables background polling", async () => {
    mocks.fetchGenerationJob.mockResolvedValue(job({ status: "queued" }));
    renderProgress();
    await screen.findByText(/waiting to start/i);
    expect(mocks.capturedQueryOptions?.refetchIntervalInBackground).toBe(false);
  });

  it("terminal states stop polling", async () => {
    vi.useFakeTimers();
    mocks.fetchGenerationJob.mockResolvedValue(
      job({ status: "failed", error_code: "internal_generation_error" }),
    );
    renderProgress();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mocks.fetchGenerationJob).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(mocks.fetchGenerationJob).toHaveBeenCalledTimes(1);
  });

  it("polls again while still in progress (does not stop before a terminal status)", async () => {
    vi.useFakeTimers();
    mocks.fetchGenerationJob.mockResolvedValue(job({ status: "queued" }));
    renderProgress();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const initialCalls = mocks.fetchGenerationJob.mock.calls.length;
    expect(initialCalls).toBeGreaterThanOrEqual(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(mocks.fetchGenerationJob.mock.calls.length).toBeGreaterThan(initialCalls);
  });
});
