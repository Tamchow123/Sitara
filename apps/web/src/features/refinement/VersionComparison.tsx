"use client";

// Side-by-side original/refined comparison (Phase 14 §30-31), rendered by
// DesignResult when the viewed version's lineage is "refinement". Version 1
// (the "refined" version's own data is passed down from DesignResult, which
// already fetches it for its own rendering) is fetched here via its OWN pair
// of independent queries — mirroring DesignResult's result/image split
// exactly, never sharing a query key, so one side's failure never touches
// the other.

import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { DesignBrief } from "@/features/results/DesignBrief";
import { ResultImage } from "@/features/results/ResultImage";
import {
  classifyResultError,
  DesignImageQueryError,
  DesignResultQueryError,
  resultErrorCopy,
} from "@/features/results/result-errors";
import { imageRefetchIntervalMs, useImageFocusRefresh } from "@/features/results/image-refresh";
import { changeTypeLabel, refinementsLeftLabel } from "./refinement-options";
import { fetchDesignImageUrls, fetchDesignResult } from "@/lib/api";
import type { DesignImages, DesignResult as DesignResultType } from "@/lib/api";

type SideProps = {
  result: DesignResultType;
  images: DesignImages | undefined;
  imagesPending: boolean;
  imagesFetching: boolean;
  imagesError: unknown;
  onRetryImages: () => void;
};

type Props = {
  designId: string;
  parentVersionId: string;
  /** The DESIGN's remaining budget, for the note under the previous version. */
  refinementsRemaining: number;
  refined: SideProps;
  /**
   * The refinement offer for the version being viewed. Passed in rather than
   * built here because it needs the design/config queries DesignResult already
   * owns — and it appears at all only because a refined concept can now be
   * refined again.
   */
  children?: ReactNode;
};

function VersionCard({
  headingId,
  label,
  side,
  current = false,
}: {
  headingId: string;
  label: string;
  side: SideProps;
  /** The latest version — rendered larger and tagged, per the History screen. */
  current?: boolean;
}) {
  const { result } = side;
  return (
    <article
      className={current ? "version-card version-card-current" : "version-card"}
      aria-labelledby={headingId}
    >
      <h2 id={headingId}>
        {/* The tag is the History screen's "Current" marker. It is inside the
            heading so the distinction is announced with the heading rather
            than floating beside it as colour alone. */}
        {current && <span className="tag tag-accent">Current</span>}
        {label} — version {result.version_number}
        <span className="version-mode-label" data-mode={result.is_demo ? "demo" : "live"}>
          {result.is_demo ? "Demo" : "Live"}
        </span>
      </h2>
      <ResultImage
        images={side.images}
        isPending={side.imagesPending}
        isFetching={side.imagesFetching}
        error={side.imagesError}
        altText={result.image_alt_text}
        onRetry={side.onRetryImages}
      />
      <p className="version-card-title">{result.title}</p>
      <p className="version-card-summary">{result.concept_summary}</p>
      <details className="version-card-details">
        <summary>View complete brief</summary>
        <DesignBrief result={result} />
      </details>
    </article>
  );
}

export function VersionComparison({
  designId,
  parentVersionId,
  refinementsRemaining,
  refined,
  children,
}: Props) {
  const parentResultQuery = useQuery({
    queryKey: ["design-result", designId, parentVersionId],
    queryFn: async () => {
      const outcome = await fetchDesignResult(designId, parentVersionId);
      if (!outcome.ok) throw new DesignResultQueryError(outcome);
      return outcome.result;
    },
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });

  const parentImageQuery = useQuery({
    queryKey: ["design-image", designId, parentVersionId],
    queryFn: async () => {
      const outcome = await fetchDesignImageUrls(designId, parentVersionId);
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
    enabled: parentResultQuery.isSuccess,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchIntervalInBackground: false,
    retry: 1,
    refetchInterval: (activeQuery) => imageRefetchIntervalMs(activeQuery.state.data, Date.now()),
  });

  useImageFocusRefresh(parentImageQuery.data, () => void parentImageQuery.refetch());

  const changeType = refined.result.lineage.refinement?.change_type;

  return (
    <div className="version-comparison">
      {/* `Sitara History.dc.html`'s hierarchy. Its "3 refinements left" kicker
          described an unlimited history when this was written and the budget
          was one; the budget is now three, so the count is real — read from the
          server rather than restated as a constant here. */}
      <p className="kicker">
        Design history · {refinementsLeftLabel(refinementsRemaining)}
      </p>
      <h1 id="comparison-heading">Compare your concepts</h1>
      <p className="lede">
        Your current design alongside the concept it was refined from. Both stay private to you.
      </p>
      <div className="comparison-disclosure" role="note" aria-label="Comparison disclaimer">
        <p>
          The refined image is a <strong>new generation</strong>, not an edit of the original —
          visual drift is expected. Only your selected change to the design brief was constrained;
          reusing the original seed does not guarantee the same pose, composition or garment
          details.
        </p>
        {changeType && (
          <p>
            Requested change: <strong>{changeTypeLabel(changeType)}</strong>
          </p>
        )}
      </div>

      {/* Latest first, as the History screen puts the current design first and
           earlier ones under "Previous designs". The refined side needs no
           query of its own — DesignResult already holds it — so it renders
           immediately while the original is still loading beside it. */}
      <div className="comparison-grid">
        <VersionCard
          headingId="version-refined-heading"
          label="Refined concept"
          side={refined}
          current
        />

        <div className="version-previous">
          <h2 className="kicker" id="version-previous-heading">
            Previous design
          </h2>
          {parentResultQuery.isPending && (
            <p role="status" aria-live="polite">
              Loading your original concept…
            </p>
          )}
          {parentResultQuery.isError &&
            (() => {
              const kind = classifyResultError(parentResultQuery.error);
              const copy = resultErrorCopy(kind);
              return (
                <div role="alert">
                  <h3>{copy.heading}</h3>
                  <p>{copy.message}</p>
                  {kind !== "not_found" && (
                    <button
                      type="button"
                      className="btn btn-secondary"
                      onClick={() => void parentResultQuery.refetch()}
                    >
                      Try again
                    </button>
                  )}
                </div>
              );
            })()}
          {parentResultQuery.data && (
            <VersionCard
              headingId="version-original-heading"
              label="Original concept"
              side={{
                result: parentResultQuery.data,
                images: parentImageQuery.data,
                imagesPending: parentImageQuery.isPending,
                imagesFetching: parentImageQuery.isFetching,
                imagesError: parentImageQuery.error,
                onRetryImages: () => void parentImageQuery.refetch(),
              }}
            />
          )}
          <p className="version-limit-note">
            {refinementsRemaining > 0
              ? `You have ${refinementsLeftLabel(refinementsRemaining)} for this design. To take it somewhere else entirely, edit your answers and start a new concept.`
              : "You have used every refinement for this design. To take it somewhere else, edit your answers and start a new concept."}
          </p>
        </div>
      </div>

      {/* The refinement offer for the version being viewed — the form when a
          round is left, the locked panel with its reason when not. Below the
          comparison rather than above it: the two concepts are what the
          customer came here to look at, and the next change is decided after
          reading them. */}
      {children}
    </div>
  );
}
