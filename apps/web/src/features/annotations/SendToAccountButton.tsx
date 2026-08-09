"use client";

// "Send to account" — the button that replaced Download.
//
// There is no address field, and there is no address parameter to pass: the
// endpoint resolves the recipient from the signed-in account server-side. The
// confirmation may NAME that address, because the client already holds it from
// `/auth/me` — it is telling the user something they gave us, not revealing
// something new. Nothing about the send is written to storage.
//
// Since Phase 21 the press opens a prompt first, because the file arriving in
// someone's inbox called `sitara-concept.png` is useless the moment they have two
// of them. The one value the caller may choose is the FILE NAME. It is pre-filled
// from the name they last chose for this exact render, else their design's title,
// and NEVER from an annotation note (CLAUDE.md §7 — a note is the most personal
// free text in the product, and a file name travels in the message headers).
//
// Every refusal is stated honestly rather than as a generic failure: signed out,
// feature off, slow down, image-not-ready, the name itself, and the render's
// lifetime allowance being spent are six different situations, and only two of
// them are the user's to fix — in two different places.

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { MAX_SEND_FILENAME_LENGTH, SEND_FLASH_MS } from "./limits";
import { ModalDialog } from "./ModalDialog";
import {
  fetchRenderSendState,
  sendRenderToAccount,
  type RenderSendKind,
  type RenderSendState,
} from "@/lib/api";

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
  | { status: "naming" }
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
  const [allowance, setAllowance] = useState<RenderSendState | null>(null);
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState("");
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const nameFieldRef = useRef<HTMLInputElement>(null);
  const nameFieldId = useId();
  const nameHintId = useId();
  const nameErrorId = useId();

  const signedOut = accountEmail === null;

  useEffect(
    () => () => {
      if (flashTimer.current) clearTimeout(flashTimer.current);
    },
    [],
  );

  const readAllowance = useCallback(async () => {
    // Null on any failure, deliberately: this only pre-fills a field and shows a
    // remaining count. A concept the user can see must never become unsendable
    // because a convenience read failed, so the dialog opens either way.
    const state = await fetchRenderSendState(designId, versionId, kind);
    setAllowance(state);
    return state;
  }, [designId, versionId, kind]);

  useEffect(() => {
    // Read up front, not on the press, so the remaining count is visible BEFORE
    // the last send is spent rather than only in the refusal that follows it.
    // Skipped while signed out, where the control is disabled anyway and the
    // endpoint has nothing to say.
    if (signedOut) return;
    let live = true;
    void fetchRenderSendState(designId, versionId, kind).then((state) => {
      if (live) setAllowance(state);
    });
    return () => {
      live = false;
    };
  }, [designId, versionId, kind, signedOut]);

  const spent = allowance !== null && allowance.used >= allowance.limit;
  const remaining = allowance === null ? null : Math.max(0, allowance.limit - allowance.used);

  async function openPrompt() {
    if (send.status === "sending") return;
    // Re-read on open as well as on mount, because another tab — or the same
    // person on their phone — may have spent an allowance since this page loaded,
    // and pre-filling from a stale read is how the field offers a name the user
    // already used.
    const fresh = await readAllowance();
    setName((fresh ?? allowance)?.suggestedFilename ?? "");
    setSend({ status: "naming" });
  }

  async function confirmSend() {
    setSend({ status: "sending" });
    const result = await sendRenderToAccount(designId, versionId, kind, name);

    if (result.ok) {
      setSend({ status: "sent" });
      // Optimistic, then corrected by the real read below: the count has to move
      // immediately or a second press can be made before the server's answer
      // arrives, on a button still showing the old number.
      setAllowance((current) =>
        current === null ? null : { ...current, used: current.used + 1, suggestedFilename: name },
      );
      void readAllowance();
      if (flashTimer.current) clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setSend({ status: "idle" }), SEND_FLASH_MS);
      return;
    }

    if (result.kind === "name_refused") {
      // Answered where the name was typed. The dialog STAYS OPEN — closing it
      // would throw away what they wrote and leave them to guess what was wrong.
      setSend({ status: "naming" });
      setNameError(result.message);
      return;
    }

    setNameError("");
    void readAllowance();
    setSend({ status: "refused", message: result.message });
  }

  if (signedOut) {
    return (
      <p className="annotation-send-disabled">
        <button type="button" className={className} disabled>
          {label}
        </button>
        <span>
          Sign in to send this to your email. <a href="/login">Sign in</a>
        </span>
      </p>
    );
  }

  return (
    <p className="annotation-send">
      <button
        type="button"
        className={className}
        onClick={() => void openPrompt()}
        disabled={send.status === "sending" || send.status === "sent" || spent}
      >
        <PlaneGlyph />
        {send.status === "sent" ? "Sent to your email ✓" : label}
      </button>

      {/* The count, before the ceiling is reached rather than only in the refusal
          that follows it. Rendered as text and not as a colour or a bar, because
          "one left" is the whole message. */}
      {remaining !== null && send.status !== "sent" && (
        <span className="annotation-send-allowance">
          {spent
            ? `You have emailed this ${allowance?.limit} times, which is the maximum.`
            : `${remaining} of ${allowance?.limit} email${allowance?.limit === 1 ? "" : "s"} left for this image.`}
        </span>
      )}

      {/* Polite, so the confirmation is announced without interrupting whatever
          the user is doing next. The address is the account's own. */}
      <span className="annotation-send-live" role="status" aria-live="polite">
        {send.status === "sent" ? `Sent to ${accountEmail}.` : ""}
        {send.status === "refused" ? send.message : ""}
      </span>

      {(send.status === "naming" || send.status === "sending") && (
        <ModalDialog
          role="dialog"
          title="Name this file"
          initialFocusRef={nameFieldRef}
          onClose={() => {
            setNameError("");
            setSend({ status: "idle" });
          }}
          body={
            <div className="field">
              <label htmlFor={nameFieldId}>File name</label>
              <input
                id={nameFieldId}
                ref={nameFieldRef}
                className="input"
                type="text"
                value={name}
                maxLength={MAX_SEND_FILENAME_LENGTH}
                autoComplete="off"
                spellCheck={false}
                // Both ids always, so the hint does not stop being announced the
                // moment an error appears beside it.
                aria-describedby={nameError ? `${nameHintId} ${nameErrorId}` : nameHintId}
                aria-invalid={nameError ? true : undefined}
                onChange={(event) => {
                  setName(event.target.value);
                  setNameError("");
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void confirmSend();
                  }
                }}
              />
              {/* Stated before they type, not after. ADR 0021 records this
                  exposure as ACCEPTED — the wording must never suggest it has been
                  removed or mitigated. */}
              <span className="field-hint" id={nameHintId}>
                It arrives as a .png. Whatever you type goes into the email&rsquo;s own
                headers, so your mail provider and your inbox keep it — outside
                Sitara&rsquo;s control. Leave it as it is to reuse the last name you chose.
              </span>
              {nameError && (
                <span className="field-error" id={nameErrorId} role="alert">
                  {nameError}
                </span>
              )}
            </div>
          }
        >
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => {
              setNameError("");
              setSend({ status: "idle" });
            }}
            disabled={send.status === "sending"}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void confirmSend()}
            disabled={send.status === "sending"}
          >
            {send.status === "sending" ? "Sending…" : "Send to my email"}
          </button>
        </ModalDialog>
      )}
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
