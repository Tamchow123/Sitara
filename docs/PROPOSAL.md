# Sitara — AI-Assisted South Asian Bridalwear Concept Design: Proposal

Sitara lets a bride-to-be answer a guided questionnaire, optionally pick up to three approved inspiration images from a rights-cleared catalogue, and receive an AI-generated bridalwear concept: a FLUX-rendered visual plus a structured written description derived from a schema-constrained DesignSpec authored by Claude Sonnet 5. One constrained refinement round is allowed. The tool is for **concept visualisation only** — no sewing patterns, no manufacturing specs, no constructability guarantee.

**Confirmed foundational decisions:**

- **Monorepo**: `frontend/` (Next.js 16) + `backend/` (Django 5.2) in this repo.
- **Inspiration influence is text-only in the MVP** — an MVP limitation, *not* a permanent decision. The provider interface must accept optional reference-image inputs later without redesign. The Phase 2 evaluation mandatorily tests text-only vs curated-metadata vs actual reference-image conditioning and produces the evidence for whether this limitation should remain.
- **Refinement is prompt-edit + regenerate**, with the honest caveat that prompt-only regeneration may substantially change the image; seed reuse does not reliably preserve a garment.
- **DEMO_MODE is strictly zero-cost and deterministic** — never calls Anthropic or Replicate. Anonymous *live* generation is a separate feature behind `LIVE_GENERATION_ENABLED` with its own limits and cost ceiling.
- **Claude returns only a DesignSpec** via Anthropic structured outputs; a deterministic, versioned backend prompt builder produces the FLUX prompt.
- **No FLUX model is committed until the Phase 2 feasibility evaluation completes**; all provider model names are env-configurable. The candidate set itself is chosen at the start of Phase 2 by inspecting the then-current Replicate/Black Forest Labs catalogue — any model names in this document are illustrative and time-sensitive.
- **Django's standard session framework** carries the anonymous browser session and CSRF; the browser reaches the API same-origin under `/api/` (proxied to Django in production). No custom authentication cookie.
- **OpenAPI-generated frontend contract** (drf-spectacular → openapi-typescript → openapi-fetch), committed types, CI drift check.
- **Prompt templates live in source control** with version constants; DB-managed prompt promotion is deferred until evaluation genuinely requires it.

The phased implementation roadmap (with per-phase scope, non-goals, commands, tests, checkpoints, and commits) lives in [PHASES.md](PHASES.md). Architecture and technology decisions are recorded in [decisions/](decisions/).

---

## 1. Product Purpose and Target Users

**Purpose.** Turn a bride's preferences (garment type, silhouette, colour story, embellishment, regional tradition, occasion) into a coherent, culturally-grounded visual concept she can react to, share with family, or bring to a designer as a starting brief. It compresses the "I can't describe what I want" phase of bridal shopping.

**Target users (MVP):**
1. **Brides-to-be** in the South Asian diaspora and subcontinent exploring looks before committing to a designer — primary persona.
2. **Family members / wedding party** browsing on the bride's behalf — same flow, no distinct features.
3. **Boutique designers** using it as a client-conversation aid — secondary; the structured description is written to double as a design brief.

**Not serving in MVP:** tailors needing measurements, manufacturers, marketplaces, users wanting to upload their own photos.

---

## 2. MVP User Journey

1. **Landing** — value proposition, showcase carousel of sample concepts, prominent "concept visualisation only" disclaimer, "Design my concept" CTA. Anonymous session created on first interaction (standard Django session cookie; no account).
2. **Guided questionnaire** — multi-step wizard (~7 steps): garment type (lehenga / saree / gharara / sharara / anarkali / shalwar kameez…), regional/cultural tradition, ceremony (nikah / mehndi / baraat / walima / pheras / reception…), colour palette, embellishment style and density, silhouette & coverage preferences (including modest full-sleeve styling), dupatta styling, capped free-text "anything else". Zod-validated per step; progress persisted to the session.
3. **Inspiration selection (optional)** — browse the approved catalogue (filterable), select up to 3 images; each displays its usage-rights attribution. *MVP limitation:* only the images' curated metadata/tags influence generation.
4. **Generate** — user reviews a summary, hits Generate. API enqueues an async job and returns 202; UI shows staged progress ("Interpreting your brief… Composing your concept… Rendering…") via status polling.
5. **Concept view** — rendered image + structured description (title, garment breakdown, colour story, embellishments, styling notes, construction caveats, cultural notes) + constructability disclaimer. Actions: download image, copy description, refine.
6. **Refinement (optional, one round)** — constrained refinement chips (colour direction, embellishment density, neckline/sleeves, dupatta style) plus a short capped note. New async job; v2 shown alongside v1, with an in-UI note that the refined render is a fresh generation and may differ in composition.
7. **Done** — design privately reachable via its session for the retention window. No sharing/publishing in MVP.

