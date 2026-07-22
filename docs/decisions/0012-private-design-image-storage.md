# 0012 — Permanent private design-image storage and signed delivery

- Status: accepted
- Phase: 11
- Date: 2026-07-19

## Context

Phase 10 left a successful generation as raw provider output staged at
`generation-staging/<attempt-uuid>/raw.<verified-extension>` in private
storage, with `DesignVersion.image_storage_key` deliberately blank. Phase 11
owns turning that staged output into permanent, canonical, privately stored
images and delivering them to the owning browser — nothing else (Phase 12
owns the results UI; Phase 16 owns retention, purging and reconciliation).

## Decision

### Raw staging versus permanent storage

Staging and permanent storage are distinct layers with distinct lifecycles:

- **Staging** (Phase 10) holds the provider's raw bytes exactly as verified,
  keyed by attempt UUID, written once, never overwritten. It is part of the
  crash-recovery story: because permanent-storage writes and PostgreSQL
  commits are not atomic, the staged object is the durable source every
  ingest re-run starts from.
- **Permanent storage** (Phase 11) holds the canonical derivatives at
  deterministic, server-owned keys:

  ```text
  design-images/<design-uuid>/<design-version-uuid>/original.webp
  design-images/<design-uuid>/<design-version-uuid>/thumbnail.webp
  ```

  Only UUIDs appear in keys — no user identity, title, answer, prompt
  fragment, prediction id or client-controlled filename. One pure
  key-building function (`sitara.media.ingest.build_design_image_keys`) is
  the single source of the layout and coerces inputs through `uuid.UUID`, so
  no traversal or unexpected character can reach a key.

**Staging objects and staged metadata are retained after ingest.** They are
crash-recovery inputs and future purge candidates; deleting them is Phase 16
work. They remain private throughout.

### Storage backends and the `design_images` alias

All permanent-image operations use the Django storage alias
`storages["design_images"]`, resolved at call time (never a module-level
instance) so tests and environment overrides always take effect. Exactly two
backends exist, selected by the strict, case-sensitive
`DESIGN_IMAGE_STORAGE_BACKEND`:

- `s3` — production and local MinIO-compatible private storage (same private
  options as the default alias: `default_acl=None`, query-string auth,
  `file_overwrite=False`, SigV4);
- `filesystem` — offline development and deterministic ingest testing ONLY:
  a private directory (`DESIGN_IMAGE_FILESYSTEM_ROOT`) outside static files
  with `base_url=None` (`.url()` raises) and owner-only permission modes.
  Production refuses this backend at startup.

### Canonical processing and processor versioning

`process_design_image` (pure, `sitara/media/image_processing.py`) re-verifies
the Phase 10 byte/pixel bounds, decodes with Pillow only, rejects
truncated/animated/unidentified/oversized input, applies EXIF orientation,
strips ALL metadata (EXIF, GPS, comments, XMP, ICC, provider fields) by
re-encoding a bare RGB copy, composites alpha onto a documented neutral
studio grey, downsizes (never upscales, never distorts or crops) to
`DESIGN_IMAGE_MAX_EDGE` / a `DESIGN_IMAGE_THUMBNAIL_EDGE` square with LANCZOS,
encodes both outputs as WebP with explicit quality/method parameters, reopens
and verifies the encoded files, and hashes the final encoded bytes.

`DESIGN_IMAGE_PROCESSOR_VERSION` (currently `1.0.0`) names this exact
behaviour and is persisted onto every ingested DesignVersion. A golden
manifest (`sitara/media/tests/processor_golden_v1.json`) pins output hashes
for deterministic inputs: changed output with an unchanged version fails the
suite; a deliberate bump requires a reviewed manifest update. A future
processor version creates NEW DesignVersions — it never rewrites an existing
generated version.

### Permanent provenance and immutability

`DesignVersion` carries the full permanent-image provenance (keys, sha256
hashes, byte sizes, dimensions for original and thumbnail, processor version,
ingested-at timestamp) under an all-or-none CHECK constraint, plus hash-shape,
positivity, keys-differ and requires-spec-and-prompt constraints. Once
present, provenance is immutable: identical re-runs are idempotent; divergent
content fails (`DesignImageImmutable`) and never overwrites. All fields are
read-only in admin. Legacy and Phase 10 rows (all fields absent) stay valid;
no migration performs storage I/O.

### Crash recovery across object storage and PostgreSQL

Object storage and the database cannot commit atomically; correctness comes
from determinism plus verification (`ingest_staged_design_image`):

