"use client";

// Single-flight, coalescing draft-save coordinator for the questionnaire
// wizard. Guarantees:
//
// - at most ONE design mutation is in flight at a time;
// - first-design creation is single-flight — rapid changes before creation
//   completes produce exactly one POST, and the returned id is stored in a ref
//   synchronously before queued changes drain;
// - queued changes are coalesced to the newest desired snapshot and sent as a
//   PATCH after the in-flight request settles;
// - a monotonic revision counter means an older snapshot can never overwrite a
//   newer one, and "Saved" shows only when the latest revision is confirmed;
// - the exact latest failed payload is retained for Retry (newest fields win);
// - server-normalised data is applied to local state only at the latest
//   confirmed revision.
//
// No Redux/Zustand/React Query — plain refs plus a little React state for the
// user-visible save status.

import { useCallback, useEffect, useRef, useState } from "react";

import { createDesignDraft, updateDesignDraft } from "./api";
import { useLatest } from "./use-latest";
import { designEnvelopeSchema } from "./validation";
import type { Answers, DesignDraft, FieldErrors } from "./types";
import type { DraftFailure, DraftResult } from "@/lib/api";

const TEXT_DEBOUNCE_MS = 600;

export type SaveState = "idle" | "saving" | "saved" | "error";

// The mutable fields of a draft update. Absent keys are left untouched server
// side, so answer-only changes never re-send (and risk rejecting) selections.
export type DraftPatch = { answers?: Answers; inspiration_asset_ids?: string[] };

type Options = {
  versionId: string;
  onCreated?: (design: DesignDraft) => void;
  onLatestConfirmed?: (design: DesignDraft) => void;
};

const ENVELOPE_FAILURE: DraftFailure = {
  ok: false,
  status: 0,
  code: "invalid_request",
  message: "Your changes could not be prepared to save. Please review your answers.",
};

function isEmpty(patch: DraftPatch): boolean {
  return patch.answers === undefined && patch.inspiration_asset_ids === undefined;
}