---

## 3. Functional Requirements

**FR1 — Sessions.** Django's standard session framework issues the anonymous session cookie; the DesignSession row is associated with the Django session; Django issues and validates the CSRF token, and unsafe requests carry it. No custom authentication cookie (any future exception requires a documented requirement Django sessions cannot meet). *Revised in Phase 3B (see ADR 0003):* optional **session-authenticated accounts** now exist on the same Django session framework (register/login/logout/me). Whether design creation will require an account is not yet decided. *Revised in Phase 4 (see ADR 0004):* designs support **both** anonymous browser-session ownership and authenticated user ownership; a browser's anonymous workspace is claimed automatically for the user on the next design API request after login (lazy promotion — no manual claim endpoint), and inaccessible designs answer 404.
**FR2 — Questionnaire.** Versioned question schema served by the backend, including **machine-readable validation and compatibility constraints** per question (types, required, option sets, bounds, caps). The frontend *derives* its Zod validation from those constraints and additionally Zod-validates the stable API submission envelope; rules are not manually duplicated in Zod and DRF. **Django is the authoritative validator and always re-validates submissions against the questionnaire version, taking precedence.** Draft answers persisted per session. *Delivered in Phase 5A (ADR 0005):* the versioned schema, its pure-Python format validator (three question types, bounded constraints, allowlisted compatibility rules — no expression engine), the one-active-version lifecycle and the public `GET /api/v1/questionnaire/active/` endpoint. *Answer submission/validation delivered in Phase 7 (ADR 0008):* the authoritative total `validate_questionnaire_answers` (draft/complete modes), the schema-driven frontend wizard with server-backed autosave/resume (no browser storage), the shared cross-language contract fixture, and `Design.questionnaire_version` (assign-once, active/retired only).
**FR3 — Inspiration catalogue.** Admin-curated images only, each with a mandatory verified usage-rights record. Public API exposes only approved images. Max 3 selections per design, enforced server-side. *Delivered in Phase 5B (ADR 0006):* the staff-managed catalogue — rights records with a pending/verified/rejected lifecycle, sanitising Pillow-only ingestion (metadata stripped, WebP derivatives, original discarded), service-only approval/retirement, and the public identity-free catalogue + image-streaming endpoints re-checking rights eligibility per request. *Per-design selection (max 3) delivered in Phase 7 (ADR 0008):* the `DesignInspiration` through model (ordered, PROTECT to the asset, nothing snapshotted) with server-side eligibility re-checked on selection and completion, and an accessible frontend picker.
**FR4 — Concept generation (async).** Pipeline: Claude Sonnet 5 → schema-constrained **DesignSpec** (structured output) → Django-side re-validation → **deterministic versioned prompt builder** → Replicate FLUX render. Job status pollable; failures surface a friendly retryable error with a stable domain error code.
**FR5 — DesignSpec.** Fixed schema (title, editorial description, garment attributes, colour story, embellishments, styling notes, construction caveats, cultural context). Enforced at the API boundary via Anthropic structured outputs *and* re-validated in Django before storage. Never raw free-form LLM text to the UI. *Delivered in Phase 8 (ADR 0009):* a strict Pydantic v2 `DesignSpec` + committed JSON Schema, exact `source_selections` verification, generated-output designer/brand safety scanning, gated `beta.messages.parse` structured generation with `max_retries=0` and one controlled retry, an advisory-lock-protected persistence service writing narrow provenance, and a `generate_spec` command with offline-fixture and gated live modes. *The deterministic prompt builder is delivered in Phase 9 (ADR 0010);* the image render (the rest of FR4) remains a later phase.
**FR6 — Refinement.** Exactly one refinement per design. Constrained option set + capped free text; Claude edits the stored DesignSpec ("change only what was asked"); the prompt builder regenerates the FLUX prompt; the image is regenerated from scratch. UI and docs state plainly that the result may substantially differ from v1.
**FR7 — Privacy default.** Designs private to their session. No public URLs to designs, no user-design gallery.
**FR8 — Demo mode (zero-cost).** `DEMO_MODE=true` serves the full journey from pre-generated DesignSpecs and images: identical API response shapes, simulated job-status transitions (queued → running_text → running_image → succeeded), optional artificial delay for realism. **Never calls Anthropic or Replicate — structurally guaranteed, not just configured.**
**FR9 — Live generation (separate feature).** `LIVE_GENERATION_ENABLED=true` allows real provider calls, governed by per-session and per-IP rate limits, a daily generation count limit, and a hard cost ceiling. When disabled or exhausted: 503/429 with stable error codes (§8). Demo mode and live generation are independent flags; a public portfolio deploy runs `DEMO_MODE=true, LIVE_GENERATION_ENABLED=false` by default.
**FR10 — Admin.** Django admin for catalogue CRUD + rights records and design/job inspection. (Spend dashboards, eval admin, DB prompt management: deferred — §7.)
**FR11 — Input safety.** Free-text inputs: hard length caps, character validation, denylist screening (including designer/brand names) server-side *before* any provider call; inside the Claude call, free text is delimited as untrusted preference data. The schema-constrained DesignSpec (mostly enums and bounded strings) is the containment mechanism — there is no generic `safety_flag` field doing the real work.