- neither final object exists → write both, then verify by read-back;
- a matching object exists → reuse it; a divergent object → fail safely,
  never overwrite, never suffix-rename around it (a renaming backend is
  detected, best-effort cleaned and failed);
- objects written but the metadata commit lost → the deterministic re-run
  recovers the metadata;
- metadata committed → the re-run verifies both objects still match before
  treating ingest as complete;
- no transaction or row lock is held during staging reads, image processing,
  final writes or verification — only the short final metadata write locks
  the GenerationAttempt then the DesignVersion (fixed order);
- recovery NEVER regenerates output or calls a provider.

### Pipeline integration and failure taxonomy

The Phase 10 state machine gains stage E: claim → DesignSpec → prompt →
submit/poll/download/staging → **canonical permanent ingest** → success. An
attempt is not `succeeded`, and its Design not `generated`, until both
permanent objects are stored and verified. A redelivery whose version already
carries complete provenance skips every provider stage and reprocessing —
stage E verifies the final objects and finalises.

Two stable codes classify ingest failures, both occurring after paid output
already exists, so neither ever permits automatic resubmission or another
paid prediction (neither is spend-resolved; the retained staged metadata
keeps blocking the enqueue guard):

- `image_ingest_unverified` — transient/ambiguous storage availability;
  bounded task retries re-run ONLY verification/ingest;
- `image_ingest_failed` — confirmed corrupt/conflicting/invalid permanent
  content.

`manage.py ingest_design_image --attempt <uuid>` is the operator-safe
recovery path: the same ingest service, zero provider calls, admitted only
for a terminal ingest-stage failure (or an already-succeeded attempt, for
idempotent re-verification), refusing missing staged data and mismatched
versions, printing only UUIDs, status, processor version and dimensions. On
success it completes the attempt and marks the Design generated.

The Celery task time budget includes an explicit ingest-stage allowance
(`INGEST_STAGE_BUDGET_SECONDS`) so a legitimately slow ingest is never
soft-killed into retry exhaustion.

### Signed delivery — ownership before signing, bearer URLs after

`GET /api/v1/designs/<design-uuid>/versions/<version-uuid>/images/` is the
ONLY way a browser obtains design images:

- `AllowAny` with ownership filtering BEFORE the design UUID lookup
  (anonymous private workspaces keep working); the version must belong to
  the owned design; anything inaccessible or nonexistent — including a
  version UUID probed on its own — is one indistinguishable 404; a failed
  GET never creates a workspace or session;
- a generated design with only Phase 10 staging returns the controlled
  `409 design_image_not_ready`;
- the filesystem backend and storage outages return the controlled
  `503 design_image_delivery_unavailable` — never a filesystem path, a
  `file://` URL or a permanent public URL (there is deliberately NO backend
  image proxy in Phase 11; a proxy is the documented upgrade path);
- responses carry `Cache-Control: no-store` and `Referrer-Policy:
  no-referrer` and expose only the two URLs, their dimensions and one shared
  `expires_at` — no prompt, DesignSpec, storage key, hash, provider/model/
  prediction id, seed, staging metadata or user/session identifier;
- signed URLs appear ONLY here — never in design list/detail, job payloads,
  public config or catalogue endpoints.

`issue_design_image_urls` requires complete provenance, confirms both private
objects exist, and presigns GET-only SigV4 URLs with the strict
`DESIGN_IMAGE_SIGNED_URL_TTL_SECONDS` (30–3600s, default 300) via a dedicated
signing adapter. The signing client targets `S3_SIGNED_URL_ENDPOINT_URL` — a
browser-reachable origin (local MinIO is `http://localhost:9000` while the
API talks to `minio:9000`); blank means the normal regional S3 endpoint;
production requires a clean HTTPS origin. The signing endpoint is never used
for ordinary object I/O and never exposed via public config. Responses pin
`image/webp` and fixed inline server-owned filenames.

The declared `expires_at` is a conservative bound, not the exact per-URL
SigV4 expiry: it is computed from the issuance instant captured just before
signing, while each URL's storage-enforced expiry is stamped by the signer a
few milliseconds later — so under synchronised clocks the real expiry is at
or slightly after the declared one (the safe direction). Clients must treat
`expires_at` as "refresh no later than this"; Phase 12's refresh logic should
renew comfortably before it.

