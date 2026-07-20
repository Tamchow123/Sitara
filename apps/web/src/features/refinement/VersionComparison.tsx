"use client";

// Side-by-side original/refined comparison (Phase 14 §30-31), rendered by
// DesignResult when the viewed version's lineage is "refinement". Version 1
// (the "refined" version's own data is passed down from DesignResult, which
// already fetches it for its own rendering) is fetched here via its OWN pair
// of independent queries — mirroring DesignResult's result/image split
// exactly, never sharing a query key, so one side's failure never touches
// the other.

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
import { changeTypeLabel } from "./refinement-options";
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
  refined: SideProps;
};

function VersionCard({
  headingId,
  label,
  side,
}: {
  headingId: string;
  label: string;
  side: SideProps;
}) {
  const { result } = side;
  return (
    <article className="version-card" aria-labelledby={headingId}>
      <h2 id={headingId}>
        {label} — version {result.version_number}
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

export function VersionComparison({ designId, parentVersionId, refined }: Props) {
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
    <main className="version-comparison">
      <h1 id="comparison-heading">Compare your concepts</h1>
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

      <div className="comparison-grid">
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
                <h2>{copy.heading}</h2>
                <p>{copy.message}</p>
                {kind !== "not_found" && (
                  <button type="button" onClick={() => void parentResultQuery.refetch()}>
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

        <VersionCard headingId="version-refined-heading" label="Refined concept" side={refined} />
      </div>
    </main>
  );
}
