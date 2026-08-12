"use client";

// The customer's phone (Phase 22, ADR 0026).
//
// Reached by scanning a QR code on a shop's iPad. Everything about this page is
// shaped by who opens it: someone who has never seen Sitara, on their own
// phone, standing in a shop, who wants to send one picture and be done.
//
// - **No account, no app, no sign-in.** The scanned code IS the authorisation.
// - **One tap to the photo library.** That is where the picture already is; the
//   camera is the secondary control, not the primary one.
// - **The full ADR 0019 disclosure, before the picker is usable**, in the same
//   words as the iPad — shared from `RightsDisclosure` so it cannot be quietly
//   shortened for the smaller screen. The affirmation is taken HERE, from the
//   person choosing the photograph. A tick on the shop's screen does not carry
//   across, and the server refuses an upload without this one.
// - **Plain HTML controls only.** A several-year-old iOS or Android browser is
//   what walks into a shop.
//
// The secret arrives in the URL FRAGMENT (`/r#<token>`). Browsers never send a
// fragment to a server, so it reaches no access log, no Referer header and no
// proxy on the way here. It is read into memory once and never written to
// localStorage, sessionStorage, IndexedDB or a cookie: it is a bearer
// credential, and that exposure is accepted and bounded, not removed — keeping
// the only copy somewhere that dies with the tab is part of the bound.

import { useEffect, useId, useState } from "react";

import { uploadReferenceByGrant } from "@/lib/api";
import {
  RIGHTS_AFFIRMATION_LABEL,
  RightsDisclosure,
} from "@/features/questionnaire/RightsDisclosure";

const ACCEPTED = "image/jpeg,image/png,image/webp";

type Status =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "sent" }
  | { kind: "error"; message: string };

export default function PhoneReferencePage() {
  // `undefined` while the fragment has not been read yet — on the very first
  // client render there is no window, and treating "not yet known" as "no code"
  // would flash a "this link is not usable" screen at every scan.
  const [token, setToken] = useState<string | undefined>(undefined);
  const [acknowledged, setAcknowledged] = useState(false);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  // Its own state rather than a field on `status`, because sending the second
  // photograph passes through `{ kind: "sending" }` and would otherwise drop
  // the tally of the first.
  const [sentCount, setSentCount] = useState(0);
  const disclosureId = useId();
  const acknowledgeId = useId();
  const helpId = useId();
  const libraryInputId = `${acknowledgeId}-library`;
  const cameraInputId = `${acknowledgeId}-camera`;

  useEffect(() => {
    // `slice(1)` drops the leading '#'. Nothing normalises or re-encodes it:
    // the token is url-safe base64 by construction, and a code that arrives
    // mangled should be refused by the server rather than repaired here.
    setToken(window.location.hash.slice(1));
  }, []);

  const sending = status.kind === "sending";
  // Nothing here knows how many slots are left, and that is deliberate: the
  // server will not say, because a count would let this page work out how many
  // references the design already had. Capacity is learned by trying — a
  // further upload either succeeds or comes back as the one "no longer usable".
  const canSend = Boolean(token) && acknowledged && !sending;

  const send = async (file: File): Promise<void> => {
    if (!token) return;
    setStatus({ kind: "sending" });
    let result;
    try {
      result = await uploadReferenceByGrant(token, file, acknowledged);
    } catch {
      setStatus({
        kind: "error",
        message: "The photograph did not finish sending. Check your signal and try again.",
      });
      return;
    }
    if (result.ok) {
      setSentCount((previous) => previous + 1);
      setStatus({ kind: "sent" });
      return;
    }
    setStatus({ kind: "error", message: result.message });
  };

  const onSelect = (event: React.ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    // Cleared immediately so choosing the SAME photograph again still fires a
    // change event — otherwise a retry after a failure appears to do nothing.
    event.target.value = "";
    if (!file) return;
    // Checked explicitly rather than relying on the disabled attribute: this is
    // the consent gate for handing someone's photograph to an external
    // provider, and it should not rest on a DOM property. The server refuses an
    // unaffirmed upload too.
    if (!canSend) return;
    void send(file);
  };

  if (token === undefined) {
    return (
      <main className="phone-page">
        <p className="field-help">Opening…</p>
      </main>
    );
  }

  if (!token) {
    return (
      <main className="phone-page">
        <h1 className="phone-heading">Nothing to send to</h1>
        <p className="field-help">
          This page needs a code from the shop&apos;s screen. Scan the square
          code again — if it still does not work, ask for a new one.
        </p>
      </main>
    );
  }

  return (
    <main className="phone-page">
      <h1 className="phone-heading">Send a photograph</h1>
      <p className="field-help">
        This goes straight to the design being put together for you. Pictures of
        anything you like the look of are useful — a saree, a colour, a neckline,
        a photograph from a wedding.
      </p>

      <RightsDisclosure id={disclosureId} scope="handoff" />

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

      <p className="field-help" id={helpId}>
        This applies to every photograph you send. JPEG, PNG or WebP, up to 15 MB.
      </p>

      {/* The library first and styled as the primary control: the picture is
          already on the phone, and that is the whole reason this page exists.
          The camera is the second option, not the first.

          The accept list is the narrow one rather than `image/*`. On iOS that
          is what makes the picker hand back a JPEG instead of the HEIC the
          device stores natively, and HEIC is a format the sanitiser refuses. */}
      <div className="phone-ways">
        <div className="upload-way">
          <input
            type="file"
            accept={ACCEPTED}
            className="upload-input"
            id={libraryInputId}
            onChange={onSelect}
            disabled={!canSend}
            aria-describedby={helpId}
          />
          <label className="phone-label phone-label-primary" htmlFor={libraryInputId}>
            Choose from your photos
          </label>
        </div>

        <div className="upload-way">
          <input
            type="file"
            accept={ACCEPTED}
            capture="environment"
            className="upload-input"
            id={cameraInputId}
            onChange={onSelect}
            disabled={!canSend}
            aria-describedby={helpId}
          />
          <label className="phone-label" htmlFor={cameraInputId}>
            Take a photo now
          </label>
        </div>
      </div>

      <p
        className={
          status.kind === "error" ? "upload-status upload-status-error" : "upload-status"
        }
        role="status"
      >
        {status.kind === "sending" && "Sending…"}
        {status.kind === "sent" &&
          `Sent — ${sentCount} photograph${sentCount === 1 ? "" : "s"} so far. Send another if you would like to.`}
        {status.kind === "error" && status.message}
      </p>

      {/* No gallery of what has been sent, and no way to remove one. A grant
          grants upload and nothing else (ADR 0026): showing this phone what is
          on the design would be a read capability, which is a permanent
          non-goal. Removing a photograph is the stylist's job, on the iPad. */}
      <p className="field-help">
        You will see the photographs appear on the shop&apos;s screen. Ask there
        if you want one taken off again.
      </p>
    </main>
  );
}
