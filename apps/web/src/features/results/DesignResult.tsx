"use client";

// The private concept results page (Phase 12 Part C). Two independent
// TanStack Query reads: the stable result payload (fetched once) and the
// short-lived signed-image payload (starts only after the result succeeds,
// refreshed on its own schedule while the page stays open). Independence is
// deliberate — image delivery may be temporarily unavailable while the
// validated design brief remains fully readable.

import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { DesignBrief } from "./DesignBrief";
import { ResultImage } from "./ResultImage";
import {
  classifyResultError,
  DesignImageQueryError,
  DesignResultQueryError,
  resultErrorCopy,
} from "./result-errors";
import { imageRefetchIntervalMs, useImageFocusRefresh } from "./image-refresh";
import { RefinementPanel } from "@/features/refinement/RefinementPanel";
import { VersionComparison } from "@/features/refinement/VersionComparison";
import {
  isRefinementEligible,
  isRefinementFailed,
  isRefinementRunning,
  isRefinementUsed,
  refinedVersionId,
} from "@/features/refinement/refinement-eligibility";
import { friendlyGenerationError } from "@/features/generation/generation-errors";
import { fetchDesign, fetchDesignImageUrls, fetchDesignResult, fetchPublicConfig } from "@/lib/api";

type Props = { designId: string; versionId: string };

