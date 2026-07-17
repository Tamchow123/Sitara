import { useRef } from "react";

// Keep a ref pointing at the LATEST value, synchronised DURING render (not in
// a post-render effect). Stable async callbacks — the save coordinator's
// sendOnce, the RHF step resolver — read these refs the instant a user event
// fires, which can be *before* an effect would have run. Writing the ref in
// render (an idempotent latest-value cache) closes that window so the first
// answer never uses a stale initial value (e.g. an empty questionnaire
// version id).
export function useLatest<T>(value: T) {
  const ref = useRef(value);
  ref.current = value;
  return ref;
}
