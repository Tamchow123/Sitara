# 0013 — Generation progress and private concept results

- **Status:** accepted
- **Date:** 2026-07-19
- **Deciders:** Sitara maintainers
- **Phase:** Phase 12 (see ../phases/PHASES.md)
- **Related:** ADR 0007 (OpenAPI-generated client), ADR 0009 (structured
  DesignSpec generation), ADR 0010 (deterministic image-prompt builder),
  ADR 0011 (asynchronous generation pipeline), ADR 0012 (private design
  image storage)

## Context

Phase 10 delivered the asynchronous generation pipeline and Phase 11 delivered
permanent private image storage with signed delivery, but nothing yet let a
browser start a generation, watch it progress, or view the finished concept.
`ReviewSummary`'s "Generate" action was inert, there was no route for the
in-flight `GenerationAttempt`, and there was no page that turned a completed
`DesignVersion` into a readable concept for the owning user. Phase 12 closes
that gap end to end: start generation, poll progress, and render the private
result — without weakening any Phase 9–11 invariant (fail-closed AI gates,
ownership-before-lookup, signed URLs as temporary bearer URLs, immutable
provenance).

## Decision

### A dedicated, curated result endpoint

`GET /api/v1/designs/<design-uuid>/versions/<version-uuid>/result/` is a new,
separate endpoint rather than exposing the raw `DesignVersion` row or raw
persisted `DesignSpec` JSON. It applies the same ownership-before-lookup and
indistinguishable-404 discipline as every other private design endpoint, adds
`Cache-Control: no-store`, and returns a purpose-built payload
(`design_result_payload`) containing only the ten user-facing DesignSpec
sections (title, concept summary, garment breakdown, colour story, fabrics
and texture, embellishment plan, coverage and drape, cultural context,
styling notes, construction caveats) plus `image_alt_text` and `created_at`.
It never returns `source_selections`, questionnaire answers, raw inspiration
selections, the image prompt, prompt-builder version, DesignSpec
provider/model, token counts, provider prediction ID, seed, staging metadata,
storage keys, hashes, or any signed URL — image delivery stays exclusively
Phase 11's job. Documentation-only DRF serializers follow the existing
`apps/api/sitara/designs/openapi.py` convention; the frontend never
hand-maintains a competing result type.

**Phase 13 amendment (ADR 0014):** the payload additively gains
`inspiration_acknowledgements` (position/title/attribution only), read solely
from the persisted `DesignVersion.inspiration_context` snapshot — never a
live catalogue re-query — with its own schema-version and hash verification
before use, mapped to the same `503 design_result_unavailable` on corruption.
A legacy pre-Phase-13 version returns an empty list; this remains additive
and is never a readiness requirement, so the "purpose-built curated payload"
principle above is preserved, not weakened.

### Readiness is re-derived, not inferred from status