---

## 4. Non-Functional Requirements

- **Latency:** interactive API p95 < 300 ms; generation end-to-end target 30–90 s with honest staged progress; polling every 2–3 s with backoff.
- **Reliability:** generation retried ≤2× on transient provider errors with backoff; DesignSpec persisted before the image stage so retries never re-spend the text call; idempotency key per generation request.
- **Cost:** zero provider spend in tests, CI, and demo mode — structural guarantees, not conventions (§12). Live generation capped by count and cost ceiling.
- **Scale target:** portfolio scale — tens of concurrent users, hundreds of generations/day. Single Postgres, single Redis, one or two Celery workers.
- **Observability:** structured logging with request/session/job ID correlation; Sentry (or equivalent) both apps; token/cost accounting persisted per generation attempt.
- **Health:** `/healthz` = process liveness only (no dependency checks); `/readyz` = PostgreSQL + Redis readiness. A Redis outage degrades readiness, not liveness.
- **Accessibility:** WCAG 2.1 AA intent — keyboard-navigable wizard; alt text on catalogue and generated images (generated alt text derived from the DesignSpec).
- **Configurability:** every provider model name (`ANTHROPIC_MODEL`, `REPLICATE_IMAGE_MODEL`, demo/live tiers) is an environment variable. No model IDs hard-coded in application logic.
- **Portability:** local dev via docker-compose with local file storage; production differs only by env config (S3-compatible storage, real domains).

---

## 5. Explicit Non-Goals (MVP)

- ~~No user accounts, authentication, or profiles.~~ *Revised in Phase 3B:* optional session-authenticated accounts exist (email + password, Django sessions — ADR 0003). Still excluded: profiles, email verification, password reset, OAuth/MFA; public production registration is not feature-complete until email verification and password recovery are designed.
- No payments, subscriptions, or marketplace.
- No tailor measurements, sizing, patterns, or manufacturing output.
- No user image uploads.
- No custom/fine-tuned model training — prompt engineering over hosted models only.
- No scraping of bridal websites or social media; catalogue is manually curated with recorded rights.
- No image conditioning in the first implementation (MVP limitation, revisit in Phase 13 — see §9 note).
- No social features: sharing links, likes, public galleries, comments.
- No mobile app; responsive web only.
- No microservices; no DB-managed prompt templates; no speculative eval/budget admin before the core vertical slice works.

---

## 6. Architecture and Service Boundaries

```
Browser (Next.js 16 App Router, React/TS, Tailwind, shadcn/ui,
         RHF+Zod forms, TanStack Query; API types generated from OpenAPI)
   │  same-origin HTTPS/JSON to /api/ (openapi-fetch typed client,
   │  Django session cookie + CSRF token on unsafe requests)
   ▼
/api/ proxy (dev: Next.js rewrite; prod: router/frontend deployment
             proxies /api/ to Django — no cross-origin browser calls)
   ▼
Django 5.2 + DRF  (single project "sitara")
   ├── apps/designs      — sessions, questionnaire answers, designs, jobs
   ├── apps/catalogue    — inspiration assets + usage rights
   ├── apps/generation   — DesignSpec schema (Pydantic), prompt builder,
   │                        provider clients, Celery tasks, demo fixtures
   └── apps/core         — settings, flags, throttling, health endpoints
   │
   ├── PostgreSQL        — all relational state
   ├── Redis             — Celery broker/result backend + rate-limit counters
   └── Celery workers    — generation pipeline (only place provider calls happen)
        ├── Anthropic API (Claude Sonnet 5): DesignSpec via structured outputs
        └── Replicate (FLUX model chosen in Phase 2, env-configured): image
Storage: Django storages — local FileSystemStorage in dev,
         S3-compatible bucket in prod. Same code path.
```

