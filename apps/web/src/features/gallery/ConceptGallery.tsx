"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { fetchOwnedDesigns, type DesignListItem, type DesignListVersion } from "@/lib/api";

import { GalleryThumbnail } from "./GalleryThumbnail";

// The account page's list of everything this person has made.
//
// Reads the paged list endpoint only. That payload carries no signed URL, no
// storage key, no image hash, no prompt, no annotation note, and none of the
// DesignSpec's description of the garment — only `display_title`, the concept's
// name, which a gallery cannot be navigated without (see the backend's
// `_display_title` for that boundary). Each card asks for its own thumbnail
// separately, through the ownership-checked images endpoint. Ownership is
// enforced by Django; this component never filters for privacy, it only renders
// what the server was willing to return.

// One page is all the gallery asks for. Paging exists on the endpoint because
// the result set grows without limit, but a "load more" control is a separate
// piece of UX that the phase does not ask for — so rather than pretend the
// list is complete, a card count states plainly how many of the total are
// shown when there are more.
const PAGE_SIZE = 20;

type Load =
  | { status: "loading" }
  | { status: "ready"; designs: DesignListItem[]; total: number }
  | { status: "failed"; message: string };

type VersionState = {
  /** What this version is doing, in the user's words. */
  label: string;
  /** True when there is a rendered concept to open. */
  viewable: boolean;
};

// Derived from the two fields the list carries — `has_image` and this
// version's own `job_status` — and deliberately nothing else. The list has no
// error code and no job snapshot, which is why a failure here says only that
// it failed: the honest limit of what the payload knows.
export function versionState(version: DesignListVersion): VersionState {
  if (version.has_image) return { label: "Ready", viewable: true };
  switch (version.job_status) {
    case "queued":
    case "running_text":
    case "running_image":
      return { label: "Still being made", viewable: false };
    case "failed":
      return { label: "Did not finish", viewable: false };
    case "succeeded":
      // Succeeded without a stored image: the job is done but the picture has
      // not landed yet. Saying "ready" here would offer a link to nothing.
      return { label: "Finishing up", viewable: false };
    default:
      // job_status is null on a row whose attempt was deleted. Nothing can be
      // said about progress, so nothing is claimed.
      return { label: "No picture available", viewable: false };
  }
}

