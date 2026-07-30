"use client";

// The user's own inspiration photographs (Phase 16B, ADR 0018/0019).
//
// An upload is private user content, not catalogue content: it is never listed,
// never shown to another session, and can never be promoted into the catalogue.
// Its rights position is ONE per-upload affirmation by the person uploading —
// deliberately weaker than the staff-verified catalogue model, and never
// presented as verified rights.
//
// The affirmation is gated behind the ADR 0019 disclosure, which must be
// readable BEFORE the file picker is usable, because that decision sends the
// bytes of a chosen reference to an image provider whose terms take a
// perpetual, irrevocable licence over inputs. A user cannot consent to
// something the interface has not told them.
//
// Uploads share the single three-reference budget with curated presets, so this
// component is told how many slots remain rather than counting its own.

import { useId, useRef, useState } from "react";

import {
  inspirationUploadImageUrl,
  removeInspirationUpload,
  uploadInspirationImage,
  type InspirationUpload as Upload,
} from "@/lib/api";

const ACCEPTED = "image/jpeg,image/png,image/webp";

type Props = {
  designId: string;
  uploads: Upload[];
  slotsRemaining: number;
  onChange: (uploads: Upload[]) => void;
};

type Status =
  | { kind: "idle" }
  | { kind: "uploading"; name: string }
  | { kind: "added" }
  | { kind: "removed" }
  | { kind: "error"; message: string };

export function InspirationUpload({
  designId,
  uploads,
  slotsRemaining,
  onChange,
}: Props) {
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [acknowledged, setAcknowledged] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const disclosureId = useId();
  const acknowledgeId = useId();
  const helpId = useId();

  const full = slotsRemaining <= 0;
  const uploading = status.kind === "uploading";
  const canChoose = acknowledged && !full && !uploading;

  const handleFile = async (file: File): Promise<void> => {
    setStatus({ kind: "uploading", name: file.name });
    let result;
    try {
      result = await uploadInspirationImage(designId, file, acknowledged);
    } catch {
      // A timeout or network drop — never a silent failure.
      setStatus({
        kind: "error",
        message: "The upload did not finish. Check your connection and try again.",
      });
      return;
    }
    if (result?.ok) {
      onChange([...uploads, result.upload]);
      setStatus({ kind: "added" });
      return;
    }
    setStatus({
      kind: "error",
      message: result?.message ?? "The image could not be added. Please try again.",
    });
  };

  const onSelect = (event: React.ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    // Clear immediately so choosing the SAME file again still fires a change
    // event — otherwise a retry after a failure would appear to do nothing.
    event.target.value = "";
    if (!file) return;
    // Checked explicitly, not left to the disabled attribute alone. This is the
    // consent gate for sending someone's photograph to an external provider; it
    // should not depend on a DOM property a stray programmatic change can
    // bypass. The server refuses an unacknowledged upload too.
    if (!canChoose) return;
    void handleFile(file);
  };

  const onRemove = async (uploadId: string): Promise<void> => {
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
      <h3 className="upload-heading">Your own photographs</h3>

      <div className="upload-disclosure" id={disclosureId}>
        <p>
          These are private to your design. They are never added to Sitara&apos;s
          catalogue, never shown to anyone else, and are deleted with your design.
        </p>
        <p>
          <strong>Before you upload, please read this.</strong> If you use an image
          you upload as a reference, its file is sent to the external AI image
          provider that draws your concept. That provider&apos;s terms take a
          perpetual, irrevocable licence over what it receives, to train and
          improve their technology. They publish no time limit on how long they
          keep it, and it is unresolved whether those terms differ when Sitara
          reaches them through Replicate. Sitara cannot undo that once an image is
          sent. Please only upload an image you are comfortable handing over on
          those terms — and not one that shows someone who has not agreed to it.
        </p>
      </div>

      <div className="upload-acknowledge">
        <input
          type="checkbox"
          id={acknowledgeId}
          checked={acknowledged}
          onChange={(event) => setAcknowledged(event.target.checked)}
          aria-describedby={disclosureId}
        />
        <label htmlFor={acknowledgeId}>
          I have the right to use these images, and I understand they will be sent
          to the AI image provider on the terms above.
        </label>
      </div>

      <p className="field-help" id={helpId}>
        {/* The affirmation stays ticked between uploads, so it has to be clear
            it covers each image and not just the first one. */}
        This applies to every image you choose. JPEG, PNG or WebP, up to 15 MB.{" "}
        {full
          ? "You have used all of your inspiration slots."
          : `${slotsRemaining} of your inspiration slots ${
              slotsRemaining === 1 ? "is" : "are"
            } free.`}
      </p>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED}
        className="upload-input"
        id={`${acknowledgeId}-file`}
        onChange={onSelect}
        disabled={!canChoose}
        aria-describedby={helpId}
      />
      <label className="upload-label" htmlFor={`${acknowledgeId}-file`}>
        Choose an image
      </label>

      {/* Announced, not merely displayed: a screen-reader user gets no visual
          cue that an upload finished, failed, or that a slot freed up. */}
      <p
        className={
          status.kind === "error" ? "upload-status upload-status-error" : "upload-status"
        }
        role="status"
      >
        {status.kind === "uploading" && `Uploading ${status.name}…`}
        {status.kind === "added" && "Image added to your design."}
        {status.kind === "removed" && "Image removed from your design."}
        {status.kind === "error" && status.message}
      </p>

      {uploads.length > 0 && (
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