export function useDraftSaver({ versionId, onCreated, onLatestConfirmed }: Options) {
  const [designId, setDesignId] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});

  // Coordinator internals (refs so they never lag behind a render).
  const revisionRef = useRef(0); // newest desired revision
  const confirmedRef = useRef(0); // highest revision the server confirmed
  const pendingRef = useRef<DraftPatch>({}); // coalesced not-yet-sent fields
  const pendingRevRef = useRef(0); // revision represented by pendingRef
  const inFlightRef = useRef<Promise<void> | null>(null);
  const failedRef = useRef(false);
  const designIdRef = useRef<string | null>(null);
  const textTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const mountedRef = useRef(true);

  // Render-critical values used by stable async callbacks, synchronised DURING
  // render so the very first save (which can fire before any effect runs)
  // never reads a stale initial value — the empty version id was exactly the
  // hosted-CI initialisation race.
  const versionRef = useLatest(versionId);
  const onCreatedRef = useLatest(onCreated);
  const onLatestConfirmedRef = useLatest(onLatestConfirmed);

  // Separate effect for mounted/unmount cleanup ONLY.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (textTimerRef.current) clearTimeout(textTimerRef.current);
      textTimerRef.current = undefined;
    };
  }, []);

  const set = useCallback((fn: () => void) => {
    // Guard React state updates against a late timer firing after unmount.
    if (mountedRef.current) fn();
  }, []);

  const mergePending = useCallback((patch: DraftPatch) => {
    revisionRef.current += 1;
    pendingRef.current = { ...pendingRef.current, ...patch };
    pendingRevRef.current = revisionRef.current;
  }, []);

  const takePending = useCallback((): { payload: DraftPatch; revision: number } => {
    const payload = pendingRef.current;
    const revision = pendingRevRef.current;
    pendingRef.current = {};
    return { payload, revision };
  }, []);

  const sendOnce = useCallback(
    async (payload: DraftPatch): Promise<DraftResult<DesignDraft>> => {
      const creating = designIdRef.current === null;
      const body: DraftPatch & { questionnaire_version_id?: string } = creating
        ? { questionnaire_version_id: versionRef.current, ...payload }
        : payload;
      // Validate the outgoing envelope shape locally: a malformed body becomes
      // a controlled error and NO request is sent.
      if (!designEnvelopeSchema.safeParse(body).success) {
        return ENVELOPE_FAILURE;
      }
      return creating
        ? createDesignDraft(body)
        : updateDesignDraft(designIdRef.current as string, payload);
    },
    // versionRef is a stable useLatest ref (identity never changes); listed to
    // satisfy exhaustive-deps.
    [versionRef],
  );

  const drain = useCallback((): Promise<void> => {
    if (inFlightRef.current) return inFlightRef.current;
    if (isEmpty(pendingRef.current)) return Promise.resolve();

    set(() => {
      setSaveState("saving");
      setSaveError(null);
    });

    const run = (async () => {
      while (!isEmpty(pendingRef.current)) {
        const { payload, revision } = takePending();
        const creating = designIdRef.current === null;
        const result = await sendOnce(payload);
        if (result.ok) {
          if (creating) {
            // Store the id SYNCHRONOUSLY before draining queued changes so the
            // next iteration PATCHes instead of POSTing a second design.
            designIdRef.current = result.data.id;
            set(() => setDesignId(result.data.id));
            onCreatedRef.current?.(result.data);
          }
          confirmedRef.current = Math.max(confirmedRef.current, revision);
          failedRef.current = false;
          set(() => setFieldErrors({}));
          // Apply server-normalised data only when this response is the latest
          // desired revision (nothing newer queued or in the pipeline).
          if (revision === revisionRef.current && isEmpty(pendingRef.current)) {
            onLatestConfirmedRef.current?.(result.data);
          }
        } else {
          // Retain the failed fields for Retry; newer queued fields win.
          pendingRef.current = { ...payload, ...pendingRef.current };
          pendingRevRef.current = Math.max(pendingRevRef.current, revision);
          failedRef.current = true;
          set(() => {
            setSaveError(result.message);
            setFieldErrors(result.fields ?? {});
            setSaveState("error");
          });
          break;
        }
      }
      inFlightRef.current = null;
      if (!failedRef.current) {
        if (confirmedRef.current === revisionRef.current && isEmpty(pendingRef.current)) {
          set(() => setSaveState("saved"));
        } else if (!isEmpty(pendingRef.current)) {
          void drain();
        }
      }
    })();
    inFlightRef.current = run;
    return run;
    // onCreatedRef/onLatestConfirmedRef are stable useLatest refs; listed to
    // satisfy exhaustive-deps.
  }, [sendOnce, takePending, set, onCreatedRef, onLatestConfirmedRef]);

  // Immediate save (choices, selections, blur): record and drain now.
  const save = useCallback(
    (patch: DraftPatch) => {
      mergePending(patch);
      void drain();
    },
    [mergePending, drain],
  );

  // Debounced save (free text): record synchronously, defer the network send.
  const saveText = useCallback(
    (patch: DraftPatch) => {
      mergePending(patch);
      if (textTimerRef.current) clearTimeout(textTimerRef.current);
      textTimerRef.current = setTimeout(() => {
        textTimerRef.current = undefined;
        void drain();
      }, TEXT_DEBOUNCE_MS);
    },
    [mergePending, drain],
  );

  // Force every pending change to the server and resolve true only when the
  // latest desired revision is confirmed. Used before any navigation.
  const flush = useCallback(async (): Promise<boolean> => {
    if (textTimerRef.current) {
      clearTimeout(textTimerRef.current);
      textTimerRef.current = undefined;
    }
    const target = revisionRef.current;
    await drain();
    // If drain returned an in-flight promise that predated `target`, keep
    // pumping until the target is confirmed or a failure stops us.
    while (confirmedRef.current < target && !failedRef.current) {
      if (inFlightRef.current) {
        await inFlightRef.current;
      } else if (!isEmpty(pendingRef.current)) {
        await drain();
      } else {
        break;
      }
    }
    return confirmedRef.current >= target && !failedRef.current;
  }, [drain]);

  const retry = useCallback(async (): Promise<boolean> => {
    failedRef.current = false;
    set(() => {
      setSaveState("saving");
      setSaveError(null);
    });
    return flush();
  }, [flush, set]);

  // Adopt an existing design on resume (no create needed).
  const adopt = useCallback((id: string) => {
    designIdRef.current = id;
    setDesignId(id);
  }, []);

  const clearFieldErrors = useCallback(() => setFieldErrors({}), []);

  return {
    designId,
    saveState,
    saveError,
    fieldErrors,
    save,
    saveText,
    flush,
    retry,
    adopt,
    clearFieldErrors,
  };
}
