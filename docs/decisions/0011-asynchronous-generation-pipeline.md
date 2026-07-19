# 0011 — Asynchronous generation pipeline and gated Replicate rendering

- **Status:** accepted
- **Date:** 2026-07-18
- **Deciders:** Sitara maintainers
- **Phase:** Phase 10 (see ../phases/PHASES.md)
- **Related:** ADR 0001 (image model), ADR 0004 (private design ownership),
  ADR 0009 (structured DesignSpec generation), ADR 0010 (deterministic
  image-prompt builder)

## Context

Phase 10 turns the offline Phase 8/9 artefacts (a validated DesignSpec and a
deterministic image prompt) into a **durable, resumable asynchronous job** that
renders a FLUX image via Replicate, while preserving every Sitara
non-negotiable: private ownership, fail-closed cost control, and no provider
call in tests or CI. It is delivered as two commits — Part A (durable jobs, no
live provider) and Part B (gated Replicate rendering).

## Decision

### GenerationAttempt begins before the DesignVersion

The async job must exist before the DesignSpec/DesignVersion do, so
`GenerationAttempt` was reshaped to own a required `design` FK and a **nullable**
`design_version` link (SET_NULL). It carries private provenance (provider,
model, prediction id, seed, server-authored parameters) and the raw staged
image metadata; none of that is ever exposed through the job API.

### Per-Design idempotency and one in-progress job

`idempotency_key` is unique **per Design** (not globally). A PostgreSQL partial
unique constraint enforces **at most one in-progress attempt** (`queued`,
`running_text`, `running_image`) per Design. A repeated key replays the same
attempt; a different key while a job is in progress is a `409
generation_in_progress`; a completed design is `409 design_already_generated`.

### Design lifecycle

`Design.status` gains `generating`, `generated` and `generation_failed`
(DB-constrained). A successful enqueue moves a draft to `generating`; successful
raw-image staging to `generated`; a terminal failure to `generation_failed`. A
failed design with no DesignVersion is recoverable — the first successful edit
returns it to `draft`; a design with a DesignVersion is never draft-editable.

### Celery generation queue and deterministic task ids

The task `sitara.generation.tasks.generate_design_attempt` is routed to a
dedicated `generation` queue (the worker also listens to the default `celery`
queue for the health ping). The task id **is** the attempt UUID, so the broker
naturally de-duplicates redelivery. Task settings: `acks_late`,
`reject_on_worker_lost`, bounded soft/hard time limits, **no** whole-pipeline
autoretry — only explicitly classified transient failures use a bounded retry.

### Attempt-level advisory locking and resumable stage markers

A non-blocking PostgreSQL advisory lock in the **two-integer** lock space
(distinct from the Design-level bigint spec lock) serialises execution so
duplicate delivery never runs one attempt twice. Persisted markers are
authoritative on resume: a linked DesignVersion skips Anthropic, an existing
image prompt skips prompt persistence, an existing prediction id is never
resubmitted, and an already-staged object is verified rather than regenerated.

### Atomic attempt↔DesignVersion linkage

The DesignVersion and the attempt's `design_version` link are written in the
**same** transaction that creates the version, so there is no crash window in
which a version exists but is unlinked.

### Best-effort prediction-creation boundary and seed reuse

Replicate gives no exactly-once create guarantee. A persisted
`image_submission_in_flight` marker is written (with the seed and parameters) in
the same transaction **before** the create call. A definitely-pre-acceptance
transient clears the marker so a bounded retry may resubmit; an ambiguous
transport failure, or a crash after a successful create but before the
prediction id is persisted, ends the attempt as `image_submission_ambiguous`
and **never resubmits** — conservative spend. The seed is generated once
(cryptographically) and reused on every retry.

### Replicate async prediction creation and polling

The provider uses only the pinned SDK's public async endpoints
(`predictions.create(model=…, input=…)`, `predictions.get`,
`predictions.cancel`) — never `replicate.run()`, streaming, webhooks or a
hard-coded model version. Polling reuses the same prediction id; a timeout
attempts cancellation and ends as `image_poll_timeout`. Provider output is
downloaded through a hardened boundary: only `*.replicate.delivery` HTTPS hosts,
no embedded credentials, every redirect re-validated, a streamed byte cap, and
the URL is never logged. The bytes are Pillow-verified (PNG/JPEG/WebP, pixel
cap, SHA-256) and copied to **private** storage at
`generation-staging/<attempt>/raw.<ext>`. `DesignVersion.image_storage_key`
stays blank — the final image ingest is Phase 11.

