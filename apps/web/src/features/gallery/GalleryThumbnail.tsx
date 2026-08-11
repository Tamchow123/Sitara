"use client";

import { useEffect, useRef, useState } from "react";

import { fetchDesignImageUrls } from "@/lib/api";

// One card's picture, fetched independently of every other card's.
//
// Why per-card rather than one batched call: the list endpoint deliberately
// carries no signed URL (a bearer token per row, most of which nobody looks
// at), so a thumbnail can only come from the ownership-checked images
// endpoint. That means N requests for N cards — accepted, because the
// alternative is minting N bearer URLs whether or not they are used.
//
// The URL is a TEMPORARY BEARER URL. It lives in this component's state for
// as long as the card is mounted and is never written to localStorage,
// sessionStorage, IndexedDB or any module-level cache. There is deliberately
// no refresh timer: a gallery is a place you pass through, and a stale
// thumbnail degrades to the same broken-image state handled below.

type Load = { status: "loading" } | { status: "ready"; url: string } | { status: "failed" };

export function GalleryThumbnail({
  designId,
  versionId,
  alt,
}: {
  designId: string;
  versionId: string;
  alt: string;
}) {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  // Guards against a state write after the card has been unmounted — a
  // gallery is exactly where someone navigates away mid-flight.
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    setLoad({ status: "loading" });
    void (async () => {
      const result = await fetchDesignImageUrls(designId, versionId);
      if (!alive.current) return;
      // Every failure — transport, 404, malformed body — lands in the same
      // place. A card whose picture will not load is still a usable card:
      // the title, the date, the label and both links are unaffected.
      setLoad(result.ok ? { status: "ready", url: result.images.thumbnail.url } : { status: "failed" });
    })();
    return () => {
      alive.current = false;
    };
  }, [designId, versionId]);

  if (load.status === "ready") {
    return (
      /* eslint-disable-next-line @next/next/no-img-element -- short-lived
         signed URL, deliberately outside next/image's remote cache */
      <img
        className="gallery-thumb"
        src={load.url}
        alt={alt}
        // A signed URL can expire between the fetch and the decode, and the
        // object is private, so a broken <img> is a normal outcome rather
        // than an exception. Fall back to the same placeholder.
        onError={() => setLoad({ status: "failed" })}
      />
    );
  }

  if (load.status === "loading") {
    // aria-hidden because the surrounding card already names itself; a
    // shimmering box does not need its own announcement.
    return <div className="gallery-thumb gallery-thumb-loading skeleton" aria-hidden="true" />;
  }

  // Not an alert: one unloadable picture in a list of concepts is not worth
  // interrupting a screen-reader user for, but it must not be silent either,
  // so the placeholder carries real text rather than an empty box.
  //
  // "Preview unavailable" and NOT ConceptGallery's "No picture yet": a picture
  // exists here and could not be shown, which is a different fact from a design
  // that has never been rendered. See the note beside that string for all three.
  return (
    <div className="gallery-thumb gallery-thumb-failed">
      <span>Preview unavailable</span>
    </div>
  );
}
