"use client";

// The shop's side of the phone handoff (Phase 22, ADR 0026).
//
// The customer scans this QR with her own phone and sends the photographs that
// are already on it. Since the iPad's camera and picker were removed it is the
// ONLY way references arrive — a customer walks in with a screenshot from
// Instagram or a photograph of her sister's wedding, and asking her to email it
// to the shop or hand over her unlocked phone is the failure mode this
// replaces.
//
// The code shows itself as soon as the step opens. Nothing is behind a button
// that would only ever be pressed.
//
// Three things this component is responsible for keeping true:
//
// 1. **No typing.** The QR carries the whole URL. The code is shown as text
//    only as a fallback for a phone whose camera app will not scan.
// 2. **The secret lives in memory and nowhere else.** It is React state, it
//    dies with the tab, and it is never written to browser storage, never put
//    in this page's URL, and never logged. It is a bearer credential — that
//    exposure is accepted and bounded, not removed.
// 3. **The stylist can see arrivals, and can STOP them and know that she
//    did.** The phase calls this grant "genuinely revocable, because it
//    resolves through Sitara", unlike a signed storage URL. A stop that
//    silently failed while the panel showed success would make that claim
//    false at the one moment it is relied on — the hand-over between one
//    customer and the next — so a stop is confirmed before it is believed.

import { useCallback, useEffect, useId, useRef, useState } from "react";

import {
  fetchDesignReferences,
  revokeReferenceGrants,
  type InspirationUpload as Upload,
} from "@/lib/api";

import {
  claimGrant,
  clearStopped,
  holdsGrant,
  isStopped,
  markStopped,
  mintShared,
  releaseGrant,
} from "./handoff-coordination";
import { QrCode } from "./QrCode";

/** How often the iPad asks whether a photograph has arrived.
 *
 * A plain interval, deliberately: CLAUDE.md §17 confines TanStack Query to the
 * generation-progress flow, and the phase decided against widening it for a
 * panel that is open for a minute or two. Two seconds is the spec's "within a
 * couple of seconds" against a handful of rows on the shop's own design. */
const POLL_INTERVAL_MS = 2000;

/** Stop offering the code slightly before the server stops honouring it.
 *
 * The server is the authority on expiry and this countdown is only a courtesy,
 * but the two clocks are a shop iPad's and a server's, and nothing enforces NTP
 * on the former. Erring early costs a re-mint; erring late sends a customer to
 * a page that refuses her while the iPad still shows time remaining, which
 * looks like her phone's fault. */
const EXPIRY_SAFETY_MARGIN_MS = 10_000;

type Props = {
  designId: string;
  /** Uploads already on the design, so the panel can say what is still free. */
  uploads: Upload[];
  max: number;
  /** Called when polling finds the list has changed. */
  onUploadsChanged: (uploads: Upload[]) => void;
};

type Handoff =
  | { kind: "idle" }
  | { kind: "minting" }
  | { kind: "live"; token: string; expiresAt: number }
  | { kind: "stopping" }
  | { kind: "stop-failed" }
  | { kind: "expired" }
  | { kind: "error"; message: string };

function secondsLeft(expiresAt: number): number {
  return Math.max(Math.ceil((expiresAt - EXPIRY_SAFETY_MARGIN_MS - Date.now()) / 1000), 0);
}

function formatRemaining(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes <= 0) return `${rest} second${rest === 1 ? "" : "s"}`;
  return `${minutes} minute${minutes === 1 ? "" : "s"}`;
}

