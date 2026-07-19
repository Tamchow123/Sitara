"use client";

// Renders the private original image from a short-lived signed URL. A plain
// <img>, never next/image — the URL is short-lived, signed and dynamically
// hosted, deliberately outside Next's remote-image cache. Independent of the
// result text: every branch here renders in place of the image only, never
// replaces the surrounding page.

import { useRef } from "react";

import { classifyImageError, imageErrorCopy } from "./result-errors";
import type { DesignImages } from "@/lib/api";

type Props = {
  images: DesignImages | undefined;
  isPending: boolean;
  isFetching: boolean;
  error: unknown;
  altText: string;
  onRetry: () => void;
};

export function ResultImage({ images, isPending, isFetching, error, altText, onRetry }: Props) {
  // Guards "attempt one signed-URL refresh, never an infinite loop": the
  // backend mints a brand-new signed URL on every refetch, so a guard keyed
  // on URL identity never actually caps a sustained failure (each refresh
  // produces a URL the guard has "never seen"). Instead this tracks whether
  // an automatic retry has already happened since the last successful image
  // load, capping to exactly one automatic retry per failure episode
  // regardless of whether the refreshed URL differs from the failing one.
  const retriedSinceLoadRef = useRef(false);

  function handleImageLoadError() {
    if (!images) return;
    if (retriedSinceLoadRef.current) return;
    retriedSinceLoadRef.current = true;
    onRetry();
  }

  function handleImageLoad() {
    retriedSinceLoadRef.current = false;
  }

  if (!images) {
    if (isPending) {
      return (
        <div className="result-image-state" role="status" aria-live="polite">
          <p>Loading your image…</p>
        </div>
      );
    }
    const kind = classifyImageError(error);
    return (
      <div className="result-image-state" role="alert">
        <p>{imageErrorCopy(kind)}</p>
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  }

  const expiresAtMs = Date.parse(images.expires_at);
  const isPastExpiry = Number.isNaN(expiresAtMs) || expiresAtMs <= Date.now();

  if (isPastExpiry) {
    if (isFetching) {
      return (
        <div className="result-image-state" role="status" aria-live="polite">
          <p>Refreshing your image…</p>
        </div>
      );
    }
    return (
      <div className="result-image-state" role="alert">
        <p>Your image link has expired.</p>
        <button type="button" onClick={onRetry}>
          Refresh image
        </button>
      </div>
    );
  }

  return (
    <figure className="result-image-figure">
      <a href={images.original.url} target="_blank" rel="noreferrer noopener">
        {/* eslint-disable-next-line @next/next/no-img-element -- short-lived
            signed URL, deliberately not part of next/image's remote cache */}
        <img
          className="result-image"
          src={images.original.url}
          alt={altText}
          width={images.original.width}
          height={images.original.height}
          referrerPolicy="no-referrer"
          onLoad={handleImageLoad}
          onError={handleImageLoadError}
        />
      </a>
      <figcaption className="result-image-actions">
        <a
          href={images.original.download_url}
          download="sitara-concept.webp"
          referrerPolicy="no-referrer"
        >
          Download image
        </a>
      </figcaption>
    </figure>
  );
}