**Boundaries and rules:**
- Next.js is a pure client of the DRF API. Server components may fetch public data (catalogue, showcase) for SEO; mutations go through the same API.
- **Sessions & CSRF are Django-standard:** Django issues the session cookie and the CSRF token; the anonymous DesignSession is associated with the Django session; unsafe requests include the CSRF token. The browser always talks to the API under the same public origin at `/api/` (Next.js rewrite in dev, reverse-proxy in prod), so cross-origin browser API requests — and the CORS surface they'd need — are avoided. No custom auth cookie exists.
- **All paid provider calls live behind the fail-closed `sitara.ai_gateway` wrappers** — one Anthropic wrapper, one Replicate wrapper; nothing else imports the SDKs. Wrappers refuse to execute unless `DEMO_MODE=false` *and* `ALLOW_PAID_AI_CALLS=true` *and* real keys are present (the older `ALLOW_PROVIDER_CALLS` name is superseded); test settings and demo mode never open them. This is the single enforcement point for the zero-cost guarantees and cost logging.
- **The generation pipeline is three separated concerns**, never one uncontrolled model response:
  1. *Input safety* — deterministic server-side validation/denylist before any call (FR11).
  2. *Spec generation* — one Claude structured-output call constrained to the DesignSpec JSON schema (generated from a Pydantic model). No Markdown-fence stripping, no regex JSON repair, no arbitrary prompt fragments in the response contract. Django re-validates the returned spec against the same Pydantic model before persisting.
  3. *Prompt building* — a deterministic, versioned pure function `build_image_prompt(spec: DesignSpec) -> str` (§9). Claude never writes the image prompt.
- **Reference-image readiness:** the Replicate wrapper signature accepts an optional list of reference-image inputs from day one (ignored/unused until Phase 13), so adding an approved image-conditioned provider later is a wrapper change, not a redesign.
- Prompt templates (Claude system prompt, prompt-builder templates) are **source-controlled constants with version identifiers**; each DesignVersion records the template versions used.
- **API contract:** drf-spectacular emits the OpenAPI schema; openapi-typescript generates committed TS definitions; openapi-fetch provides the typed client. CI regenerates and fails on drift. No hand-maintained duplicate TS interfaces. Note: OpenAPI types describe the *envelope* contract; they do not replace runtime validation of the dynamic questionnaire schema, which flows from the backend's machine-readable constraints (FR2) with Django as the authority.

---

## 7. Initial Data Model

(PostgreSQL; UUID PKs, `created_at`/`updated_at` throughout.)

**Core models (Phase 4–5, the minimum for the vertical slice):**