export function DesignResult({ designId, versionId }: Props) {
  const queryClient = useQueryClient();
  const resultQuery = useQuery({
    queryKey: ["design-result", designId, versionId],
    queryFn: async () => {
      const outcome = await fetchDesignResult(designId, versionId);
      if (!outcome.ok) throw new DesignResultQueryError(outcome);
      return outcome.result;
    },
    // Fetch once while mounted: no interval polling for the stable result.
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });

  const imageQuery = useQuery({
    queryKey: ["design-image", designId, versionId],
    queryFn: async () => {
      const outcome = await fetchDesignImageUrls(designId, versionId);
      if (!outcome.ok) {
        throw new DesignImageQueryError(outcome.status, outcome.code, outcome.message);
      }
      const expiresAt = Date.parse(outcome.images.expires_at);
      if (Number.isNaN(expiresAt) || expiresAt <= Date.now()) {
        throw new DesignImageQueryError(
          200,
          "invalid_response",
          "The service returned an unexpected response.",
        );
      }
      return outcome.images;
    },
    // Starts only once the result payload is valid.
    enabled: resultQuery.isSuccess,
    gcTime: 0,
    // Focus-triggered refresh is handled explicitly below (near-expiry
    // gated); the library's own default focus refetch would defeat that.
    refetchOnWindowFocus: false,
    refetchIntervalInBackground: false,
    retry: 1,
    refetchInterval: (activeQuery) => imageRefetchIntervalMs(activeQuery.state.data, Date.now()),
  });

  useImageFocusRefresh(imageQuery.data, () => void imageQuery.refetch());

  // Since Phase 14: the owning Design's latest_job (refinement eligibility,
  // in-progress/failed banners) and the public config's generation_enabled
  // flag (the same fail-closed signal ReviewSummary gates "Generate" on).
  // Independent of the result/image queries above — a slow or failed design/
  // config fetch never blocks the validated brief/image from rendering; it
  // only withholds the additive refinement section until it resolves.
  const designQuery = useQuery({
    queryKey: ["design-detail", designId],
    queryFn: () => fetchDesign(designId),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });

  const configQuery = useQuery({
    queryKey: ["public-config"],
    queryFn: () => fetchPublicConfig(),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });

  // A submit-time 409 (design_not_refinable / refinement_in_progress /
  // refinement_limit_reached) means our locally cached latest_job is stale —
  // refetch the Design so eligibility/running/failed recompute from the
  // server's current state instead of re-offering a submission we know will
  // fail again.
  const handleRequiresRecheck = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["design-detail", designId] });
  }, [queryClient, designId]);

  if (resultQuery.isPending) {
    return (
      <p role="status" aria-live="polite" className="loading-note">
        Loading your result…
      </p>
    );
  }

  if (resultQuery.isError) {
    const kind = classifyResultError(resultQuery.error);
    const copy = resultErrorCopy(kind);
    return (
      <div role="alert" className="route-error">
        <h1>{copy.heading}</h1>
        <p>{copy.message}</p>
        {/* A not-found is final — there is nothing to retry, and offering a
            button would invite the user to keep asking a question already
            answered. Everything else gets one clear next action. */}
        {kind !== "not_found" && (
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => void resultQuery.refetch()}
          >
            Try again
          </button>
        )}
      </div>
    );
  }

  const result = resultQuery.data;

  // Viewing the refined output (version 2): render the side-by-side
  // comparison instead of a single-version view. A DB constraint enforces
  // parent_version_id non-null for any "refinement" lineage kind, so the
  // `&&` below is defensive only, against a malformed or stale-cached payload
  // — not a condition expected to actually be false in practice.
  if (result.lineage.kind === "refinement" && result.lineage.parent_version_id) {
    return (
      <VersionComparison
        designId={designId}
        parentVersionId={result.lineage.parent_version_id}
        refined={{
          result,
          images: imageQuery.data,
          imagesPending: imageQuery.isPending,
          imagesFetching: imageQuery.isFetching,
          imagesError: imageQuery.error,
          onRetryImages: () => void imageQuery.refetch(),
        }}
      />
    );
  }

  const design = designQuery.data;
  const generationEnabled = configQuery.data?.generation_enabled === true;
  const eligible = designQuery.isSuccess && isRefinementEligible(result, design, generationEnabled);
  const running = designQuery.isSuccess && isRefinementRunning(design);
  const failed = designQuery.isSuccess && isRefinementFailed(design);
  // Locked, not merely absent: once the Design's own state is known and the
  // form is not on offer, the page says which of the two reasons applies.
  // Until designQuery resolves, nothing is claimed either way.
  const locked = designQuery.isSuccess && !eligible && !running && !failed;
  const used = isRefinementUsed(design);
  const refinedId = refinedVersionId(design);

  return (
    <div className="design-result">
      {/* The concept screen's masthead: kicker, title, then the labels that say
          WHICH concept this is (version number, and whether it came from the
          demo pack or a provider). The handoff's third header action — a
          "Design history" link — is deliberately absent: this application has
          no history route, and the refined version's own comparison view is
          where two versions are read side by side. */}
      <header className="result-header">
        <div className="result-header-text">
          <p className="kicker">Your concept</p>
          <h1>{result.title}</h1>
          <p className="result-meta">
            <span className="tag tag-neutral">Version {result.version_number}</span>
            <span className={result.is_demo ? "tag tag-accent-2" : "tag tag-accent"}>
              {result.is_demo ? "Demo concept" : "AI-generated concept"}
            </span>
          </p>
        </div>
        <div className="result-header-actions">
          <a className="btn btn-secondary" href={`/design/${designId}`}>
            Edit answers
          </a>
          {eligible && (
            <a className="btn btn-primary" href="#refine-concept">
              Refine this concept
            </a>
          )}
        </div>
      </header>

      <div className="concept-cols">
        {/* Sticky on a wide screen so the render stays beside whichever card is
            being read; `position: sticky` simply does not apply in the single
            column the same CSS falls back to below 860px. */}
        <div className="concept-render">
          <ResultImage
            images={imageQuery.data}
            isPending={imageQuery.isPending}
            isFetching={imageQuery.isFetching}
            error={imageQuery.error}
            altText={result.image_alt_text}
            onRetry={() => void imageQuery.refetch()}
          />
        </div>

        <div className="concept-detail">
          <DesignBrief result={result} />

          {running && design?.latest_job && (
            <div role="status" aria-live="polite" className="refinement-running-notice">
              <p>
                A refinement is currently running.{" "}
                <a href={`/design/${designId}/generation/${design.latest_job.id}`}>
                  View refinement progress
                </a>
              </p>
            </div>
          )}

          {failed && design?.latest_job && (
            <div role="alert" className="refinement-failed-notice">
              <h2>{friendlyGenerationError(design.latest_job.error_code).heading}</h2>
              <p>{friendlyGenerationError(design.latest_job.error_code).message}</p>
            </div>
          )}

          {eligible && (
            <RefinementPanel
              designId={designId}
              sourceVersionId={versionId}
              isDemo={result.is_demo}
              onRequiresRecheck={handleRequiresRecheck}
            />
          )}

          {locked && (
            <section
              className="refinement-panel refinement-locked"
              aria-labelledby="refinement-locked-heading"
            >
              <h2 id="refinement-locked-heading">Refinement</h2>
              {used ? (
                <>
                  <p>
                    You have used your one refinement for this design. Sitara allows a single
                    constrained change, so there is nothing further to request here.
                  </p>
                  {refinedId && (
                    <p>
                      <a href={`/design/${designId}/result/${refinedId}`}>
                        View your refined concept
                      </a>
                    </p>
                  )}
                </>
              ) : (
                <p>
                  Concept generation is not currently available, so this concept cannot be refined.
                </p>
              )}
              <p>To take the design somewhere else, edit your answers and start a new concept.</p>
              <div className="refinement-actions">
                <a className="btn btn-secondary" href={`/design/${designId}`}>
                  Edit answers
                </a>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
