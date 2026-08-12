"use client";

// The reference step (Phase 16B ADR 0018/0019; rebuilt in Phase 22, ADR 0025).
//
// Since the curated catalogue was retired this screen holds one thing: the
// customer's own photographs. There is no grid to browse, no shared budget to
// arithmetic against, and no catalogue request to fail — the three reference
// slots are the uploads' alone.
//
// A reference is private user content, not catalogue content: it is never
// listed, never shown to another session, and can never be promoted into the
// catalogue. Its rights position is ONE per-upload affirmation by the person
// choosing the image — deliberately weaker than the staff-verified catalogue
// model that just left the product, and never to be presented as verified
// rights. Removing the stronger model does not upgrade this one.
//
// The affirmation is gated behind the ADR 0019 disclosure, which must be
// readable BEFORE any way of adding a photograph is usable, because that
// decision sends the bytes of a chosen reference to an image provider whose
// terms take a perpetual, irrevocable licence over inputs. A user cannot
// consent to something the interface has not told them.

import { useId, useState } from "react";

import {
  inspirationUploadImageUrl,
  removeInspirationUpload,
  uploadInspirationImage,
  type InspirationUpload as Upload,
} from "@/lib/api";

import { PhoneHandoff } from "./PhoneHandoff";
import { RIGHTS_AFFIRMATION_LABEL, RightsDisclosure } from "./RightsDisclosure";

const ACCEPTED = "image/jpeg,image/png,image/webp";

type Props = {
  // Absent until the first autosave has created the draft. The ways in are
  // disabled rather than hidden in that window, so the screen still explains
  // itself instead of appearing to offer nothing.
  designId?: string;
  uploads: Upload[];
  /** The whole reference budget — this screen is the only thing drawing on it. */
  max: number;
  onChange: (uploads: Upload[]) => void;
};

type Status =
  | { kind: "idle" }
  | { kind: "uploading" }
  | { kind: "added" }
  | { kind: "removed" }
  | { kind: "error"; message: string };

export function InspirationUpload({ designId, uploads, max, onChange }: Props) {
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [acknowledged, setAcknowledged] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const disclosureId = useId();
  const acknowledgeId = useId();
  const helpId = useId();
  const fileInputId = `${acknowledgeId}-file`;
  const cameraInputId = `${acknowledgeId}-camera`;

  const slotsRemaining = Math.max(max - uploads.length, 0);
  const full = slotsRemaining <= 0;
  const uploading = status.kind === "uploading";
  const canAdd = Boolean(designId) && acknowledged && !full && !uploading;

  const handleFile = async (file: File): Promise<void> => {
    if (!designId) return;
    setStatus({ kind: "uploading" });
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
    if (!canAdd) return;
    void handleFile(file);
  };

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

      <p className="field-help">
        Add up to {max} of your own photographs, if you have any — a saree you
        love, a colour you keep coming back to, a photograph of your sister at
        her wedding. This step is optional. Your questionnaire answers stay
        authoritative, and the concept will not be an exact copy of anything you
        add.
      </p>

      <p className="field-help" id={helpId}>
        {full
          ? "You have used all of your reference slots."
          : `${slotsRemaining} of your ${max} reference slots ${
              slotsRemaining === 1 ? "is" : "are"
            } free.`}
      </p>

      <div className="upload-ways" role="group" aria-labelledby={`${helpId}-ways`}>
        <h3 className="upload-ways-heading" id={`${helpId}-ways`}>
          Ways to add a photograph
        </h3>

        {/* First and dominant, because it is the normal case rather than the
            fallback: the picture is already on the customer's own phone.

            Deliberately OUTSIDE the affirmation below. The phone takes its own
            affirmation from the person choosing the photograph, and gating the
            QR behind this device's checkbox would say the opposite — that
            whoever is holding the shop's screen can consent on her behalf. That
            is exactly the substitution ADR 0026 exists to prevent. */}
        {designId && (
          <PhoneHandoff
            designId={designId}
            uploads={uploads}
            max={max}
            onUploadsChanged={onChange}
          />
        )}

        <h4 className="upload-ways-heading" id={`${helpId}-own`}>
          Or add one from this device
        </h4>

        <RightsDisclosure id={disclosureId} scope="own-device" />

        <div className="upload-acknowledge">
          <input
            type="checkbox"
            id={acknowledgeId}
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
            aria-describedby={disclosureId}
          />
          <label htmlFor={acknowledgeId}>{RIGHTS_AFFIRMATION_LABEL}</label>
        </div>

        <p className="field-help" id={`${helpId}-own-help`}>
          {/* The affirmation stays ticked between photographs, so it has to be
              clear it covers each one and not just the first. */}
          This applies to every image you add here. JPEG, PNG or WebP, up to 15 MB.
        </p>

        {/* `capture` asks the device for its rear camera. It is a HINT: a
            browser that does not honour it opens an ordinary file picker
            instead, which is why the control is labelled by what the person
            gets ("Take a photo") and why both inputs run the same handler —
            whichever dialogue opens, the result is one chosen file.

            The accept list is the same narrow one as the file picker rather
            than `image/*`. On iOS that is what makes the camera hand back a
            JPEG instead of the HEIC the device stores natively, and HEIC is a
            format the sanitiser refuses. Widening this to `image/*` would turn
            "take a photo" into "take a photo and be told it is not an
            image". */}
        <div className="upload-way">
          <input
            type="file"
            accept={ACCEPTED}
            capture="environment"
            className="upload-input"
            id={cameraInputId}
            onChange={onSelect}
            disabled={!canAdd}
            aria-describedby={`${helpId}-own-help`}
          />
          <label className="upload-label" htmlFor={cameraInputId}>
            Take a photo
          </label>
        </div>

        <div className="upload-way">
          <input
            type="file"
            accept={ACCEPTED}
            className="upload-input"
            id={fileInputId}
            onChange={onSelect}
            disabled={!canAdd}
            aria-describedby={`${helpId}-own-help`}
          />
          <label className="upload-label" htmlFor={fileInputId}>
            Choose a file
          </label>
        </div>
      </div>

      {!designId && (
        <p className="field-help">
          Answer a question first — your design has to exist before a photograph
          can be attached to it.
        </p>
      )}

      {/* Announced, not merely displayed: a screen-reader user gets no visual
          cue that an upload finished, failed, or that a slot freed up.

          Named, because the handoff panel above has a live region of its own
          and two unnamed ones on a single screen leave a screen-reader user
          unable to tell which just spoke. */}
      <p
        className={
          status.kind === "error" ? "upload-status upload-status-error" : "upload-status"
        }
        role="status"
        aria-label="Photographs added from this device"
      >
        {status.kind === "uploading" && "Adding your photograph…"}
        {status.kind === "added" && "Image added to your design."}
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