### Capability gates

Code-level flags (`STRUCTURED_DESIGN_PROVIDER_IMPLEMENTED`,
`IMAGE_PROVIDER_IMPLEMENTED`, `FULL_GENERATION_PIPELINE_IMPLEMENTED`) are all
True. `image_generation_is_available()` requires DEMO_MODE false, paid calls
allowed, a non-empty Replicate token and a valid model; the worker re-checks it
before every new submission. Public `generation_is_available()` additionally
requires `LIVE_GENERATION_ENABLED`, which defaults false — so a token alone
enables nothing, demo mode enables nothing, and public generation stays off
until an operator deliberately enables it. A previously accepted prediction may
still be polled/staged if the public flag is later disabled (no loss of
already-paid output).

## Consequences

- Phase 11 owns final image ingest (transcode/thumbnail, signed design-image
  URLs); `DesignVersion.image_storage_key` remains reserved for it.
- Phase 12 owns the progress/results UI; Phase 15 owns demo generation; Phase 16
  owns rate limits, cost ceilings and the stuck-job reconciler.
- **Live generation must not be publicly enabled before the Phase 16
  safeguards** (rate limits + atomic cost ceiling) are in place.
- Operational reconciliation of an attempt stranded in-progress by a hard worker
  loss is deferred to Phase 16; the bounded task time limits, `conn_max_age`
  and idempotent writes limit the blast radius in the interim.

## Amendment: unresolved-spend regeneration block (review hardening)

The Phase 10 spec's recovery rule ("a new API request with a new idempotency
key may link the same incomplete version and retry only the image stage")
applies to spend-**resolved** terminal outcomes, enumerated in
`pipeline._SPEND_RESOLVED_CODES`: a provider-reported terminal
failure/cancel/abort polled from the provider itself
(`image_prediction_failed`, `image_prediction_canceled`,
`image_prediction_aborted`), and output that was obtained and confirmed
unusable (`image_output_invalid`, `image_staging_failed`) — there
regeneration is the only possible remedy. **Every other** terminal code
on a failed attempt that carries submission evidence (an accepted prediction
id, or the in-flight marker persisted before the create call) blocks
regeneration by default — fail closed — because the spend question is
unresolved: an ambiguous acceptance (`image_submission_ambiguous`),
unverified staged paid output (`image_staging_unverified`), a poll or
download outage against a live prediction (`image_provider_unavailable`,
`image_download_failed`), our own poll deadline (`image_poll_timeout` — its
best-effort cancellation is never confirmed, and the prediction may still
complete and bill after the deadline), or an unclassified crash after
submission (`internal_generation_error`). A fresh attempt would submit a second billed
prediction while the first may already be paid. The cost-control
non-negotiables take precedence over the generic retry sentence; an operator
resolution path (and reconciliation) belongs to Phase 16. A failed attempt
with **no** submission evidence provably never invoked the provider and
remains freely retryable.

## Checkpoints

The offline fixture checkpoint (zero network, `run_generation_fixture`) is
validated by automated tests. The **paid live checkpoint remains pending** and
must not run without explicit, budgeted spend authorisation. Only safe aggregate
observations may ever be recorded here.

## Amendment: Phase 11 gates `generated` on permanent ingest

Phase 11 (ADR 0012) appended a canonical permanent-ingest stage (E) to this
pipeline. Two statements above are superseded:

- a Design now moves `generating` -> `generated` only once the permanent
  original AND thumbnail have been stored and verified by the Phase 11 ingest
  (raw staging alone no longer completes a generation);
- `DesignVersion.image_storage_key` is no longer blank after success — the
  Phase 11 ingest populates the full permanent-image provenance
  (all-or-none), and a redelivery with complete provenance skips every
  provider stage and finalises after verifying the final objects.

The staged raw object and its metadata are retained after ingest (crash
recovery across the non-atomic object-storage/PostgreSQL boundary); purging
them remains Phase 16 work. See
`docs/decisions/0012-private-design-image-storage.md`.
