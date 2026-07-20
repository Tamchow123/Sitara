"use client";

// The generation progress screen (Phase 12 Part B). Polls one owned
// generation job via TanStack Query with a created_at-derived backoff
// schedule, renders the durable states honestly (no fake percentage, no
// invented completion estimate), and hands off to the result route once a
// job succeeds with a confirmed DesignVersion id.

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";

import { fetchGenerationJob, GenerationJobNotFoundError, type GenerationJob } from "@/lib/api";
import { friendlyGenerationError } from "./generation-errors";
import { pollingIntervalMs } from "./generation-status";
import {
  REFINEMENT_PROGRESS_NOTES,
  refinementProgressExplanation,
  refinementProgressHeading,
} from "./refinement-progress-copy";

type Props = { designId: string; jobId: string };

const STAGE_KEYS = ["queued", "running_text", "running_image"] as const;
type StageKey = (typeof STAGE_KEYS)[number];

const STAGE_LABELS: Record<StageKey, string> = {
  queued: "Preparing",
  running_text: "Design brief",
  running_image: "Visual concept",
};

function stageIndexFor(status: GenerationJob["status"]): number {
  const index = STAGE_KEYS.indexOf(status as StageKey);
  if (index >= 0) return index;
  // succeeded/failed both mean every stage was at least attempted.
  return STAGE_KEYS.length;
}

export function GenerationProgress({ designId, jobId }: Props) {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Since Phase 14: RefinementPanel appends the source version id so a
  // refinement's progress/failure screens can link back to the still-private,
  // still-readable original concept. Purely a navigation convenience — never
  // trusted for anything security-sensitive, never persisted anywhere.
  const originalVersionId = searchParams.get("from");

  const query = useQuery({
    queryKey: ["generation-job", jobId],
    queryFn: async (): Promise<GenerationJob> => {
      const job = await fetchGenerationJob(jobId);
      // A mismatch between the route's designId and the fetched job's own
      // design_id is treated as not-found — never redirect using ids from a
      // payload that does not belong to this route.
      if (job.design_id !== designId) {
        throw new GenerationJobNotFoundError();
      }
      return job;
    },
    refetchInterval: (activeQuery) => {
      const data = activeQuery.state.data;
      // No successful fetch yet: rely solely on the query's own bounded
      // retry/backoff below, not a second overlapping polling layer.
      if (!data) return false;
      return pollingIntervalMs(data.status, data.created_at, Date.now());
    },
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    retry: (failureCount, error) =>
      !(error instanceof GenerationJobNotFoundError) && failureCount < 3,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
  });

  const job = query.data;

  useEffect(() => {
    if (job?.status === "succeeded" && job.design_version_id) {
      router.replace(`/design/${designId}/result/${job.design_version_id}`);
    }
    // router is intentionally omitted: Next.js guarantees a stable
    // reference, and including it would re-run this effect (and re-issue
    // the navigation) whenever a caller's router mock is not memoised.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status, job?.design_version_id, designId]);

  if (query.error instanceof GenerationJobNotFoundError) {
    return (
      <div role="alert" className="generation-unavailable">
        <h1>Generation not found</h1>
        <p>This generation attempt is not available. It may belong to a different design.</p>
      </div>
    );
  }

  if (!job) {
    if (query.isError) {
      return (
        <div role="alert" className="generation-unavailable">
          <h1>Progress temporarily unavailable</h1>
          <p>We could not check your generation progress just now.</p>
          <button type="button" onClick={() => void query.refetch()}>
            Try again
          </button>
        </div>
      );
    }
    return (
      <p role="status" aria-live="polite">
        Checking your generation…
      </p>
    );
  }

  if (job.status === "succeeded" && !job.design_version_id) {
    return (
      <div role="alert" className="generation-unavailable">
        <h1>Something went wrong</h1>
        <p>
          Your concept finished generating, but we could not confirm the result. Please try
          again shortly.
        </p>
      </div>
    );
  }

  if (job.status === "succeeded") {
    // The effect above performs the redirect; this is a brief transitional
    // state while that navigation completes.
    return (
      <p role="status" aria-live="polite">
        Your concept is ready — taking you to the result…
      </p>
    );
  }

  const isRefinement = job.generation_kind === "refinement";

  if (job.status === "failed") {
    const friendly = friendlyGenerationError(job.error_code);
    return (
      <main className="generation-progress">
        <div role="alert" className="generation-failed">
          <h1>{friendly.heading}</h1>
          <p>{friendly.message}</p>
          {friendly.editable && (
            <p>
              <a href={`/design/${designId}`}>Return to the questionnaire</a>
            </p>
          )}
          {isRefinement && originalVersionId && (
            <p>
              <a href={`/design/${designId}/result/${originalVersionId}`}>
                Return to your original concept
              </a>
            </p>
          )}
        </div>
        <p className="generation-privacy-note">
          Your private design details are never made public during generation.
        </p>
      </main>
    );
  }

  const stageIndex = stageIndexFor(job.status);
  const heading = isRefinement
    ? refinementProgressHeading(job.status)
    : job.status === "queued"
      ? "Preparing your concept"
      : job.status === "running_text"
        ? "Creating your design brief"
        : "Creating your visual concept";
  const explanation = isRefinement
    ? refinementProgressExplanation(job.status)
    : job.status === "queued"
      ? "Your generation job is waiting to start."
      : job.status === "running_text"
        ? "The details you selected are being converted into a structured concept."
        : "Your image is being generated, verified and stored privately.";

  return (
    <main className="generation-progress">
      <ol className="generation-stages">
        {STAGE_KEYS.map((stage, index) => (
          <li
            key={stage}
            aria-current={index === stageIndex ? "step" : undefined}
            className={
              index < stageIndex
                ? "generation-stage-complete"
                : index === stageIndex
                  ? "generation-stage-active"
                  : "generation-stage-pending"
            }
          >
            {STAGE_LABELS[stage]}
            {index < stageIndex ? " (complete)" : null}
          </li>
        ))}
      </ol>
      <div role="status" aria-live="polite">
        <h1>{heading}</h1>
        <p>{explanation}</p>
      </div>
      {isRefinement && (
        <ul className="refinement-progress-notes">
          {REFINEMENT_PROGRESS_NOTES.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
      <p className="generation-privacy-note">
        Your private design details are never made public during generation.
      </p>
    </main>
  );
}
