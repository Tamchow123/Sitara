"use client";

// Renders the private original image from a short-lived signed URL. A plain
// <img>, never next/image — the URL is short-lived, signed and dynamically
// hosted, deliberately outside Next's remote-image cache. Independent of the
// result text: every branch here renders in place of the image only, never
// replaces the surrounding page.

import { useRef } from "react";

import { classifyImageError, imageErrorCopy } from "./result-errors";
import { AccountSendDisclosure } from "@/features/annotations/AccountSendDisclosure";
import { SendToAccountButton } from "@/features/annotations/SendToAccountButton";
import { useAuth } from "@/lib/auth";
import type { DesignImages } from "@/lib/api";

type Props = {
  images: DesignImages | undefined;
  isPending: boolean;
  isFetching: boolean;
  error: unknown;
  altText: string;
  onRetry: () => void;
  /** Omitted on the comparison view, which shows two renders read-only. */
  designId?: string;
  versionId?: string;
};

export function ResultImage({
  images,
  isPending,
  isFetching,
  error,
  altText,
  onRetry,
  designId,
  versionId,
}: Props) {
  const { user } = useAuth();
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
        <button type="button" className="btn btn-secondary" onClick={onRetry}>
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
        <button type="button" className="btn btn-secondary" onClick={onRetry}>
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
      {/* Phase 19 replaced the download link with these two actions. Removing
          the link is a UX decision, NOT a privacy control: the signed image URL
          is still a temporary bearer URL that anyone holding it can fetch, and
          the browser can still save the image it is already displaying. What
          changed is where the product points the user — a private worktable, and
          a copy sent to their own account address. */}
      {designId && versionId && (
        <figcaption className="result-image-actions">
          <a className="btn btn-secondary" href={`/design/${designId}/result/${versionId}/annotate`}>
            Annotate
          </a>
          <SendToAccountButton
            designId={designId}
            versionId={versionId}
            kind="plain"
            label="Send to account"
            accountEmail={user?.email ?? null}
          />
          {/* §8.5's disclosure, on THIS surface too. It was originally written
              only into the annotation workspace's panel footer — but this button
              is one click from the concept screen and is the likelier first send
              for someone who never opens Annotate, so scoping the sentence to the
              annotated flow left the more direct path undisclosed. Shared
              component, so the two surfaces cannot describe the same accepted
              exposure differently. */}
          <p className="result-image-send-note">
            <AccountSendDisclosure kind="plain" />
          </p>
        </figcaption>
      )}
    </figure>
  );
}
