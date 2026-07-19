"use client";

// The private concept results page (Phase 12 Part C). Two independent
// TanStack Query reads: the stable result payload (fetched once) and the
// short-lived signed-image payload (starts only after the result succeeds,
// refreshed on its own schedule while the page stays open). Independence is
// deliberate — image delivery may be temporarily unavailable while the
// validated design brief remains fully readable.

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

import { DesignBrief } from "./DesignBrief";
import { ResultImage } from "./ResultImage";
import {
  classifyResultError,
  DesignImageQueryError,
  DesignResultQueryError,
  resultErrorCopy,
} from "./result-errors";
import { fetchDesignImageUrls, fetchDesignResult } from "@/lib/api";
import type { DesignImages } from "@/lib/api";

type Props = { designId: string; versionId: string };

// Refresh at ~80% of the observed remaining lifetime, never below a minimum
// positive delay, and never at all once there is no data or the expiry
// timestamp is unusable.
const MIN_REFRESH_DELAY_MS = 1_000;
const NEAR_EXPIRY_MS = 15_000;

function imageRefetchIntervalMs(images: DesignImages | undefined, now: number): number | false {
  if (!images) return false;
  const expiresAt = Date.parse(images.expires_at);
  if (Number.isNaN(expiresAt)) return false;
  const remaining = expiresAt - now;
  if (remaining <= 0) return false;
  return Math.max(MIN_REFRESH_DELAY_MS, remaining * 0.8);
}

export function DesignResult({ designId, versionId }: Props) {
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

  // Immediate refresh on focus, but only when the current URL is genuinely
  // near expiry — not on every focus, which would defeat the 80%-lifetime
  // schedule above and risk a tight refresh loop.
  useEffect(() => {
    function onFocus() {
      const images = imageQuery.data;
      if (!images) return;
      const expiresAt = Date.parse(images.expires_at);
      if (Number.isNaN(expiresAt)) return;
      if (expiresAt - Date.now() <= NEAR_EXPIRY_MS) {
        void imageQuery.refetch();
      }
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
    // imageQuery.refetch is stable for a given query key; only the observed
    // data needs to retrigger this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imageQuery.data]);

  if (resultQuery.isPending) {
    return (
      <p role="status" aria-live="polite">
        Loading your result…
      </p>
    );
  }

  if (resultQuery.isError) {
    const kind = classifyResultError(resultQuery.error);
    const copy = resultErrorCopy(kind);
    return (
      <div role="alert">
        <h1>{copy.heading}</h1>
        <p>{copy.message}</p>
        {kind !== "not_found" && (
          <button type="button" onClick={() => void resultQuery.refetch()}>
            Try again
          </button>
        )}
      </div>
    );
  }

  const result = resultQuery.data;

  return (
    <main className="design-result">
      <h1>{result.title}</h1>
      <ResultImage
        images={imageQuery.data}
        isPending={imageQuery.isPending}
        isFetching={imageQuery.isFetching}
        error={imageQuery.error}
        altText={result.image_alt_text}
        onRetry={() => void imageQuery.refetch()}
      />
      <DesignBrief result={result} />
    </main>
  );
}