- **DesignSession** — one private design workspace, created lazily on first design activity. *Revised in Phase 4 (ADR 0004):* associated with the Django session by storing the DesignSession **UUID inside Django session data** (`sitara_design_session_id`) — **no raw `session_key` column** (it would break on login's session-key rotation and turn a session-store leak into an ownership leak); nullable `user` FK for authenticated ownership (an anonymous workspace is claimed automatically on the next design request after login); `last_seen_at`. `ip_hash` deferred until rate limiting needs it. No custom token column — Django's session framework is the identity mechanism.
- **QuestionnaireVersion** — *Revised in Phase 5A (ADR 0005):* UUID PK, globally unique positive `version`, `status` (draft/active/retired — replaces the originally proposed `is_active` boolean; a PostgreSQL partial unique constraint enforces at most one active row), `schema` (JSONB: steps, questions, options, machine-readable constraints and allowlisted compatibility rules), staff `created_by`/`activated_by` (SET_NULL). Published versions are immutable; replacement happens by activating a new version, which retires the old one. Served at `GET /api/v1/questionnaire/active/`. (Doubles as the taxonomy source; a separate TaxonomyOption table only if the schema JSONB proves unwieldy.)
- **InspirationAsset** — *Revised in Phase 5B (ADR 0006):* UUID PK, `title`, `alt_text`, optional `garment_type`/`cultural_context` (replacing the originally proposed free-form `tags` JSONB for now), `status` (draft/approved/retired with named DB constraints; approved content frozen, retirement terminal), OneToOne → **UsageRights** (`PROTECT`; required for approval), sanitised-image facts only (`image_storage_key`/`thumbnail_storage_key` — private WebP derivatives, never a Django `FileField` or public URL — width/height/bytes/SHA-256 under an all-or-none constraint), staff `uploaded_by`/`approved_by` (SET_NULL). The raw upload is discarded at ingestion.
- **UsageRights** — *Revised in Phase 5B (ADR 0006):* UUID PK, `rights_basis` (owned/commissioned/licensed/public_domain/permission_granted), `rights_holder`, `evidence_reference` (required before verification), `source_url`, `licence_name`/`licence_url`, the four usage permissions (`allows_public_display`, `allows_ai_input`, `allows_derivative_generation`, `allows_commercial_use` — all mandatory for catalogue use), `attribution_required`/`attribution_text`, `expires_at`, `verification_status` (pending/verified/rejected; rejected can never become verified), `verified_by` (SET_NULL)/`verified_at`, bounded `internal_notes`.
- **Design** — FK → DesignSession, FK → QuestionnaireVersion, `answers` (JSONB), M2M → InspirationAsset (≤3, validated), `status`, `expires_at`.
- **DesignVersion** — FK → Design, `version_number` (positive integer; **unique constraint on (design, version_number)** — the MVP maximum is enforced in application code via `MAX_DESIGN_VERSIONS=2`, not at the database level, so future multi-round refinement needs no migration), `design_spec` (JSONB, validated DesignSpec), `image_prompt` (text, builder output — stored for audit/repro), `prompt_builder_version`, `spec_template_version`, `image_model` (as configured at run time), `seed` (nullable), `image` (storage path), `refinement_input` (JSONB, nullable), `is_demo` (bool).
- **GenerationAttempt** — FK → DesignVersion, `celery_task_id`, `status` (queued / running_text / running_image / succeeded / failed), `error_code`, `attempt_number`, `idempotency_key` (unique), timing columns, and inline cost fields: `input_tokens`, `output_tokens`, `text_cost_usd`, `image_cost_usd` (no separate ProviderCall table in MVP).

**Deferred models (built only when their phase needs them):**
- BudgetWindow (Phase 16 — live-generation cost ceiling; until then a Redis counter + env cap suffices).
- EvalRun / EvalResult and any eval admin (post-MVP, after the evaluation workflow exists in earnest; Phase 2 uses files/spreadsheets).
- PromptTemplate DB rows and promotion workflow (post-MVP; source-controlled constants until then).
- Provider spend dashboard (post-MVP; `GenerationAttempt` aggregates queried ad hoc).

Retention assumption: designs purged 30 days after creation (configurable) by a periodic task (Phase 16).

---

## 8. API Endpoint Outline

Base: `/api/v1/`, always reached same-origin (dev rewrite / prod reverse-proxy per §6). Identity via the Django session cookie; unsafe requests carry the Django CSRF token; DRF throttle classes per scope. The OpenAPI schema is generated by drf-spectacular through the `spectacular` management command and committed as `apps/api/openapi/schema.json` (ADR 0007); a served runtime schema/Swagger endpoint (e.g. `/api/schema/`) is deferred to a later phase.

| Method | Path | Purpose |
|---|---|---|
| POST | `/sessions/` | Create anonymous session (idempotent per cookie) |
| GET | `/questionnaire/` | Active questionnaire schema (versioned) |
| GET | `/catalogue/images/` | Approved inspiration assets (filterable, paginated) |
| POST | `/designs/` | Create draft design (answers + ≤3 inspiration IDs) |
| GET | `/designs/{id}/` | Design + versions (session-scoped; cross-session → 404) |
| PATCH | `/designs/{id}/` | Update draft answers before generation |
| POST | `/designs/{id}/generate/` | Enqueue generation (`Idempotency-Key` required); 202 + job ID |
| POST | `/designs/{id}/refine/` | Enqueue the single refinement; same semantics |
| GET | `/jobs/{id}/` | Job status + stage; includes design-version ID on success |
| GET | `/showcase/` | Public pre-generated showcase concepts |
| GET | `/healthz` | Process liveness only |
| GET | `/readyz` | PostgreSQL + Redis readiness |

**Generation-unavailable semantics** (stable machine-readable `error_code` in the JSON body; never HTTP 402):
- `429 generation_limit_reached` — per-session/per-IP rate limit or daily generation count limit hit.
- `503 live_generation_disabled` — `LIVE_GENERATION_ENABLED` is off (and the request isn't servable by demo mode).
- `503 live_generation_budget_exhausted` — the server-side live-generation cost ceiling is reached; the UI degrades to the showcase gallery. (Demo mode is zero-cost by construction, so no demo budget code exists.)
- `409 generation_in_progress` — job already running for this design.

**Private image access (corrected model):** design images in prod are served via short-lived signed object-storage URLs. A signed URL is a **bearer URL** — anyone holding it can open it until expiry; it is *not* bound to the session. Mitigations: URLs are only issued inside session-authorised design responses; expiry is short (assumption: ~5 minutes); the residual shareability risk is documented in the privacy notes. If strict per-request enforcement is ever required, switch to proxying image bytes through an authorised backend endpoint (accepted trade-off: backend bandwidth). Catalogue/showcase images are public-read behind a CDN.

---

## 9. Generation Pipeline (Claude → prompt builder → FLUX)

**Step A — Input safety (deterministic, pre-spend).** Length caps, character validation, denylist screening (designer/brand names, disallowed content patterns) on all free text. Fails fast with a user-facing message; no provider call made.

**Step B — DesignSpec via Anthropic structured outputs.** One Claude Sonnet 5 call using the structured-outputs feature with a JSON schema generated from the Pydantic `DesignSpec` model:
`{title, editorial_description, garment: {type, silhouette, neckline, sleeves, ...}, colour_story, embellishments, dupatta_styling, styling_notes, construction_caveats, cultural_context}` — predominantly enums and bounded strings. Questionnaire answers and inspiration tags are structured context; user free text is passed **delimited as untrusted preference data** with an explicit instruction that it cannot override system rules. The response is schema-enforced by the API; Django then **re-validates it against the same Pydantic model** before persisting (defence in depth — never trust even a schema-constrained response blindly). One retry on validation failure, then hard fail. No fence-stripping, no regex repair.

**Step C — Deterministic image-prompt builder.** A pure, versioned function in `apps/generation/prompt_builder.py`. *Delivered in Phase 9 (ADR 0010):*
- `PROMPT_BUILDER_VERSION` constant, recorded on every DesignVersion (`image_prompt` + `prompt_builder_version`, read-only, all-or-none + requires-a-spec DB constraints).
- Produces **one positive editorial prompt** — no separate negative prompt and no JSON prompt, because the selected FLUX 1.1 Pro exposes neither (Phase 2 editorial path); no Replicate/model identifier is hard-coded in the builder.
- Fixed, snapshot-tested field ordering (garment/ceremony → silhouette/components → drape/proportions → colours → fabrics → embellishment → coverage → dupatta/saree drape → cultural direction/styling → fixed presentation).
- Appends fixed positive presentation instructions (full-length studio photograph head-to-hem, clean background, original non-branded design, natural anatomy/coherent hands, soft even lighting). **No universal modesty/sleeve/neckline suffix** — coverage comes only from the DesignSpec. The DesignSpec's construction caveats and alt text are not rendered; the builder never interpolates raw free text (the DesignSpec carries none) and interpolates every narrative string only through bounded, safety-scanned slots. A tiny gharara/sharara/saree integrity-cue set guards the Phase 2 confusion risks.
- `build_and_store_image_prompt` persists atomically under a DesignVersion row lock with strict immutability; an offline `build_image_prompt` command builds it with zero provider calls.
- Covered by **snapshot-style tests**: golden DesignSpec fixtures → exact expected prompt strings, plus a combined-hash/version manifest; any builder change forces a reviewed snapshot update and a version bump.

**Step D — Replicate render.** FLUX model from `REPLICATE_IMAGE_MODEL` env var (chosen in Phase 2; never hard-coded). Portrait aspect, seed recorded. Wrapper accepts optional reference images (unused until Phase 13). Timeout ~120 s; ≤2 retries resume here (spec already persisted — no double text spend).

**Refinement variant.** Claude receives the stored v1 DesignSpec + the constrained refinement input with an "edit only what was asked" instruction and the same structured-output schema; the builder regenerates the prompt. **Honest limitation, stated in UI and docs:** this is a fresh text-to-image generation; even with the same seed, changing the prompt can substantially alter composition, pose, and garment details. Seed reuse is a mild continuity aid, not a preservation mechanism. True visual continuity would require image-conditioned editing — out of MVP scope.

**Job orchestration.** Celery task on a `generation` queue; status transitions queued → running_text → running_image → succeeded/failed exposed via `/jobs/{id}/`; frontend polls with TanStack Query. Periodic tasks (Phase 16): purge expired designs, reconcile stuck jobs.

---

## 10. Image-Storage Workflow

- `django-storages` with a dedicated `design_images` alias resolved at call time (Phase 11). The strict `DESIGN_IMAGE_STORAGE_BACKEND` selects `s3` (production and the local MinIO in compose — the normal path) or `filesystem` (offline ingest testing only; private root, no public base URL, refused in production). Identical application code either way.
- Layout (as delivered in Phase 11): permanent design images at `design-images/{design_uuid}/{design_version_uuid}/original.webp` and `.../thumbnail.webp` (server UUIDs only); Phase 10 raw provider output stays at `generation-staging/{attempt_uuid}/raw.{ext}` until Phase 16 purges it; catalogue derivatives keep their own `catalogue/inspiration/...` prefix. Provider output is transcoded to canonical WebP + thumbnail on ingest (Pillow, versioned processor, full metadata strip).
- Catalogue uploads only via Django admin: content-type validation, EXIF strip, thumbnail generation; approval blocked without a verified UsageRights record.
- Prod access per §8: private bucket + short-lived signed URLs for design images (bearer-URL risk documented); public CDN for catalogue/showcase; optional backend-proxy upgrade path if strict enforcement is later required.
- Deletion: purging a Design deletes its storage objects in the same task; UsageRights rows outlive asset retirement (audit trail).

---

## 11. Security, Privacy, Copyright and Abuse Risks

**Security.** Every design read/write scoped to the Django session (via its DesignSession); cross-session access → 404. Session cookie `Secure`/`HttpOnly`/`SameSite=Lax`; Django-issued CSRF token required on unsafe requests. Same-origin `/api/` proxying keeps the browser off cross-origin API calls, minimising the CORS surface. Django hardening: security headers/CSP, DEBUG off, secrets via env. Provider keys only on backend/workers.

**Privacy.** Designs private by default; retention purge; minimal PII (accounts are optional as of Phase 3B and store only a canonical email — no profiles); IPs stored only as salted hashes (auth rate limiting uses HMAC-hashed identifiers, never raw IPs or emails); plain-language privacy page that also documents the signed-URL shareability caveat.

**Copyright / cultural risk.** Mandatory verified UsageRights before catalogue approval; attribution rendered where required; no scraping, ever. Prompts are built deterministically from a controlled vocabulary — named-designer/brand imitation blocked at input (denylist) and never introduced by the builder. Output disclaimer: AI concepts, not any designer's work. Cultural vocabulary (garment/ceremony terms across nikah, mehndi, baraat, walima, pheras, and regional traditions) human-reviewed; Claude instructed not to conflate distinct regional traditions.

**Abuse.**
- Content: strict input validation before any spend (FR11); modest-styling presentation defaults in the builder; schema-constrained spec limits free-text influence channels.
- Cost: throttles (session + IP), daily generation count limits, idempotency keys, hard cost ceiling on live generation, and the structural `DEMO_MODE=false` + `ALLOW_PAID_AI_CALLS=true` gate. Demo mode cannot spend by construction.
- Prompt injection: free text is delimited untrusted data in the Claude call and only reaches FLUX via sanitised, slot-limited fragments in the deterministic builder — user text cannot override instructions in either stage.

---

## 12. Cost-Control Strategy

- **Single choke point:** two provider wrappers, invoked only from Celery tasks; each records tokens/cost onto GenerationAttempt. No wrapper, no spend. CI greps that nothing outside `generation/providers/` imports the SDKs.
- **Structural zero-spend guarantees:** wrappers raise unless `DEMO_MODE=false` *and* `ALLOW_PAID_AI_CALLS=true` *and* real keys exist. Test settings never open them → **paid calls in automated tests are impossible, asserted by tests that expect the raise and deny sockets**. Demo mode never reaches the wrappers at all (demo pipeline is a separate fixture-backed code path).
- **Demo mode = $0 by definition** (FR8). No rate-limited-paid tier is ever called "demo".
- **Live generation controls** (Phase 16): `LIVE_GENERATION_ENABLED` flag; per-session and per-IP throttles; daily generation count limit; hard daily cost ceiling enforced by **atomic reserve-before-spend** in Redis (Lua script / atomic transaction, no check-then-increment race — details in Phase 16). Rate/count exhaustion → `429 generation_limit_reached`; ceiling exhaustion → `503 live_generation_budget_exhausted`; both degrade the UI to the showcase gallery — never a 5xx surprise.
- **Cheap by default:** model tiers per env (`REPLICATE_IMAGE_MODEL`, optional cheaper `REPLICATE_IMAGE_MODEL_FAST`); tight `max_tokens` on the single spec call; spec persisted before the image stage so retries never re-spend text; idempotency keys stop double-clicks.
- **Visibility:** GenerationAttempt cost columns queried ad hoc / via admin list; a dashboard is deferred.

---

## 13. Model-Evaluation Strategy

No custom training — evaluation targets **image-model choice first (Phase 2), then prompt templates and builder versions**.

**Phase 2 — image-model feasibility (before any application code):**
- A standalone, budget-capped experiment (`experiments/model-eval/`, typed Python — not Django) with a two-stage design: a ~12-brief **screening** round across all candidates to remove clearly unsuitable models, then a **finalist** round running the top two (human-chosen) against the full matrix.
- **Prompt matrix:** representative South Asian bridalwear briefs covering at minimum: lehenga, saree, gharara, sharara, anarkali, shalwar kameez; ceremony contexts nikah, mehndi, baraat, walima (plus pheras/reception in finalists); modest full-sleeve styling; heavy embroidery and minimal embroidery variants (~24–36 briefs, written to mirror what the future prompt builder would emit).
- **Candidate models — selected at the start of Phase 2 from the live catalogue, not from this document.** One inexpensive/fast model, one balanced production-quality model, one highest-quality model, and **at least one reference-image-conditioning / image-editing-capable model (mandatory)**. Exact provider identifiers, versions, capabilities, pricing (with check dates) and provider terms (with verification dates) are recorded in the candidates config and decision record. Any model names appearing in project documents are illustrative and time-sensitive.
- **Inspiration-influence comparison (mandatory):** each applicable brief runs in `text_only`, `metadata` (curated inspiration metadata, no image bytes) and `reference_image` (actual rights-verified references, only on capable models; incapable models recorded as skipped) modes — producing the evidence for whether the MVP's text-only limitation should remain. The MVP decision itself is unchanged until that evidence is reviewed.
- **Refinement-strategy comparison:** prompt-modification + fresh regeneration (seed reused where supported; seed reuse is recorded as a continuity aid, not a guarantee) versus image-editing / conditioned refinement (base image + one constrained change + preserve-the-rest instruction), scored for requested-change success and unrelated/composition/pose/garment-structure drift. Neither strategy is presumed better before scoring.
- **Prompt-format comparison:** deterministic per-model formats — editorial, sectioned, structured JSON only where officially supported, positive-only wording, controlled exclusions only on models with real negative-prompt support.
- **Scoring (1–5, human, blind):** garment accuracy, cultural coherence, fabric realism, embroidery quality, dupatta styling, anatomy, prompt adherence, modesty/coverage adherence, **bridal-occasion distinctiveness** (a beautiful outfit that reads as ordinary formalwear is not sufficient for Sitara), reference-image influence, refinement consistency, overall quality — plus cultural hard-failure checks (gharara/sharara confusion, saree misrepresentation, tradition conflation, ignored coverage, dupatta errors, sexualisation, implausible construction, text/logos/marks, over-literal reference copying, unrelated refinement drift, reads-as-non-bridal everydaywear). Contact sheets and scoring CSVs are genuinely anonymised (no model identity anywhere in the artefacts; mapping kept in a separate protected file), and incomplete runs are refused for review by default; no automated aesthetic scoring, no LLM judge.
- **Controls:** hard reserve-before-call budget enforcement behind four explicit live gates (`ALLOW_PROVIDER_CALLS`, token, `--budget-usd`, `--confirm-live`); full per-generation provenance including costs, latency, output hashes and pricing snapshot dates; a versioned provider-terms snapshot with unresolved items flagged for human review.
- **Gate:** no FLUX model is committed anywhere in application code or docs until this completes; thereafter it lives only in env vars.

**Ongoing (post-MVP, lightweight until then):**
- Golden brief fixtures (reused from Phase 2) exercised against the prompt builder in CI (snapshot tests — free).
- Spec-quality checks on recorded Claude outputs in CI: schema validity, required-field coverage, banned-term absence, length bounds. Live re-runs are a manual, budgeted command.
- Refinement checks on fixtures: "changed only what was asked" spec-diff.
- LLM-as-judge rubric scoring and EvalRun/EvalResult persistence: deferred until a real template-iteration workflow needs it; scores live in files/spreadsheets before that.
- Human side-by-side image review remains the arbiter for image quality in MVP.

---

## Assumptions Register

1. **Candidate FLUX models for Phase 2:** chosen at the start of Phase 2 from the then-current Replicate/BFL catalogue (one fast/cheap, one balanced, one highest-quality, and at least one reference-image/editing-capable — mandatory); any names in this document are illustrative and time-sensitive; exact model identifiers and versions are recorded in the candidates config and decision record; nothing is committed before the experiment and human scoring complete.
2. Phase 2 experiment budget: screening round ≤ USD 10; finalist round sized by config, conservative reservation ceiling ~USD 50 as shipped (expected actuals far lower — reservations are deliberately pessimistic) — confirm before each live run.
3. Retention: private designs purged after 30 days (configurable).
4. Signed design-image URL expiry ~5 minutes; bearer-URL shareability risk accepted and documented for MVP; backend proxy is the documented upgrade path.
5. Polling (2–3 s, backoff) rather than WebSockets/SSE — adequate at portfolio scale.
6. Demo-mode fixture set: ~10–15 pre-generated concepts spanning the garment/ceremony taxonomy, generated once manually within budget.
7. Live-generation defaults when enabled: ~2 generations + 1 refinement per session, low per-IP daily ceiling, daily cost ceiling a few USD — confirm before any live-enabled public deployment.
8. Deployment target is a simple PaaS/VPS; env-configured, does not change architecture.
9. Human (project owner) reviews cultural vocabulary, Phase 2 scoring, and image quality; no expert panel budgeted.
10. English-only UI with i18n-ready string structure.
11. QuestionnaireVersion JSONB doubles as the taxonomy source; a normalised TaxonomyOption table only if admin editing demands it.
