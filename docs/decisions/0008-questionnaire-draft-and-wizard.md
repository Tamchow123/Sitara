# 0008 — Questionnaire draft persistence and the design wizard

- **Status:** accepted
- **Date:** 2026-07-17
- **Deciders:** Sitara maintainers
- **Phase:** Phase 7 (see ../phases/PHASES.md)
- **Related:** ADR 0004 (private design ownership), ADR 0005 (versioned
  questionnaire schema), ADR 0006 (rights-controlled catalogue), ADR 0007
  (OpenAPI generated client)

## Context

Phase 5A shipped a versioned, backend-owned questionnaire schema but stored
no answers; Phase 5B shipped the rights-controlled inspiration catalogue but
no way to select from it. Phase 7 joins them into the first real user
journey: a guided wizard that captures a validated bridal design brief and up
to three inspiration selections into a private draft — with **no generation,
no DesignSpec, no provider calls** (those are later phases).

Two forces shaped the design: the backend must remain the single authority
for what a valid answer is (a browser can be bypassed), and the questionnaire
rules must drive both the server validator and the frontend UX **without
duplication** (ADR 0005).

## Decision

### The questionnaire version is pinned to each design, once

`Design.questionnaire_version` is a nullable `PROTECT` foreign key. It is
assigned at most once (a service-enforced, immutable link) and only to an
**active or retired** version — never a draft, which can therefore never
receive user answers. Persisted answers reference that version's stable
question/option ids forever, so a design linked to a now-**retired** version
stays fully editable and resumable against its own historical schema; a
`PROTECT` link means a version with any design can never be deleted. Legacy
Phase 4 title-only designs keep a null link and continue to work unchanged.

### Backend validation is authoritative; frontend Zod is derived

`validate_questionnaire_answers(schema, answers, *, require_complete)` is a
pure, **total** validator (any malformed JSON-compatible input becomes a
controlled domain error, never a traceback). The compatibility-rule
**semantics** — visibility, required, restricted options — live once in
`sitara.questionnaire.rules` and are mirrored, not re-invented, in
`apps/web/src/features/questionnaire/rules.ts`. The frontend's Zod schemas
and its `validateAnswers` are **derived from the machine-readable schema**;
no individual fixture rule (garment silhouettes, colour limits, saree/dupatta
visibility, note caps) is hard-coded in either language. A shared
`contracts/questionnaire-validation-cases.json` fixture is executed by **both**
the Django and the Vitest suites so the two validators cannot drift.

### Two validation modes

- **Draft** (`require_complete=False`, autosave): structurally validate every
  supplied value, enforce option allowlists, active restrictions, exclusivity
  and maximum counts/lengths — but do **not** require missing answers or
  enforce minimums. This makes partial autosave safe.
- **Complete** (`require_complete=True`, the `POST .../validate/` endpoint):
  additionally require every visible required question and enforce minimum
  counts/lengths. Validation performs no generation and re-checks inspiration
  eligibility.

### One atomic draft-update service

`update_design_draft` runs in a single transaction under a `Design` row lock:
it assigns the version once, draft-validates and persists answers, and
replaces inspiration selections as **one ordered set**. Answers and selections
**roll back together** on any failure — no partial "answers saved but
inspirations failed" state — and concurrent updates serialise on the row, so
positions can never duplicate or exceed the limit.

### DesignInspiration: ordered, three at most, nothing snapshotted

`DesignInspiration` is a through model with a 1-based `position`, unique
`(design, asset)` and `(design, position)`, and a `1..MAX_INSPIRATION_IMAGES`
(3) database check as the final backstop. It links the catalogue asset by
`PROTECT` and copies **nothing** from it — no storage key, image hash, rights
evidence/notes, verifier detail or attribution. The live asset and its rights
record stay authoritative.

### Rights eligibility is checked on selection AND on completion

Only assets currently returned by `InspirationAsset.objects.publicly_eligible()`
can be selected; a draft, retired, expired, unverified or incompletely-
permitted asset is refused with one indistinguishable message. A previously
selected asset that later becomes ineligible is represented in the detail
response as `{available: false, asset: null}` — the reason is never disclosed,
and no private field leaks. The user can remove or replace it, but **complete
validation fails while any unavailable selection remains**.

### Server-backed autosave; no browser storage for answers

Progress persists only to the private Design through the CSRF-aware API.
Answers, design ids and questionnaire content are **never** written to
localStorage, sessionStorage, IndexedDB or cookies. Choice changes save
promptly; text changes debounce and save on blur; navigation flushes pending
saves. The UI reports "Saved" **only** after the server confirms, keeps
unsaved values visible on failure, and offers retry — all announced through
`aria-live`. The Design is created on the first successful save (not on page
view), and resume reconstructs the wizard from the persisted answers and the
design's linked questionnaire, landing on the first incomplete step.

### Frontend transport unchanged; unsafe mutations stay CSRF-aware

Reads use the generated GET-only typed client (ADR 0007). The three unsafe
operations (`createDesignDraft`, `updateDesignDraft`, `validateDesignDraft`)
are **explicit** typed wrappers over the existing in-memory-CSRF, retry-once,
same-origin/no-store/5s transport — the generic client stays GET-only; no
generic POST/PATCH client is exported. Catalogue thumbnails render through a
plain `<img>` (never Next.js image optimisation), so the backend's no-store
eligibility check applies to every request and a rights-revoked image is never
proxied or cached.

## Consequences

- The design draft is now a real, validated, resumable artefact — the input a
  later generation phase will consume.
- Backend serializer/rule changes must be followed by schema regeneration
  (CI drift check) and, where semantics change, a matching update to the
  frontend rule mirror plus the shared contract fixture.
- Deferred (unchanged): DesignSpec construction, prompt building, provider
  calls, Celery generation, results, refinement, signed design-image URLs,
  user image uploads.

## Alternatives considered

- **Re-declare the questionnaire rules in Zod.** Rejected: exactly the
  duplication ADR 0005 exists to prevent; both validators derive from schema
  data instead, guarded by the shared contract.
- **Snapshot the chosen inspiration's fields onto the selection.** Rejected:
  it would freeze rights state and leak private data; the live asset + rights
  record must stay authoritative so revocation takes effect immediately.
- **A generic typed POST/PATCH client.** Rejected (per ADR 0007): it would
  either omit CSRF or re-implement the tested bootstrap/retry flow; explicit
  wrappers over the shared transport are safer.
