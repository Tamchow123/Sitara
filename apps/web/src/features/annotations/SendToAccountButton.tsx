"use client";

// "Send to account" — the button that replaced Download.
//
// There is no address field, and there is no address parameter to pass: the
// endpoint resolves the recipient from the signed-in account server-side. The
// confirmation may NAME that address, because the client already holds it from
// `/auth/me` — it is telling the user something they gave us, not revealing
// something new. Nothing about the send is written to storage.
//
// Every refusal is stated honestly rather than as a generic failure: signed out,
// feature off, slow down, and image-not-ready are four different situations and
// only one of them is the user's to fix.

import { useEffect, useRef, useState } from "react";

import { SEND_FLASH_MS } from "./limits";
import { sendRenderToAccount, type RenderSendKind } from "@/lib/api";

type Props = {
  designId: string;
  versionId: string;
  kind: RenderSendKind;
  label: string;
  /** Null when the workspace owner is anonymous, which disables the control. */
  accountEmail: string | null;
  className?: string;
};

type SendState =
  | { status: "idle" }
  | { status: "sending" }
  | { status: "sent" }
  | { status: "refused"; message: string };

export function SendToAccountButton({
  designId,
  versionId,
  kind,
  label,
  accountEmail,
  className = "btn btn-secondary",
}: Props) {
  const [send, setSend] = useState<SendState>({ status: "idle" });
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (flashTimer.current) clearTimeout(flashTimer.current);
    },
    [],
  );

  const signedOut = accountEmail === null;

  async function onClick() {
    // `sent` as well as `sending`: for the length of the flash the button READS
    // "Sent to your email ✓", and a second press on a button in that state is
    // almost certainly a mis-click. It would also be answered by the endpoint's
    // already-terminal 202 — a success the user cannot distinguish from a second
    // real send.
    if (send.status === "sending" || send.status === "sent") return;
    setSend({ status: "sending" });
    const result = await sendRenderToAccount(designId, versionId, kind);

    if (result.ok) {
      setSend({ status: "sent" });
      if (flashTimer.current) clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setSend({ status: "idle" }), SEND_FLASH_MS);
      return;
    }
    setSend({ status: "refused", message: result.message });
  }

  if (signedOut) {
    return (
      <p className="annotation-send-disabled">
        <button type="button" className={className} disabled>
          {label}
        </button>
        <span>
          Sign in to send this to your email.{" "}
          <a href="/login">Sign in</a>
        </span>
      </p>
    );
  }

  return (
    <p className="annotation-send">
      <button
        type="button"
        className={className}
        onClick={() => void onClick()}
        disabled={send.status === "sending" || send.status === "sent"}
      >
        <PlaneGlyph />
        {send.status === "sent" ? "Sent to your email ✓" : label}
      </button>

      {/* Polite, so the confirmation is announced without interrupting whatever
          the user is doing next. The address is the account's own. */}
      <span className="annotation-send-live" role="status" aria-live="polite">
        {send.status === "sent" ? `Sent to ${accountEmail}.` : ""}
        {send.status === "refused" ? send.message : ""}
      </span>
    </p>
  );
}

function PlaneGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
      <path d="M3 11.5 21 3l-8.5 18-2.2-7.3L3 11.5Z" fill="currentColor" />
    </svg>
  );
}
