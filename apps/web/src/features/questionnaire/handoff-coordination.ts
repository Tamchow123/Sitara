// Coordination for the phone handoff that has to outlive one mount of the
// panel (Phase 22, ADR 0026 as amended 2026-08-12).
//
// Since the code shows itself as soon as the reference step opens, a stylist
// stepping Back and forward over that step is no longer a no-op: each mount
// wants a code. Three things therefore cannot live in a component ref, because
// what needs coordinating happens BETWEEN mounts.
//
// All of it is memory only, scoped to the tab, and none of it is a token cache
// — leaving the step still revokes, which ADR 0026 requires and none of this
// may quietly undo. A different customer is a different design and shares
// nothing here.
//
// What bounds this module's lifetime in production is a full document load, and
// the only one that happens between customers is "Finish and hand back" (ADR
// 0027, CLAUDE.md §11), which is a hard reload precisely so no in-memory copy of
// the previous customer survives. If that control ever becomes a soft
// navigation, these three collections need their own explicit clear-down —
// nothing here would notice on its own. The failure would be slow growth on a
// shop iPad left running all day, not a leak between customers: the keys are
// design UUIDs, the values a symbol and a flag, and grants stay independently
// TTL-bounded and revoked server-side regardless of this bookkeeping.

import { createReferenceGrant } from "@/lib/api";

type MintResult = Awaited<ReturnType<typeof createReferenceGrant>>;

const mintsInFlight = new Map<string, Promise<MintResult>>();
const grantHolders = new Map<string, symbol>();
const stoppedDesigns = new Set<string>();

/** Mint a code, joining a mint already in flight for the same design.
 *
 * Two mints in flight at once are resolved by the server in whichever order
 * they reach the design's row lock, and each revokes the other's grant on the
 * way (at most one live grant per design). So the response that arrives last in
 * the browser can describe a grant the server has already killed — and the
 * panel would show its QR with a ticking countdown while no phone can use it.
 * Nothing on screen would say so, and the phone is the only way a reference
 * arrives at all.
 *
 * Joining means one server-side grant and one code, with both mounts showing
 * the same thing.
 */
export function mintShared(designId: string): Promise<MintResult> {
  const joined = mintsInFlight.get(designId);
  if (joined) return joined;
  const pending = createReferenceGrant(designId).finally(() => {
    mintsInFlight.delete(designId);
  });
  mintsInFlight.set(designId, pending);
  return pending;
}

/** Record which mount's code is currently live for a design. */
export function claimGrant(designId: string, mount: symbol): void {
  grantHolders.set(designId, mount);
}

/** Whether this mount still owns the design's live code.
 *
 * A revoke is design-scoped: it kills whatever is live, not one named grant. So
 * a departing mount that revoked unconditionally could reach the server after a
 * newer mount's mint and kill the code that had already replaced its own. A
 * mount whose own mint was still in flight holds nothing — the mount that
 * joined that mint owns the result, and revokes it when IT leaves.
 */
export function holdsGrant(designId: string, mount: symbol): boolean {
  return grantHolders.get(designId) === mount;
}

export function releaseGrant(designId: string, mount: symbol): void {
  if (holdsGrant(designId, mount)) grantHolders.delete(designId);
}

/** Remember that the stylist deliberately stopped this design's code.
 *
 * A stop has to outlive the mount that took it. Otherwise stepping back and
 * forward hands back a fresh live code moments after she killed the last one,
 * and ADR 0026's revocability claim would be true only until the next
 * navigation. Cleared by asking for a code again, which is the stylist saying
 * she wants one.
 */
export function markStopped(designId: string): void {
  stoppedDesigns.add(designId);
}

export function clearStopped(designId: string): void {
  stoppedDesigns.delete(designId);
}

export function isStopped(designId: string): boolean {
  return stoppedDesigns.has(designId);
}

/** Forget everything, as a fresh tab would.
 *
 * Exported for tests: this state deliberately outlives a component, so without
 * a reset one test's stopped design silently suppresses the next one's code and
 * the failure looks like a broken panel rather than a leaked fixture.
 */
export function resetHandoffCoordination(): void {
  mintsInFlight.clear();
  grantHolders.clear();
  stoppedDesigns.clear();
}