// The gallery is fetched in the browser after mount, so this only ever formats
// on the client — there is no server-rendered copy for it to disagree with,
// which is why the visitor's own locale is safe to use here.
function madeOn(iso: string): string {
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return "";
  return new Date(parsed).toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

// Alt text built from what the LIST carries — the card's display name and the
// version number. Deliberately NOT the result screen's `image_alt_text`, which
// is the DesignSpec's own description of the garment: the list carries the
// concept's NAME and none of its description, so the two screens describe the
// same picture differently on purpose, and this is the more modest of the two.
// A gallery row is not the place to publish the generated description of what
// twenty people are wearing to their weddings.
function thumbnailAlt(design: DesignListItem, version: DesignListVersion): string {
  return `${design.display_title} — version ${version.version_number}`;
}

// The newest version that actually has a picture, which is not always the
// newest version: a refinement that is still generating should not blank out
// the card for the concept the person already has.
function newestViewable(design: DesignListItem): DesignListVersion | null {
  for (let index = design.versions.length - 1; index >= 0; index -= 1) {
    const version = design.versions[index];
    if (version.has_image) return version;
  }
  return null;
}

function DesignCard({ design }: { design: DesignListItem }) {
  const headingId = `gallery-card-${design.id}`;
  const cover = newestViewable(design);
  // Versions come back ordered by version number; rendered in that order so
  // the original reads above the refinement made from it.
  const versions = design.versions;

  return (
    <li>
      <article className="card gallery-card" aria-labelledby={headingId}>
        {cover ? (
          <GalleryThumbnail
            designId={design.id}
            versionId={cover.id}
            alt={thumbnailAlt(design, cover)}
          />
        ) : (
          // Three different sentences can land in a card, and they are NOT
          // interchangeable — keeping them distinct is the point, so they are
          // listed together here to stop a future edit collapsing them:
          //   "No picture yet"      (here) nothing has been rendered for this
          //                         design at all — a structural absence.
          //   "Preview unavailable" (GalleryThumbnail) a picture exists, but
          //                         fetching or decoding it failed — transient.
          //   "No picture available" (versionState) one VERSION has no image and
          //                         no job status to explain why — a row label,
          //                         not a placeholder, and it sits beside the
          //                         version number rather than in this box.
          <div className="gallery-thumb gallery-thumb-failed">
            <span>No picture yet</span>
          </div>
        )}

        {/* `display_title`, never `title`: the questionnaire never sets a
            design's own title, so `title` is the empty string for every concept
            made through the product and this heading would render blank. */}
        <h3 id={headingId} className="gallery-card-title">
          {design.display_title}
        </h3>

        <p className="gallery-card-meta">
          <time dateTime={design.created_at}>{madeOn(design.created_at)}</time>
          {/* Null means the design has no versions, so it was never generated
              either way — labelling it would be inventing a fact. */}
          {design.is_demo !== null && (
            <span className={design.is_demo ? "tag tag-accent-2" : "tag tag-accent"}>
              {design.is_demo ? "Demo concept" : "AI-generated concept"}
            </span>
          )}
        </p>

        {versions.length > 0 ? (
          <ul className="gallery-versions">
            {versions.map((version) => {
              const state = versionState(version);
              return (
                <li key={version.id} className="gallery-version">
                  <span className="gallery-version-name">
                    Version {version.version_number}
                  </span>
                  <span className="tag tag-neutral">{state.label}</span>
                  {state.viewable && (
                    <span className="gallery-version-actions">
                      {/* Every link names its design AND its version, so the
                          card's links are still tellable apart in a
                          screen-reader's list of links, where the visual
                          grouping that separates them is gone. */}
                      <a
                        className="btn btn-secondary btn-small"
                        href={`/design/${design.id}/result/${version.id}`}
                      >
                        View
                        <span className="visually-hidden">
                          {` ${design.display_title}, version ${version.version_number}`}
                        </span>
                      </a>
                      <a
                        className="btn btn-ghost btn-small"
                        href={`/design/${design.id}/result/${version.id}/annotate`}
                      >
                        Annotate
                        <span className="visually-hidden">
                          {` ${design.display_title}, version ${version.version_number}`}
                        </span>
                      </a>
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="gallery-card-note">
            This design has no concept yet — its answers are saved.
          </p>
        )}

        {/* Only when there is nothing to open, so a card never carries two
            competing primary actions. */}
        {!cover && (
          <a className="btn btn-secondary btn-small" href={`/design/${design.id}`}>
            Continue
            <span className="visually-hidden">{` ${design.display_title}`}</span>
          </a>
        )}
      </article>
    </li>
  );
}

export function ConceptGallery() {
  const [load, setLoad] = useState<Load>({ status: "loading" });

  const run = useCallback(async () => {
    setLoad({ status: "loading" });
    const result = await fetchOwnedDesigns({ limit: PAGE_SIZE });
    if (result.ok) {
      setLoad({ status: "ready", designs: result.data.designs, total: result.data.total });
      return;
    }
    // The list either loaded or it did not. Showing an empty gallery on a
    // failed request would tell someone their concepts are gone.
    setLoad({
      status: "failed",
      message: "Your concepts could not be loaded right now. Please try again shortly.",
    });
  }, []);

  useEffect(() => {
    void run();
  }, [run]);

  const shown = load.status === "ready" ? load.designs.length : 0;
  const hidden = load.status === "ready" ? Math.max(0, load.total - shown) : 0;

  return (
    <section className="panel" aria-labelledby="gallery-heading">
      <h2 id="gallery-heading">Your concepts</h2>

      {/* One live region for the section's own state. Individual cards stay
          out of it: a thumbnail that fails is not worth an announcement. */}
      <div role="status" aria-live="polite">
        {load.status === "loading" && <p className="loading-note">Loading your concepts…</p>}
        {load.status === "ready" && shown === 0 && (
          <>
            <p>You have not made a concept yet.</p>
            <Link className="btn btn-primary" href="/design/new">
              Start a design
            </Link>
          </>
        )}
        {load.status === "ready" && hidden > 0 && (
          <p className="gallery-count">
            {`Showing your ${shown} most recent concepts of ${load.total}.`}
          </p>
        )}
      </div>

      {load.status === "failed" && (
        <div role="alert" className="alert alert-error">
          <p className="alert-title">Your concepts could not be loaded</p>
          <p>{load.message}</p>
          <button type="button" className="btn btn-secondary" onClick={() => void run()}>
            Try again
          </button>
        </div>
      )}

      {load.status === "ready" && shown > 0 && (
        <ul className="gallery-grid">
          {load.designs.map((design) => (
            <DesignCard key={design.id} design={design} />
          ))}
        </ul>
      )}
    </section>
  );
}
