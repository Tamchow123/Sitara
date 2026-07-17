# 0007 — OpenAPI contract and generated TypeScript client

- **Status:** accepted
- **Date:** 2026-07-17
- **Deciders:** Sitara maintainers
- **Phase:** Phase 6 (see ../phases/PHASES.md)

## Context

The frontend and backend previously shared server wire types by hand: the
Next.js client re-declared `ReadyResponse`, `PublicConfig`, `MeResponse` and
friends as TypeScript interfaces that had to be kept in sync with the DRF
responses manually. That is exactly the kind of duplicated contract CLAUDE.md
§17 warns against, and it drifts silently.

Phase 6's job is to make the **backend the single authoritative source** of
the API contract and generate the frontend types from it, with CI failing on
drift in either direction — without adding any new product endpoint, any
served runtime schema/Swagger UI, or any change to endpoint behaviour.

Constraints that mattered:

- Authentication stays Django session + CSRF (no JWT/bearer — CLAUDE.md §9).
- Anonymous unsafe requests keep explicit Django CSRF enforcement.
- Private-by-default designs, indistinguishable 404s, and the rights-
  controlled catalogue must not leak internal fields into a public schema.
- Same-origin browser transport, in-memory CSRF, 5s timeout preserved
  (CLAUDE.md §10).
- Generated files are never hand-edited (CLAUDE.md §18).

## Decision

**Backend OpenAPI is authoritative and committed.** drf-spectacular
(`0.28.0`, hash-locked) generates an OpenAPI 3.0.3 document for every
existing endpoint, produced only through the `spectacular` management command
into `apps/api/openapi/schema.json`. No runtime schema endpoint, Swagger UI or
Redoc is served this phase.

**TypeScript types are generated, never edited.** `openapi-typescript`
(`7.13.0`) compiles the committed schema into `apps/web/src/api/schema.d.ts`
via `npm run generate:api`; the file carries a generated-file header and is
never hand-touched. The frontend's server wire types are now aliases of the
generated components (`ReadyResponse`, `ReadyChecks`, `PublicConfig`,
`AuthUser`, `MeResponse`). Client-only result unions (e.g. the CSRF-aware
`{ ok }` results) remain handwritten because they describe frontend behaviour,
not the wire contract.

**CI fails on drift, both directions.** The backend job regenerates the
schema (`--validate --fail-on-warn`) and runs `git diff --exit-code` on
`schema.json`; the frontend job regenerates `schema.d.ts` and diffs it. A
stale contract on either side is a hard CI failure. Both files are pinned to
LF in `.gitattributes` so generation on Linux CI and checkout on Windows never
disagree.

**A typed client for safe reads; CSRF-aware mutations stay put.**
`apps/web/src/api/client.ts` wraps openapi-fetch (`0.17.0`) with
`createClient<paths>()`. Its base URL is the current page origin resolved at
runtime (same-origin, never a `NEXT_PUBLIC` backend host); it shares the one
`lib/transport.ts` fetch policy (same-origin credentials, `no-store`, 5s
abort) with the hand-written client so there are not two competing request
policies. The client stores no credentials, cookies or CSRF tokens.
Registration, login, logout and design mutations continue through the tested
CSRF-aware `lib/api.ts` flow. **An unsafe typed client that silently omitted
the `X-CSRFToken` header is deliberately NOT provided** — unsafe typed
mutations are deferred until the shared CSRF middleware is integrated
carefully.

### Why these tools

- **drf-spectacular** is the de-facto DRF OpenAPI generator, introspects DRF
  serializers/views, supports explicit `@extend_schema` annotations and
  preprocessing hooks, and needs no runtime endpoint. `0.28.0` is compatible
  with the pinned Django 5.2.4 / DRF 3.16.0.
- **openapi-typescript** emits dependency-free `.d.ts` types (no runtime), and
  **openapi-fetch** is a ~1 kB typed fetch wrapper that reuses our transport —
  no Axios, React Query, Redux or other state library (CLAUDE.md §17).

### Session/CSRF and binary representation in the schema

The only security scheme is `cookieAuth` (an `apiKey` in the `sitara_sessionid`
cookie) — no bearer/JWT scheme exists. The `X-CSRFToken` header is documented
on every unsafe browser operation; identity-free public GETs (health, config,
questionnaire, catalogue) do not claim authentication is required. The two
catalogue image endpoints are typed as `image/webp` binary responses with
documented 404/503 failures. Slash-optional runtime routes appear once, in
their canonical trailing-slash spelling, via a narrow preprocessing hook that
does not change runtime routing.

## Consequences

- Backend serializer changes now *must* be followed by schema regeneration or
  CI fails — this is the intended forcing function.
- Frontend wire types cannot drift from the server; a breaking response change
  surfaces as a TypeScript error at build time.
- Auth views are now DRF `APIView` classes (so they are discoverable) but keep
  Django `@csrf_protect` on `dispatch`; all existing auth tests pass unchanged.
- Deferred: a served schema/Swagger endpoint; unsafe typed mutations through
  openapi-fetch; documenting future generation/provider endpoints.
- Revisit if: we add a public schema endpoint, integrate CSRF into the typed
  client, or a drf-spectacular/DRF upgrade changes generated output (the byte
  contract will flag it).

## Alternatives considered

- **Keep hand-written interfaces.** Rejected: silent drift, the very problem
  this phase removes.
- **Serve a runtime schema + Swagger UI now.** Rejected for this phase: adds
  public surface with no current need; generation via management command is
  enough to drive types. Can be added later.
- **A single unified typed client for all methods including mutations.**
  Rejected now: it would either omit CSRF (unsafe) or require carefully
  re-implementing the tested CSRF bootstrap/retry-once flow inside openapi-
  fetch middleware. Deferred to a focused change.
- **Axios / a data-fetching library.** Rejected: unnecessary weight; openapi-
  fetch reuses our existing transport.