A version is result-ready only when it carries every prerequisite: a
persisted DesignSpec, a supported schema version, a persisted image prompt
and prompt-builder version, and complete original/thumbnail image provenance
(including processor version and ingestion timestamp). `Design.status` is
never trusted as a proxy for this. Before returning `200`, the endpoint
revalidates the persisted DesignSpec JSON through the authoritative Pydantic
model, reruns the existing generated-content safety scan, and reconfirms the
schema version — exactly the same defense-in-depth pattern already used by
`prompt_service.py` and `generation/services.py` (tracked as accepted
duplication, ADR 0010 precedent; a shared helper is future cleanup, not this
phase's scope). An incomplete version returns `409
design_result_not_ready`; corrupt, unsupported, or unsafe persisted content
returns a controlled `503 design_result_unavailable` rather than a raw
exception. Logs for this path record only the operation name, DesignVersion
UUID, and exception type — never the DesignSpec, narrative, prompt, answers,
storage keys, hashes, or URLs.

### `latest_job` on design detail, for durable resume navigation

`DesignDetailResponse` additively gains `latest_job: GenerationJob | null`,
selecting the newest attempt by `created_at` with UUID as a deterministic
tie-breaker, using exactly the existing public `GenerationJob` shape (no new
private attempt fields). This lets a browser that returns to `/design/<id>`
after losing the original POST's response, or after a page reload mid-poll,
resume the correct progress or result route without a dedicated "current
job" endpoint. Design-list responses are unchanged; this stays a
detail-view-only addition, selected/prefetched to avoid an extra query per
request where the view structure already supports it.

### TanStack Query as the in-memory polling and fetch layer, not WebSockets/SSE

`@tanstack/react-query` (pinned `5.101.2`) is adopted as the sole client-side
data layer for both the progress and result pages, via one shared
`QueryClient` (`apps/web/src/app/providers.tsx`) with app-wide defaults
(`refetchOnWindowFocus: false`, `refetchIntervalInBackground: false`,
`retry: 1`, `staleTime: 0`, `gcTime: 0`) that individual queries override
explicitly when they need different behaviour — never silently relying on an
ambient default a reader can't see at the call site. Polling, not
WebSockets/SSE, is the transport: generation is a bounded, minutes-long
background job with a small number of durable states, not a continuous
stream, and polling needs no new infrastructure (no channel layer, no
persistent connection management, no reconnection/backpressure design) for a
problem this shaped. The progress route
(`/design/<designId>/generation/<jobId>`) polls
`GET /api/v1/jobs/<jobId>/` with a query key
including the job UUID, and treats a `design_id` mismatch between the fetched
job and the route as not-found rather than ever redirecting from a
mismatched payload.

### Honest progress, backoff, and terminal stop — never a fake percentage

The polling schedule is derived from the server's `created_at`, not a client
clock: every 1s under 10s of age, every 2s from 10–30s, every 5s after 30s,
and polling stops entirely at a terminal `succeeded`/`failed` status.
`refetchIntervalInBackground: false` avoids polling a backgrounded tab;
`refetchOnWindowFocus: true` promptly refreshes when the tab regains focus.
The UI renders only the four durable states the backend actually reports
(`queued`, `running_text`, `running_image`, terminal) with honest copy for
each — no invented percentage, no fabricated completion estimate, and
`running_image`'s copy never names the provider or exposes an internal
ingest stage. A `succeeded` job requires a non-null `design_version_id`
before `router.replace`-ing to `/design/<id>/result/<version-id>`; a null or
malformed version ID stops polling and shows a controlled invalid-state
message instead of redirecting into a broken route. A 404 on the job itself
renders an indistinguishable "Generation not found" state, matching the
private-resource-enumeration rule used everywhere else.

### Exhaustive, stable, non-leaking error mapping

`apps/web/src/features/generation/generation-errors.ts` maps every one of
the 21 backend `GENERATION_ERROR_CODES` (frontend-typed as
`NonNullable<GenerationJob["error_code"]>`) to plain, user-facing copy via a
`satisfies Record<...>` exhaustive check, so a future backend code added
without a matching frontend entry is a compile error, not a silent runtime
gap. Messages never mention Anthropic, Replicate, model IDs, predictions,
storage keys, hashes, or billing internals; they distinguish editable
questionnaire problems (`design_incomplete`, `design_changed`) from
technical failures, explain ambiguous-submission states without inviting an
automatic retry, and describe storage/ingest failures as safe preparation
failures rather than implying no image work occurred. One unknown-code
fallback exists for runtime defence against a code the frontend doesn't yet
know.

### Independent result and image queries — the results page's central shape

The results page (`apps/web/src/features/results/DesignResult.tsx`) issues
two deliberately separate TanStack queries rather than one combined fetch,
because the two payloads have unrelated failure and lifecycle
characteristics: the curated result is stable, durable, and safe to treat as
"fetch once while mounted" (`retry: false`, `gcTime: 0`,
`refetchOnWindowFocus: false`); the signed-image payload is short-lived,
expires, and must be refreshed repeatedly while the page stays open
(`enabled: resultQuery.isSuccess`, `gcTime: 0`, `refetchOnWindowFocus:
false` — declared explicitly per-query rather than relied on from the
app-wide default, so the custom focus-refresh effect below is provably the
only focus-refresh mechanism — `refetchIntervalInBackground: false`, `retry:
1`). The image query starts only once the result query succeeds. Critically,
an image-delivery failure never hides the result brief: `ResultImage`'s
error branch renders in place of the image only, and `DesignBrief` always
renders from `resultQuery.data` independently of `imageQuery`'s state.

### Signed-URL refresh: computed from the server's `expires_at`, never a hard-coded TTL

Phase 11 defined signed URLs as short-lived bearer credentials with a
declared `expires_at`; Phase 12 owns keeping them fresh while a results page
stays open. `imageRefetchIntervalMs` computes 80% of the *observed* remaining
lifetime (`expires_at - now`), clamped to a `MIN_REFRESH_DELAY_MS` (1s) floor
so it can never schedule a zero or negative interval, and returns `false`
(no scheduling) once already past expiry or given an unparseable timestamp —
never a tight loop. A `useEffect`-driven `window` "focus" listener
additionally triggers an immediate refetch, but only when the current URL is
within `NEAR_EXPIRY_MS` (15s) of expiry, so a routine tab-focus never fights
the 80%-lifetime schedule. `expires_at` is validated as a genuine future
timestamp in the query function itself (both `Number.isNaN` and `<= Date.now()`
are rejected) — a malformed or already-past expiry throws a controlled error
before an unsafe or stale URL is ever assigned to `<img src>`. When URLs
expire and a refresh has failed, `ResultImage` stops rendering the expired
URL, shows a controlled retry action, and leaves the result brief fully
visible. On an `<img onError>` load failure, exactly one automatic signed-URL
refresh is attempted per failure episode: the guard resets on a genuine
`onLoad` success (not on URL-string identity, since the backend mints a
fresh signature on every issuance and a URL-keyed guard would never actually
cap a sustained failure across several refreshes) and is proven by tests to
hold across a sequence of distinct, consecutively-failing URLs.

### Signed URLs remain temporary bearer URLs — never persisted, cached, or logged

Consistent with ADR 0012's explicit privacy limitation, this phase adds no
new persistence for signed URLs: they live only in TanStack Query's
in-memory cache with `gcTime: 0` (verified by tests that the cache entry is
actually gone after unmount, not merely configured to be), are never written
to `localStorage`, `sessionStorage`, `IndexedDB`, cookies, route parameters,
or Sitara-controlled query strings, never appear in `console` output, and
never appear in `formatDesignBrief`'s copy/download text (verified by a
dedicated privacy test using deliberately identifiable fixture values). The
`<img>` and both download `<a>` tags carry `referrerPolicy="no-referrer"` so
the bearer URL is never leaked via the `Referer` header, and the full-size
image's new-tab link uses `rel="noreferrer noopener"`. A signed URL is never
described in UI copy as revocable or non-shareable.

### Attachment signing for image download, via a narrow signer extension

Phase 11's `S3DesignImageSigner.sign_get` gains one allowlisted
`disposition: "inline" | "attachment"` parameter (default `"inline"`,
preserving existing behaviour) rather than a duplicated signing service. The
image endpoint additionally signs the original image with
`disposition="attachment"` and the fixed server-owned filename
`sitara-concept.webp` (no user-controlled title or filename ever enters
signing), sharing one `expires_at` with the existing inline original and
thumbnail URLs. `apps/web/src/features/results/DesignBrief.tsx`'s "Download
image" action is a plain anchor using `original.download_url` — no backend
proxy, no fetch-to-Blob step, disabled once the URL is past its declared
expiry.

### Plain `<img>`, never `next/image`, for signed results imagery

`ResultImage.tsx` renders a plain HTML `<img>` rather than `next/image`,
because the source is short-lived, signed, dynamically hosted, and
deliberately outside Next's remote-image allowlist/cache — the same
reasoning already applied to the catalogue and generation-progress imagery
elsewhere in this codebase. `width`/`height` come from the image API
response (preserving aspect ratio without layout shift); no signed URL ever
appears in accessible text (`alt` uses only the DesignSpec-derived
`image_alt_text`) or in any rendered error message.

### Copy and download behaviour

`formatDesignBrief(result): string` is one pure, deterministic formatter
(`apps/web/src/features/results/result-brief.ts`) covering the title,
concept summary, every rendered DesignSpec section, construction caveats,
and the generic concept-only disclaimer — and, symmetrically with the API
payload rules above, never IDs, signed URLs, source selections, questionnaire
answers, provider details, storage metadata, or prompt text (enforced by a
dedicated test). "Copy brief" uses the Clipboard API behind an explicit user
click only, with an accessible `role="status"` success/failure
announcement and no raw HTML ever placed on the clipboard. "Download brief"
builds a client-side UTF-8 text `Blob`, always using the fixed filename
`sitara-design-brief.txt` (never the generated title, which could carry
attacker-influenced narrative content into a filename), and revokes the
temporary object URL in a `finally` block so the URL is released even if an
intermediate DOM step throws.

### Concept-only and constructibility disclaimers

A concise disclaimer block sits near the page heading, before the detailed
brief — stating this is an AI-assisted visual concept, not a photograph, not
a sewing pattern, and not a constructibility guarantee, and that colours,
materials, and fine details may differ when interpreted physically. The
DesignSpec's own `construction_caveats` are additionally rendered in the
detailed result. Neither the disclaimer nor any rendered section claims
cultural or historical authenticity beyond what the generated specification
itself states.

### No browser persistence of private result data

Beyond the signed-URL rule above, the curated result payload itself is never
written to any browser storage mechanism and is cleared from the query cache
on unmount (`gcTime: 0` on both the result and image queries, each verified
by its own unmount test) — consistent with "private by default" and with
this phase introducing no new durable client-side state for design content.

## Consequences

- Phase 14 (constrained refinement, ADR 0015) delivered on this exact result
  shape and the same result/image query separation predicted here — the
  result endpoint's payload contract was not changed, only additively
  extended with `lineage` (kind, parent_version_id, refinement.change_type).
  Version comparison reuses this ADR's result/image query independence
  twice over: viewing a refined (version 2) result renders a side-by-side
  comparison that fetches version 1's result and signed images through their
  own independent query pair (own query keys, own `gcTime: 0`, own focus/
  near-expiry refresh schedule) alongside version 2's already-fetched data —
  one version's image-delivery failure never hides the other version's
  brief, exactly as this ADR's single-version independence already
  guaranteed for a version's own result vs. image split. Progress copy
  (queued/running_text/running_image headings and explanations) is now
  branched on the job's `generation_kind`, reusing this ADR's exact
  polling/backoff/redirect machinery unchanged for both kinds.
- Phase 15 (demo flow) reuses this progress and result UI verbatim against
  demo-mode fixtures; this phase makes no demo-mode claim and ships no demo
  fixture pipeline itself.
- Phase 16 owns live-generation cost ceilings, rate limits, and the retention
  window for staged/permanent objects; nothing in this phase changes when
  `LIVE_GENERATION_ENABLED` may be safely turned on.
- Phase 17 owns the full accessibility and visual polish pass; this phase's
  styling is deliberately minimal, vanilla-CSS, and functional rather than a
  final design pass.
- `imageRefetchIntervalMs`'s 80%-of-remaining-lifetime schedule can decay
  toward its 1s floor during a sustained run of *failed* refreshes very close
  to a stale URL's actual expiry (self-terminating once `remaining <= 0`, not
  unbounded); tightening this into a real backoff in that final window is
  accepted as follow-up debt rather than blocking this phase.
- ADR 0012's "Consequences" section already anticipated this phase building
  on `fetchDesignImageUrls`; no change to ADR 0012's Decision was required —
  the additive `download_url`/`disposition` extension is documented above and
  cross-referenced from ADR 0012 rather than duplicated there.

## Alternatives considered

- **WebSockets or Server-Sent Events for progress** — rejected for this
  phase: generation is a bounded, minutes-long job with four durable states,
  not a continuous stream, and a push transport would add channel-layer
  infrastructure, reconnection handling, and backpressure design with no
  proportionate benefit over bounded polling at the schedule above.
- **One combined result+image query** — rejected: the two payloads have
  unrelated lifecycles (stable vs. short-lived-and-refreshing) and unrelated
  failure modes (a corrupt DesignSpec vs. a storage outage); combining them
  would force a single error/loading state that hides the result brief
  whenever only image delivery is degraded, contradicting the requirement
  that image failure must never become total result-page failure.
- **URL-identity-keyed single-retry guard for image load failures** —
  implemented first, then replaced: since the backend mints a fresh signed
  URL on every issuance, a guard that only blocks a retry for a
  byte-identical URL string never actually caps a sustained failure across
  several refreshes. The shipped guard instead resets only on a genuine
  successful `onLoad`, capping to exactly one automatic retry per failure
  episode regardless of URL identity.
- **A backend image proxy for delivery** — still deliberately not built in
  Phase 12, consistent with ADR 0011's "no image proxy in Phase 11" position;
  remains the documented future upgrade path if immediate revocation is ever
  required.
- **Hand-maintained TypeScript result types** — rejected in favour of the
  existing generated-OpenAPI-client convention (ADR 0007); the frontend
  narrative-fixture privacy tests (result-brief, DesignResult) instead prove
  the *rendering* never leaks the fields the type system already forbids
  requesting.
