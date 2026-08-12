"use client";

// The reference step (Phase 16B ADR 0018/0019; rebuilt in Phase 22, ADR 0025;
// narrowed to the phone handoff alone after Phase 22 — see ADR 0026's
// amendment).
//
// Since the curated catalogue was retired this screen holds one thing: the
// customer's own photographs. There is no grid to browse, no shared budget to
// arithmetic against, and no catalogue request to fail — the three reference
// slots are the uploads' alone.
//
// And there is now one way in: the customer's own phone. The iPad's camera and
// file picker are gone. That removes this screen's own rights affirmation with
// them — it existed solely to gate those two controls, and the affirmation that
// matters was never this one. It is taken on the PHONE, per upload, from the
// person who actually chose the photograph (ADR 0026). Nothing about that
// changed; there is simply no longer a second, weaker place to take it.
//
// A reference is private user content, not catalogue content: it is never
// listed, never shown to another session, and can never be promoted into the
// catalogue. Its rights position is ONE per-upload affirmation by the person
// choosing the image — deliberately weaker than the staff-verified catalogue
// model that left the product, and never to be presented as verified rights.
// Removing the stronger model does not upgrade this one.

import { useState } from "react";

import {
  inspirationUploadImageUrl,
  removeInspirationUpload,
  type InspirationUpload as Upload,
} from "@/lib/api";

import { PhoneHandoff } from "./PhoneHandoff";

type Props = {
  // Absent until the first autosave has created the draft. The step explains
  // itself in that window rather than appearing to offer nothing.
  designId?: string;
  uploads: Upload[];
  /** The whole reference budget — this screen is the only thing drawing on it. */
  max: number;
  onChange: (uploads: Upload[]) => void;
};

type Status =
  | { kind: "idle" }
  | { kind: "removed" }
  | { kind: "error"; message: string };

export function InspirationUpload({ designId, uploads, max, onChange }: Props) {
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [busyId, setBusyId] = useState<string | null>(null);

  const slotsRemaining = Math.max(max - uploads.length, 0);

  const onRemove = async (uploadId: string): Promise<void> => {
    if (!designId) return;
    setBusyId(uploadId);
    let result;
    try {
      result = await removeInspirationUpload(designId, uploadId);
    } catch {
      setBusyId(null);
      setStatus({
        kind: "error",
        message: "That image could not be removed. Please try again.",
      });
      return;
    }
    setBusyId(null);
    if (result.ok) {
      onChange(uploads.filter((upload) => upload.id !== uploadId));
      setStatus({ kind: "removed" });
      return;
    }
    setStatus({ kind: "error", message: result.message });
  };

  return (
    <div className="upload">
      <h2 className="upload-heading">Your own photographs</h2>

      {/* One line. The panel below says where they come from, so saying it
          here too was the kind of repetition this screen was asked to lose. */}
      <p className="field-help">
        Optional — up to {max}.{" "}
        {slotsRemaining > 0
          ? `${slotsRemaining} of ${max} free.`
          : "All slots are used."}
      </p>

      {designId ? (
        <PhoneHandoff
          designId={designId}
          uploads={uploads}
          max={max}
          onUploadsChanged={onChange}
        />
      ) : (
        <p className="field-help">
          Answer a question first — your design has to exist before a photograph
          can be attached to it.
        </p>
      )}

      {/* Announced, not merely displayed: a screen-reader user gets no visual
          cue that a removal succeeded or failed, or that a slot freed up.

          Named, because the handoff panel has a live region of its own and two
          unnamed ones on a single screen leave a screen-reader user unable to
          tell which just spoke. */}
      <p
        className={
          status.kind === "error" ? "upload-status upload-status-error" : "upload-status"
        }
        role="status"
        aria-label="Changes to your photographs"
      >
        {status.kind === "removed" && "Image removed from your design."}
        {status.kind === "error" && status.message}
      </p>

      {uploads.length > 0 && designId && (
        <ul className="upload-grid" aria-label="Your uploaded images">
          {uploads.map((upload, index) => (
            <li key={upload.id} className="upload-card">
              {/* Plain <img>, never next/image: these bytes come from an
                  ownership-checked, no-store endpoint and must not be proxied
                  or cached by the image optimiser. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                className="upload-thumb"
                src={inspirationUploadImageUrl(designId, upload.id)}
                alt={`Your uploaded inspiration image ${index + 1}`}
                width={upload.width}
                height={upload.height}
              />
              <button
                type="button"
                className="upload-remove"
                onClick={() => void onRemove(upload.id)}
                disabled={busyId === upload.id}
              >
                {busyId === upload.id
                  ? "Removing…"
                  : `Remove image ${index + 1}`}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