export function PhoneHandoff({ designId, uploads, max, onUploadsChanged }: Props) {
  // Starts at "minting", not "idle", when there is room: the effect below mints
  // on mount, so the first paint would otherwise flash a "Show the code" button
  // that nobody is meant to press. "Creating a code…" is what is actually
  // happening.
  // Must agree with the auto-show effect's own conditions, or the panel paints
  // "Creating a code…" for a mint that is never going to run.
  const [handoff, setHandoff] = useState<Handoff>(() =>
    max - uploads.length > 0 && !isStopped(designId)
      ? { kind: "minting" }
      : { kind: "idle" },
  );
  const [remaining, setRemaining] = useState(0);
  const [arrived, setArrived] = useState(0);
  const headingId = useId();
  const statusId = useId();

  const slotsFree = Math.max(max - uploads.length, 0);
  const live = handoff.kind === "live";

  // The count at the moment the code went live, so "2 photographs arrived" is
  // about THIS handoff rather than the design's whole history.
  const baselineRef = useRef(uploads.length);
  // Read inside the poll without making the interval depend on it — otherwise
  // every arriving photograph would tear down and rebuild the timer.
  const latestRef = useRef(onUploadsChanged);
  useEffect(() => {
    latestRef.current = onUploadsChanged;
  }, [onUploadsChanged]);

  // A REF, not the `handoff` state, because two clicks dispatched before React
  // commits the first one's render both close over the same state value — the
  // button disappearing is a consequence of the re-render, not a lock. Without
  // this a double-tapped button on a laggy iPad mints twice, and the second
  // response can overwrite the first's QR while the customer is mid-scan.
  const busyRef = useRef(false);
  // This mount's identity, used to claim and release the design's live grant.
  // A symbol rather than a counter so two mounts can never collide.
  const mineRef = useRef<symbol>(Symbol("handoff"));
  // Monotonic poll sequence. `cancelled` below only covers teardown; it does
  // nothing about two in-flight polls resolving out of order, which would let
  // an older response overwrite a newer one and visibly regress the count.
  const pollSeqRef = useRef(0);
  const appliedSeqRef = useRef(0);

  const start = useCallback(async (): Promise<void> => {
    if (busyRef.current) return;
    busyRef.current = true;
    // Any start at all — the automatic one or a deliberate press — is a code
    // being asked for, so an earlier stop stops standing in the way.
    clearStopped(designId);
    setHandoff({ kind: "minting" });
    setArrived(0);
    baselineRef.current = uploads.length;
    try {
      const result = await mintShared(designId);
      if (!result.ok) {
        setHandoff({ kind: "error", message: result.message });
        return;
      }
      const expiresAt = Date.parse(result.grant.expires_at);
      if (!Number.isFinite(expiresAt)) {
        setHandoff({
          kind: "error",
          message: "A code could not be created just now. Please try again.",
        });
        return;
      }
      claimGrant(designId, mineRef.current);
      setHandoff({ kind: "live", token: result.grant.token, expiresAt });
      setRemaining(secondsLeft(expiresAt));
    } catch {
      setHandoff({
        kind: "error",
        message: "A code could not be created just now. Please try again.",
      });
    } finally {
      busyRef.current = false;
    }
  }, [designId, uploads.length]);

  const stop = useCallback(async (): Promise<void> => {
    if (busyRef.current) return;
    busyRef.current = true;
    // "stopping", not "idle". The panel must not present a stop it has not had
    // confirmed: the stylist reads the idle screen as "that code is dead" and
    // hands the iPad to the next customer on the strength of it.
    setHandoff({ kind: "stopping" });
    try {
      const result = await revokeReferenceGrants(designId);
      if (!result.ok) {
        setHandoff({ kind: "stop-failed" });
        return;
      }
      releaseGrant(designId, mineRef.current);
      markStopped(designId);
      setArrived(0);
      setHandoff({ kind: "idle" });
    } catch {
      setHandoff({ kind: "stop-failed" });
    } finally {
      busyRef.current = false;
    }
  }, [designId]);

  // Countdown. The server decides expiry; this only stops OFFERING a code the
  // server would soon refuse, so the customer is not sent to a dead page.
  useEffect(() => {
    if (handoff.kind !== "live") return;
    const expiresAt = handoff.expiresAt;
    const tick = window.setInterval(() => {
      const left = secondsLeft(expiresAt);
      setRemaining(left);
      if (left <= 0) setHandoff({ kind: "expired" });
    }, 1000);
    return () => window.clearInterval(tick);
  }, [handoff]);

  // Poll for arrivals, only while a code is live.
  useEffect(() => {
    if (!live) return;
    let cancelled = false;
    appliedSeqRef.current = 0;
    pollSeqRef.current = 0;
    const poll = async (): Promise<void> => {
      const seq = (pollSeqRef.current += 1);
      let found;
      try {
        // The narrow references read, not the whole design: this runs every
        // two seconds for the life of the code, and the questionnaire schema
        // it used to drag along cannot have changed between two of them.
        found = await fetchDesignReferences(designId);
      } catch {
        // A dropped poll is not worth telling the stylist about: the next one
        // is two seconds away and the customer's upload already succeeded or
        // failed on its own screen.
        return;
      }
      if (cancelled) return;
      // Discard a response that started earlier than one already applied. On a
      // shop's variable wifi — with a phone uploading several megabytes at the
      // same time — a slow first poll can land after a fast second one and drag
      // the arrival count backwards.
      if (seq <= appliedSeqRef.current) return;
      appliedSeqRef.current = seq;
      latestRef.current(found);
      setArrived(Math.max(found.length - baselineRef.current, 0));
    };
    const timer = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [live, designId]);

  // A code outlives its usefulness the moment the design is full.
  useEffect(() => {
    if (live && slotsFree <= 0) void stop();
  }, [live, slotsFree, stop]);

  // Show a code as soon as the step opens, rather than behind a button.
  //
  // The phone IS the way references arrive now, so making the stylist tap
  // "show the code" first was a step that only ever had one answer. The cost is
  // a grant minted on every visit to this step; that is bounded by everything
  // ADR 0026 already bounds it with — a short TTL, at most one live grant per
  // design (a re-mint revokes the previous code), revocation on leaving the
  // step, and the mint throttles.
  //
  // Guarded by a ref rather than by `handoff.kind === "idle"`, because idle is
  // also where an explicit "Stop accepting photos" lands. Without the ref the
  // stop button would mint a fresh code the instant it succeeded, which is the
  // opposite of what the stylist just asked for — and ADR 0026 accepts the
  // bearer exposure specifically on revocation meaning what it says.
  const autoShownRef = useRef(false);
  useEffect(() => {
    if (isStopped(designId)) return;
    if (autoShownRef.current) return;
    if (slotsFree <= 0) return;
    autoShownRef.current = true;
    void start();
  }, [designId, slotsFree, start]);

  // Leaving the step must not leave a photographed code working. A cleanup
  // cannot show an error, so this is the one place a failed revoke is silent —
  // it is a backstop under the explicit control above, not a replacement for
  // it, and the code still expires on its own.
  //
  // Only what this mount still HOLDS, though. A revoke is design-scoped: it
  // kills whatever is live, not one named grant. So a departing mount that
  // revoked unconditionally could reach the server after a newer mount's mint
  // and kill the code that had already replaced its own — leaving a QR on
  // screen that no phone can use. A mount whose own mint was still in flight
  // holds nothing: the mount that joined that same mint owns the result, and
  // will revoke it when IT leaves.
  const mine = mineRef.current;
  useEffect(() => {
    return () => {
      if (!holdsGrant(designId, mine)) return;
      releaseGrant(designId, mine);
      // Caught, not floated: this runs on every departure from the step now, so
      // an unhandled rejection here would be routine noise in the console and
      // in Sentry rather than a rare artefact.
      void revokeReferenceGrants(designId).catch(() => {});
    };
  }, [designId, mine]);

  const url =
    handoff.kind === "live" && typeof window !== "undefined"
      ? // The secret rides in the FRAGMENT. Browsers never send a fragment to a
        // server, so it cannot land in an access log, a Referer header or a
        // proxy trace on the way to the phone's own page. (Sentry's browser SDK
        // would otherwise attach the whole page URL to an event — see
        // `lib/sentry-scrub.ts`, which cuts at the first `?` or `#`.)
        `${window.location.origin}/r#${handoff.token}`
      : "";

  return (
    <section className="handoff" aria-labelledby={headingId}>
      <h3 className="handoff-heading" id={headingId}>
        Send from your phone
      </h3>
      <p className="field-help">
        Scan this with your phone&apos;s camera — no app, no sign-in, nothing to
        type.
      </p>

      {/* Only reachable once a code has been deliberately stopped, or while the
          design is full — the code shows itself otherwise. Labelled for that
          situation rather than for a first visit. */}
      {handoff.kind === "idle" && (
        <button
          type="button"
          className="handoff-start"
          onClick={() => void start()}
          disabled={slotsFree <= 0}
        >
          Show a new code
        </button>
      )}

      {slotsFree <= 0 && handoff.kind === "idle" && (
        <p className="field-help">
          All {max} reference slots are used. Remove one to accept another
          photograph.
        </p>
      )}

      {handoff.kind === "minting" && <p className="handoff-status">Creating a code…</p>}
      {handoff.kind === "stopping" && <p className="handoff-status">Stopping…</p>}

      {handoff.kind === "live" && (
        <div className="handoff-live">
          <QrCode value={url} label="Scan this to send a photograph from your phone" />

          <p className="handoff-expiry">
            This code works for about {formatRemaining(remaining)}.
          </p>

          {/* The typed fallback, for a phone whose camera app will not scan.
              Shown as ordinary selectable text rather than a link: tapping it
              on the iPad would open the customer's page on the wrong device. */}
          <details className="handoff-fallback">
            <summary>The camera will not scan it</summary>
            <p className="field-help">
              Type this into the phone&apos;s browser instead. It is long — the
              scan is much easier if it can be made to work.
            </p>
            <p className="handoff-url">{url}</p>
          </details>

          <button type="button" className="handoff-stop" onClick={() => void stop()}>
            Stop accepting photos
          </button>
        </div>
      )}

      {handoff.kind === "stop-failed" && (
        <div className="handoff-live">
          {/* Said plainly, because the stylist is about to act on it. The
              honest state is "we could not confirm", not "stopped". */}
          <p className="handoff-status handoff-status-error">
            The code could not be stopped just now, so it may still work until it
            expires on its own. Try again, or wait a few minutes before handing
            the screen to someone else.
          </p>
          <button type="button" className="handoff-stop" onClick={() => void stop()}>
            Try stopping again
          </button>
        </div>
      )}

      {handoff.kind === "expired" && (
        <div className="handoff-live">
          <p className="handoff-status">
            That code has expired. Codes are short-lived on purpose, so one
            photographed off this screen does not keep working later.
          </p>
          <button type="button" className="handoff-start" onClick={() => void start()}>
            Show a new code
          </button>
        </div>
      )}

      {handoff.kind === "error" && (
        <div className="handoff-live">
          <p className="handoff-status handoff-status-error">{handoff.message}</p>
          <button type="button" className="handoff-start" onClick={() => void start()}>
            Try again
          </button>
        </div>
      )}

      {/* Announced, not merely shown. The stylist may be looking at the
          customer rather than the screen when a photograph lands. */}
      <p
        className="handoff-arrivals"
        id={statusId}
        role="status"
        aria-label="Photographs arriving from a phone"
      >
        {arrived > 0 &&
          `${arrived} photograph${arrived === 1 ? "" : "s"} arrived from the phone.`}
      </p>
    </section>
  );
}