Delivery does NOT rely on the storage client's own botocore timeouts for
fail-fast behaviour — those are deliberately generous, sized for the slower
asynchronous ingest consumer of the same `design_images` alias. Instead the
existence-check phase bounds itself with an in-process deadline
(`EXISTENCE_DEADLINE_SECONDS`, 3.5s against the frontend's fixed 5s
transport abort): both object checks run concurrently on ONE module-level
bounded thread pool (`_EXISTENCE_POOL_WORKERS`), each request cancels its
still-queued checks on every exit, and an expired deadline returns the
controlled 503 immediately. Under a sustained storage hang the pool
saturates at its fixed cap instead of growing with request volume — new
requests' checks stay queued, their deadlines expire, and they degrade to
the same controlled 503: a circuit breaker that never pins request workers
and never grows threads without bound.

**The privacy limitation is explicit:** ownership is checked before issuance;
AFTER issuance a signed URL is a temporary bearer URL. Anyone possessing it
may use it until expiry, and logout, session rotation or account switching
does not revoke it. URLs are therefore short-lived and never persisted,
cached or logged — anywhere, including the frontend (memory-only usage,
re-fetched fresh on every call). A future authenticated backend proxy is the
upgrade path when immediate revocation or stricter delivery controls are
required. A signed URL must never be described as permanently private or
non-shareable.

Generated designs remain private by default throughout: private buckets, no
public ACLs, no public URLs, no sharing surface.

## Consequences

- Phase 12 builds the results UI on `fetchDesignImageUrls` (same-origin,
  generated OpenAPI types, strict result mapping, no caching) and owns URL
  refresh while a results page stays open.
- Phase 16 owns staging retention/purge, stuck-attempt reconciliation and
  rate/cost safeguards; until then staged objects accumulate privately.
  *Delivered in Phase 16 (ADR 0017):* `purge_expired_designs` (Celery Beat)
  deletes each expired design's permanent **and** Phase 10 staging objects
  before its row, under a row lock, aborting that design without orphaning
  objects if a storage delete fails.
- The Phase 10 paid live checkpoint remains pending and unaffected.

## Amendment: delivery latency is bounded in-process, not by client timeouts

The full-phase council found the original "Signed delivery" text attributed
delivery's fail-fast behaviour to bounded storage-client connect/read
timeouts. The delivered design is deliberately different, and the section
above now records it: the `design_images` storage client keeps generous,
ingest-sized timeouts (the settings comment names both consumers), and the
synchronous delivery path bounds ONLY its own storage phase with the
in-process `EXISTENCE_DEADLINE_SECONDS` deadline over one shared bounded
existence-check pool. Tuning the client timeouts therefore changes ingest
behaviour, not the delivery worst case; tuning delivery means changing the
deadline or the pool cap in `sitara/media/delivery.py`, whose comments
document the sizing basis, the cancel-on-exit guarantee and the saturation
circuit breaker.

## Amendment: Phase 12 adds a separately signed attachment download URL

Phase 12 (ADR 0013) extends the image endpoint's response additively rather
than superseding the "Signed delivery" contract above: the original image's
response object gains a `download_url` alongside the existing inline `url`,
sharing the same declared `expires_at` as the inline original and thumbnail
URLs. The new URL is signed with `Content-Disposition: attachment` and the
fixed server-owned filename `sitara-concept.webp` — no user-controlled title
or filename ever enters signing. This is implemented as one allowlisted
`disposition: "inline" | "attachment"` parameter on the existing
`S3DesignImageSigner.sign_get`, not a second signing service, and the
filesystem backend's controlled `503 design_image_delivery_unavailable`
still applies to both dispositions identically. Every privacy limitation
above — temporary bearer URL, no revocation before expiry, never persisted,
cached or logged, ownership checked only before issuance — applies equally
to the attachment URL.

## Operator note: stray permanent objects are never auto-deleted

This note concerns ingest/crash-recovery generally (both amendments above),
not the attachment download URL specifically. Recorded residual edge,
accepted as safer than deleting: the ingest service never deletes objects at
a version's deterministic
permanent keys. If an anomalous state (for example staged metadata
rewritten mid-ingest, which the locked re-checks refuse) ever strands a
just-written object there whose content differs from what a later correct
ingest produces, that later ingest fails closed with "a conflicting
permanent object already exists" rather than overwriting. Recovery is a
manual, deliberate deletion of the two private objects under
`design-images/<design-uuid>/<version-uuid>/` followed by the
`ingest_design_image` command. Automated cleanup was considered and
rejected: deletion at a deterministic key risks discarding an object that a
concurrently committed provenance row already references, which is the
worse failure.
