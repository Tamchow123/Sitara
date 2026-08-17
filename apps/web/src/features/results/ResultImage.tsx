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

/**
 * Whether this image will render its actions — i.e. whether a send control will
 * exist for it at all.
 *
 * Exported because the comparison screen has to state ADR 0021's accepted
 * exposure exactly once for a screen carrying two of these, and "once" is only
 * answerable if the screen can ask the same question the cards answer. Every
 * branch below that returns early — no images, past expiry — returns a state
 * with no Annotate and no Send, and this is the one predicate that decides it.
 */
export function hasDeliverableImage(
  images: DesignImages | undefined,
  now: number = Date.now(),
): boolean {
  if (!images) return false;
  const expiresAtMs = Date.parse(images.expires_at);
  return !Number.isNaN(expiresAtMs) && expiresAtMs > now;
}

type Props = {
  images: DesignImages | undefined;
  isPending: boolean;
  isFetching: boolean;
  error: unknown;
  altText: string;
  onRetry: () => void;
  /**
   * REQUIRED. These two used to be optional, with a comment saying the
   * comparison view omitted them because it "shows two renders read-only".
   * That was never true — the full-size signed-URL anchor below sits above the
   * gate, and the brief on each card carries Copy and Download — and the effect
   * was that after a refinement the concept a customer was looking at offered
   * neither Annotate nor Send to account, while the same version's own page
   * offered both. Required so the type system, not a convention, guarantees
   * that every concept image carries its actions.
   */
  designId: string;
  versionId: string;
  /**
   * Which concept this is, e.g. "version 2" or "Previous concept, version 1".
   * Appended visually-hidden to the actions so two sets on one screen are
   * distinguishable in a screen reader's list, where the grouping <article> is
   * not shown.
   */
  versionLabel: string;
  /**
   * Whether THIS instance renders the account-send disclosure sentence.
   *
   * Exactly one instance per SCREEN must, and the sentence must appear whenever
   * any send control does. A screen with one image leaves this alone: the
   * disclosure then lives in the same branch as the button it describes, so it
   * never prints while the image is pending, errored or expired and no send
   * control exists.
   *
   * A screen with TWO cannot decide it here — tying it to one nominated card
   * would drop the sentence entirely if THAT card's image failed while its
   * sibling still offered Send. Such a screen passes `false` to every card and
   * renders the sentence itself, asking `hasDeliverableImage` above the same
   * question these branches answer.
   */
  showSendDisclosure?: boolean;
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
  versionLabel,
  showSendDisclosure = true,
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

  if (!hasDeliverableImage(images)) {
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
      <figcaption className="result-image-actions">
        <a className="btn btn-secondary" href={`/design/${designId}/result/${versionId}/annotate`}>
          Annotate
          <span className="visually-hidden"> — {versionLabel}</span>
        </a>
        <SendToAccountButton
          designId={designId}
          versionId={versionId}
          kind="plain"
          label="Send to account"
          accountEmail={user?.email ?? null}
          qualifier={versionLabel}
        />
        {/* §8.5's disclosure, on THIS surface too. It was originally written
            only into the annotation workspace's panel footer — but this button
            is one click from the concept screen and is the likelier first send
            for someone who never opens Annotate, so scoping the sentence to the
            annotated flow left the more direct path undisclosed. Shared
            component, so the two surfaces cannot describe the same accepted
            exposure differently. */}
        {showSendDisclosure && (
          <p className="result-image-send-note">
            <AccountSendDisclosure kind="plain" />
          </p>
        )}
      </figcaption>
    </figure>
  );
}
