# ADR 0004 — Private design ownership (Phase 4)

- **Status:** Accepted (2026-07-17, after the Phase 4 test suite passed);
  amended 2026-07-17 with the workspace-creation concurrency correction
  (see "Concurrency" below)
- **Deciders:** Sitara project
- **Related:** ADR 0002 (application foundation), ADR 0003 (session authentication)

## Context

Phase 4 introduces the minimum domain model for future questionnaire and
generation work: `DesignSession`, `Design`, `DesignVersion` and
`GenerationAttempt`. The original proposal assumed anonymous-only sessions;
Phase 3B added optional accounts. Designs therefore need an ownership model
that serves **both** an anonymous browser and an authenticated user — and a
safe path from one to the other.

## Decision

### Dual ownership on one mechanism

A `DesignSession` is one private design workspace:

- **Anonymous browser:** the workspace is private to the current Django
  browser session. Its `user` foreign key is NULL.
- **Authenticated user:** workspaces with `user` set are reachable from any
  of that user's authenticated browser sessions. A user may own several
  workspaces (one per browser they designed in before signing in).

### The pointer lives in Django session data, not in a domain table

Django's session **data** holds the internal DesignSession UUID under the
key `sitara_design_session_id`. No domain table stores a raw Django session
key, and no custom ownership cookie or token exists.

Why not store the session key? Django **rotates the session key** on login
(`login()` → `cycle_key()`) precisely so that a pre-login session identifier
cannot be fixated into an authenticated one. A domain column holding raw
session keys would break on every login, tempt code into disabling
rotation, and turn a session-store leak into a design-ownership leak.
Session *data* survives the rotation — which is exactly the property that
lets an anonymous workspace follow its browser through login.

### Concurrency: workspace creation is serialised per browser session

*(correction, 2026-07-17)*

As first shipped, the create path checked the pointer and created a
workspace without coordination: two requests sharing one browser session
(two tabs) could both observe "no pointer", create separate workspaces, and
let competing session saves strand one design. Corrected as follows:

- **CSRF bootstrap materialises the Django session.** `GET
  /api/v1/auth/csrf/` now creates/saves the database session (through the
  supported session API) whenever the browser lacks a live one, so a
  successful bootstrap sets both `sitara_csrftoken` and `sitara_sessionid`
  and the `django_session` row exists before the first unsafe request.
  Repeated bootstraps reuse the existing session; no session key is ever
  returned or logged.
- **The create path locks the browser's `django_session` row**
  (`SELECT … FOR UPDATE` inside a transaction), re-reads the freshest
  committed session data, re-checks the pointer under the unchanged
  ownership rules (reuse unclaimed/own, claim lazily, never another
  user's), and creates a new `DesignSession` only when the locked, freshly
  loaded data still has no usable pointer. The loser of a race blocks
  briefly and then reuses the winner's workspace. The chosen pointer is
  persisted into the locked row, and `request.session` is synchronised so
  `SessionMiddleware`'s save of this request's earlier snapshot cannot
  erase it.
- **Design creation joins the same transaction**, so a failed insert
  leaves neither an empty workspace nor a dangling pointer, and the row
  lock is held no longer than that one request's work.
- **Failures fail closed.** A lock or session-persistence failure rolls
  everything back and returns a controlled
  `503 design_workspace_unavailable`; there is no unlocked fallback, and
  neither responses nor logs carry session keys, cookie values or store
  payloads (logs record only the exception type).
- **Locking the row is not storing the key**: domain tables still hold no
  raw Django session key, and the non-create read path stays lightweight
  (no session-row lock).
- **Per-browser, not per-user**: a user may still legitimately hold
  several DesignSessions — one per browser session they designed in; only
  simultaneous creation *within* one browser session serialises onto one
  workspace.

### Automatic promotion after login (lazy, no claim endpoint)

When a browser that owns an anonymous workspace logs in or registers, the
pointer survives the key rotation, and the **next design API interaction**
claims the workspace for the user (a conditional
`UPDATE … WHERE user IS NULL` inside a transaction, so concurrent requests
cannot double-claim or transfer). There is deliberately **no
general-purpose manual claim endpoint** — nothing accepts an arbitrary
workspace or design identifier to take ownership of, so identifiers can
never be replayed into ownership.

Rules the tests pin down:

- an unclaimed pointer + authenticated request → claimed for that user;
- the user's own workspace → reused;
- **another user's workspace → never reused, never transferred** (fresh
  workspace created when needed) — switching accounts on a shared browser
  cannot move designs;
- claimed workspaces are invisible to anonymous requests even if a pointer
  survives;
- malformed or stale pointers are dropped and treated as absent.

### Ownership failures are 404

Every retrieve/update applies the ownership filter **before** the UUID
lookup. Nonexistent designs, another anonymous session's designs, another
user's designs and formerly-anonymous designs already claimed by a user are
all the same `404 not_found`. A 403 would confirm a guessed UUID exists;
designs must never become reachable — or even provable — merely because
their UUID is known. There are no public URLs, slugs, sharing or visibility
fields; designs are private by default and by construction.

### API surface stays minimal

Phase 4 exposes only `GET/POST /api/v1/designs/` and
`GET/PATCH /api/v1/designs/<uuid>/` (DRF, `AllowAny` + mandatory ownership
filtering, Django `csrf_protect` on unsafe methods because DRF's
`SessionAuthentication` does not CSRF-protect anonymous requests). Clients
may write **only `title`**; `status` is server-set (`draft`), `answers` is
server-set `{}` and read-only until the Phase 7 questionnaire defines its
schema — the column exists now so Phase 7 extends rather than redesigns.
Unknown or immutable fields are rejected with 400, never silently ignored.
Responses expose `id`, `title`, `status`, `answers` and timestamps only —
no DesignSession identifier, user, email, session key, version rows,
generation attempts or storage keys — and always carry
`Cache-Control: no-store`.

### Version and generation scaffolding

`DesignVersion` numbering is issued by a service that locks the Design row,
refuses numbers beyond `MAX_DESIGN_VERSIONS` (default 2 = initial concept +
one refinement; strict positive-integer env parsing) and leaves the
database `UNIQUE (design, version_number)` + `CHECK (version_number > 0)`
constraints as the final backstop. The maximum is deliberately an
application rule, not a database constraint, so future multi-round
refinement needs no migration. `GenerationAttempt` reserves durable state
(globally unique idempotency key, status lifecycle, safe `error_code`,
timing columns) for later phases — no Celery task, endpoint, prompt,
credential or raw provider error body exists.

## Unresolved (recorded, deferred)

- **Retention:** the proposal assumes a 30-day purge for anonymous designs;
  no purge job exists yet, and how retention interacts with user-owned
  designs is undecided.
- **Account deletion:** deleting a user cascades their design sessions at
  the database level, but no deletion endpoint or policy exists (ADR 0003
  non-goal, unchanged).

## Non-goals (Phase 4)

Questionnaire schemas/validation, inspiration catalogue, uploads,
DesignSpec, prompt construction, provider calls, Celery generation tasks,
job polling, signed image URLs, frontend design pages, deletion endpoints,
sharing, public galleries, retention purge jobs, email verification,
payments, analytics.
