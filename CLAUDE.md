# Sitara Repository Instructions

This file applies to the whole repository. Read it before making changes.

Task-specific instructions may add constraints but must not weaken the security, privacy, rights, cost-control, or evidence-integrity rules below. When documentation and implementation disagree, inspect the current code and tests, identify the discrepancy, and preserve the safer behaviour until the documentation is corrected.

## 1. Project purpose

Sitara is an AI-assisted South Asian bridalwear **concept-design** application. A user will: complete a guided bridalwear questionnaire; optionally supply up to three inspiration references — since Phase 22 (ADR 0025) their **own photographs only**, handed off from their own phone by QR — the only way in since ADR 0026 was amended (2026-08-12), which removed the iPad's camera capture and file picker; receive a structured bridal design description; receive a FLUX-generated visual concept; request one constrained refinement.

The intended deployment is a **shop-floor iPad**: one boutique account serves many walk-in customers, the walk-in never registers, and a concept produced on that device is the shop's work product (ADR 0027).

Sitara is for **concept visualisation only** — no sewing patterns, manufacturing specs, or construction guarantees.

## 2. Product principles

- Cultural accuracy matters. Do not flatten distinct garments, regions, communities, or ceremonies into generic "South Asian" styling.
- Privacy is the default. Designs are never public merely because their UUID is known.
- Image rights must be documented and verified before catalogue approval or AI use. Two different bars, deliberately: a CATALOGUE asset needs staff-verified, unexpired, evidenced rights (ADR 0006) before it is approved or reaches a provider; a user's OWN uploaded reference rests on a bounded per-upload self-affirmation, which is weaker and is never to be presented — in code, copy or documentation — as verified rights (ADR 0018, ADR 0019, §13).
- Accessibility is a product requirement, not later polish.
- Demo mode makes zero paid AI calls. Paid-provider access fails closed and stays explicitly gated.
- Keep Django/Next.js flows understandable; prefer small, reviewable vertical slices over speculative infrastructure.

## 3. Current repository state

Phases 1–16 are delivered and merged to `main` (Phase 16: live-generation security and cost controls, ADR 0017), followed by the inserted generated-image composition/coverage-first prompt restructure (also merged; `PROMPT_BUILDER_VERSION` 5.0.0, ADR 0010 amended). **Phase 16B is IN PROGRESS on a branch** (not merged): questionnaire feedback, cultural expansion and visual choice UX — satin, Anand Karaj, a dedicated neckline question, expanded grouped colours, no-preference controls, DesignSpec schema v2, then questionnaire v4 / DesignSpec v3 with per-role colour and per-area coverage, a one-question-per-screen wizard, private user inspiration uploads, and the ADR 0019 reference-image conditioning / flux-2-max override. Then **Phase 17** (high-fidelity UI completion and accessibility — delivered to a draft PR; the screen-reader checkpoint remains outstanding). **Phase 18** (E2E tests and deployment) is **SKIPPED** by the project owner's decision — nothing in it is delivered, so there is no deployment configuration, no smoke script and no runbook, and CI does not run E2E; deployment must be taken up as its own future phase. **Phase 19** (private stylist annotation workspace) is **merged** — ADR 0020 (annotation workspace) and ADR 0021 (account render delivery by email), the latter shipped **disabled** with no SMTP send ever performed. **Phase 21** (account render delivery and concept gallery) is delivered to a draft PR — ADR 0022 (caller-named render attachments, capped and remembered), ADR 0023 (an account is required to generate — the questionnaire stays anonymous, only the terminal generate/refine actions require sign-in), ADR 0024 (the account concept gallery). Email delivery remains **disabled** and still no SMTP send has ever been performed. **Phase 22** (in-store reference capture) is delivered to a draft PR — ADR 0025 (the inspiration catalogue is retired from the product, its machinery kept staff-only), ADR 0026 (a QR phone handoff whose grant is a hashed-at-rest, short-lived, revocable, upload-only bearer credential — an exposure **accepted and bounded, not removed**), ADR 0027 (a concept produced on a shop's iPad is the shop's work product; the walk-in never registers; the ADR 0024 account gallery ships **gated off**; an explicit "Finish and hand back" control plus a server-enforced idle timeout). Its accepted consequence: with the gallery gated and email delivery still disabled, a concept is reachable only during the session that produced it. Then **Phase 20** (optional, flag-gated height/body representation). Delivered: Phase 2 image-model evaluation; app foundation; session auth/CSRF; anonymous + authenticated design ownership; versioned questionnaire; rights-controlled catalogue; OpenAPI-generated client; structured DesignSpec generation; deterministic image-prompt builder; async Celery/Replicate generation; permanent design-image storage; generation-progress and private results; curated inspiration-metadata influence on generation; single-round constrained refinement with version comparison; deterministic zero-cost demo generation reusing the same asynchronous pipeline, storage, job/result APIs and frontend UI as live generation; live-generation gating with atomic reserve-before-spend micro-USD budget ceiling, per-session/hashed-IP throttles and a global UTC daily count limit, retention purge and stuck-job reconciliation on Celery Beat, production security hardening (CSP, headers, admin lockdown), correlation-aware structured logging, and privacy-safe DSN-gated Sentry — live generation still disabled, provider pricing operator-configured and unverified; a private per-version stylist annotation workspace whose flattened render is emailed to the owner's own account address behind a separate closed-by-default gate; and Phase 21's caller-named attachment with a durable three-send lifetime cap per render, an authenticated-only generate/refine gate, and the account concept gallery.

`docs/phases/PHASES.md` is authoritative for future work — always inspect the current branch and that file rather than relying on this paragraph.

Selected image models. ADR 0001 chose `flux-1.1-pro` for both tiers. ADR 0019
(Phase 16B) changed the DEFAULT tier to `flux-2-max` — chosen for a capability
`flux-1.1-pro` lacks, reference-image input, with no new evaluation run:

```text
DEFAULT_IMAGE_MODEL  black-forest-labs/flux-2-max     (ADR 0019)
FAST_IMAGE_MODEL     black-forest-labs/flux-1.1-pro   (ADR 0001, unchanged)
```

Check `DEFAULT_IMAGE_MODEL` in `apps/api/config/settings.py` rather than
trusting this block if the two ever disagree. The default tier MUST accept
`input_images`; changing either model requires bumping
`LIVE_GENERATION_PRICING_PROFILE`.

flux-2-max is materially more expensive per image (roughly 1.75× on the
maintainer's recorded figures — an ESTIMATE, not a verified price; see ADR 0019),
so changing the model **must** bump `LIVE_GENERATION_PRICING_PROFILE`; carrying
the old profile over would under-reserve every image. Any further model change still needs a scoped,
documented evaluation.

## 4. Read these files first

For any substantial task, read the relevant code plus `README.md`, `docs/PROPOSAL.md`, `docs/phases/PHASES.md`, `docs/decisions/`, `compose.yaml`, `.github/workflows/ci.yml`. For a phase task with its own spec file, read that file in full before editing.

ADRs currently on record: 0001 image model, 0002 application foundation, 0003 session authentication, 0004 private design ownership, 0005 versioned questionnaire schema, 0006 rights-controlled inspiration catalogue, 0007 OpenAPI generated client, 0008 questionnaire draft and wizard, 0009 structured design-spec generation, 0010 deterministic image-prompt builder, 0011 asynchronous generation pipeline, 0012 private design-image storage, 0013 generation progress and results, 0014 rights-safe inspiration metadata influence, 0015 single-round constrained refinement, 0016 deterministic demo mode, 0017 live-generation security and cost controls, 0018 questionnaire feedback and visual choice UX (Phase 16B), 0019 reference-image conditioning and the flux-2-max switch, 0020 private stylist annotation workspace, 0021 account render delivery by email, 0022 caller-named render attachments, 0023 an account is required to generate, 0024 the account concept gallery, 0025 the inspiration catalogue is retired from the product, 0026 in-store reference capture by phone handoff, 0027 the shop owns the design and the shared device.

## 5. Repository layout

```text
apps/api/       Django + Django REST Framework backend
apps/web/       Next.js App Router frontend with strict TypeScript
infra/minio/    Local private-bucket initialisation
experiments/    Phase 2 model-evaluation implementation and evidence
docs/           Proposal, roadmap, ADRs and project documentation
design_handoff_sitara_flow/
                Vendored UX handoff bundle (reference only, never imported)
images/         Source photography for questionnaire visuals (build input)
compose.yaml    Local PostgreSQL, Redis, MinIO, API, web and Celery stack
```

`design_handoff_sitara_flow/` (at the repository root) holds the supplied bridalwear-flow UX
handoff: its README, the `.dc.html` visual references — including
`Sitara Annotation.dc.html`, which Phase 19's workspace is built to — and the
"Organic" design-system stylesheet that
`apps/web/src/app/globals.css` transcribes its tokens from. It is **reference material only** —
never imported, bundled or served, and not held to repository code standards. The bundle's
prototype runtimes (`support.js`, `image-slot.js`) are deliberately excluded and must not be
ported. `images/` holds the project's own AI-generated source photography; it is a **build input**
converted into `apps/web/public/questionnaire-visuals/`, never served directly at full size.

Django apps under `apps/api/sitara/`: `accounts`, `designs`, `questionnaire`, `catalogue` (**no public API surface since Phase 22 / ADR 0025** — models, migrations, rights records, ingest sanitiser, `publicly_eligible()`, services and admin all intact and reachable only by an admin login; its views, urls, serializers and openapi module are gone), `health`, `ai_gateway` (fail-closed live-provider gateway: gating policy, Anthropic/Replicate wrappers, `resolve_generation_mode()`), `generation` (pipeline orchestration, DesignSpec generation, prompt builder/service, Celery tasks; `generation/demo/` is the deterministic zero-cost demo engine — manifest, selector, local structured/image adapters — reached only through the same asynchronous pipeline, never a mock behind `ai_gateway`). `apps/api/sitara/media/` is a support package (image processing, ingest, signed delivery, Phase 19 annotated-PNG composition and the sole `django.core.mail` choke point in `account_delivery.py`) for permanent design images — not a Django app.

## 6. Technology and version discipline

Use the versions pinned by the repository; do not opportunistically upgrade. Baselines: Python 3.12.7 (CI/image); Node 22 (CI); Django/DRF from `apps/api/requirements.in`; Next.js/React/TypeScript from `apps/web/package.json`; PostgreSQL/Redis/MinIO from `compose.yaml`. An upgrade must be justified by the task, narrowly scoped, tested, and documented.

## 7. Non-negotiable AI and cost controls

Safety gates (`apps/api/config/settings.py`): `DEMO_MODE=true`, `ALLOW_PAID_AI_CALLS=false`, `LIVE_GENERATION_ENABLED=false`, `ACCOUNT_EMAIL_DELIVERY_ENABLED=false`. `LIVE_GENERATION_ENABLED` gates the PUBLIC end-to-end generation API — a present token, both provider gates open, and complete provider config are still not enough; the operator must also set this flag. The Phase 16 rate-limit/cost-ceiling safeguards now exist (ADR 0017), but live generation stays disabled by default and enabling it additionally requires a named pricing profile with real dated prices, a positive `LIVE_GENERATION_DAILY_BUDGET_MICRO_USD`, and a persistent `noeviction` standalone budget Redis — all operator responsibilities; provider prices ship unverified (defaulting to 0). The manual budgeted live checkpoint remains pending.

`ACCOUNT_EMAIL_DELIVERY_ENABLED` (Phase 19, ADR 0021) is a **separate** operator decision on exactly the same pattern, and it gates outbound mail rather than provider spend. It is never implied by `DEBUG`, by a configured `EMAIL_HOST`, by working SMTP credentials, or by any other flag — present mail configuration must never enable sending by itself, precisely as a present API key must never enable a provider call. In production, enabling it additionally requires a real `DEFAULT_FROM_EMAIL` and a non-placeholder host, validated at startup. Automated tests and CI open **zero SMTP connections** and assert the locmem backend; do not introduce a real mail connection in tests. No SMTP send has ever been performed — do not describe email delivery as exercised.

Related settings: `DEFAULT_IMAGE_MODEL`, `FAST_IMAGE_MODEL`, `ANTHROPIC_MODEL`, `ANTHROPIC_API_KEY`, `REPLICATE_API_TOKEN`, `REPLICATE_TIMEOUT_SECONDS`, `REPLICATE_POLL_INTERVAL_SECONDS`/`_TIMEOUT_SECONDS`, `GENERATION_RAW_MAX_BYTES`/`_MAX_PIXELS`, `DESIGN_SPEC_MAX_INPUT_CHARS`/`_MAX_OUTPUT_TOKENS`, `ANTHROPIC_TIMEOUT_SECONDS`, `MAX_DESIGN_VERSIONS`, `MAX_INSPIRATION_IMAGES`/`MAX_REFINEMENTS`, `DEMO_STAGE_DELAY_MS` (demo-only, strictly bounded 0–5000, never applies to live generation). Phase 19 mail/render settings: `ANNOTATION_RENDER_MAX_PIXELS`/`_MAX_BYTES`/`_READ_DEADLINE_SECONDS`, `ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES`, `ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR`/`_PER_DAY`, `ACCOUNT_EMAIL_SEND_IP_LIMIT_PER_HOUR`, `ACCOUNT_EMAIL_RECIPIENT_LIMIT_PER_DAY`, `ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS`, `ACCOUNT_EMAIL_RENDER_BUDGET_SECONDS`, `EMAIL_TIMEOUT`, `DEFAULT_FROM_EMAIL`. Phase 21 adds `ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER` (the durable lifetime send cap per render, ADR 0022). Phase 22 adds `ACCOUNT_GALLERY_ENABLED` (default false, ADR 0027 — a third independent operator gate on the same pattern as the two above; being signed in is not enough), `WALK_IN_IDLE_TIMEOUT_SECONDS` (ADR 0027) and the handoff-grant bounds `REFERENCE_UPLOAD_GRANT_TTL_SECONDS`/`_LIMIT`/`_WINDOW_SECONDS`/`_IP_LIMIT`/`_IP_WINDOW_SECONDS`/`_MINT_LIMIT`/`_MINT_WINDOW_SECONDS`/`_MINT_IP_LIMIT`/`_MINT_IP_WINDOW_SECONDS` (ADR 0026). Some older roadmap text uses superseded names (e.g. `ALLOW_PROVIDER_CALLS`); do not reintroduce them without an explicit migration decision.

Rules:

- A present API key must never enable a provider call by itself; a present SMTP host must never enable an email by itself.
- Automated tests and CI make zero Anthropic or Replicate calls and zero SMTP connections; do not introduce such network calls in tests.
- **Annotation notes are never AI input.** A note is the most personal free text in the product — it says what someone dislikes about a garment they intend to wear. It is never part of a prompt, a `DesignSpec`, a refinement payload or any provider request, under any flag, and it is never logged or sent to Sentry.
- Do not call providers manually unless the user explicitly authorises a budgeted live checkpoint with all documented gates satisfied.
- Never log or return API keys/tokens, provider request bodies containing private user data, or provider credentials.
- All provider access goes through the `ai_gateway` fail-closed wrapper boundary, never directly from views, serializers, models, or frontend code.
- Do not change the selected model without a scoped, documented evaluation and decision update.
- Demo mode (`DEMO_MODE=true`) is strictly zero-cost and deterministic: no Anthropic or Replicate SDK client is ever constructed, no provider network request occurs, and API keys are unnecessary — a configured key never changes demo-mode behaviour, and neither `ALLOW_PAID_AI_CALLS` nor `LIVE_GENERATION_ENABLED` can make demo mode spend money. Demo execution is a distinct local branch in the generation layer, never a mock hidden behind the paid-provider wrapper: it builds a real DesignSpec locally (validated and safety-scanned like a live one), reuses the same deterministic image-prompt builder unchanged, deterministically selects from a small reviewed fixture-image manifest, and runs the result through the same durable pipeline, storage, job/result APIs and frontend UI as live generation — only the paid text and image provider stages are replaced. An absent, incomplete, or corrupt fixture pack fails closed rather than silently falling back to a generic placeholder, and demo output is always honestly labelled as such, never presented as a fresh AI-provider render.
- The image prompt is built only by the deterministic, versioned `build_image_prompt` (`generation/prompt_builder.py`) from a validated DesignSpec: one positive natural-language prompt, no negative prompt, no JSON prompt, no hard-coded model id, no provider call, no construction caveats/alt text/inspiration metadata/raw questionnaire text/provider metadata. Persisted `image_prompt`/`prompt_builder_version` are immutable audit data; a builder change requires a `PROMPT_BUILDER_VERSION` bump plus a reviewed snapshot/manifest update.
- A `Design` may be refined at most once (`MAX_REFINEMENTS = 1`; a completed refinement is enforced via `DesignVersion.parent_version`/`refined_versions`). A refinement request must name exactly one allowlisted `change_type`; the resulting DesignSpec diff is checked field-by-field against that category's exact allowlist and rejected if any changed path falls outside it or touches an immutable root — never trust a model's own claim of what it changed. Refinement is always a fresh text-to-image generation through the same deterministic `build_image_prompt` and selected image model as initial generation — never image-to-image editing, never sent the original image's bytes/URL/storage key. Seed reuse (when available) is documented everywhere as a continuity aid only, never a guarantee. The raw refinement note is untrusted, safety-scanned, bounded input; it must never be persisted into any public/result-facing payload — only the allowlisted `change_type` category may be exposed there.

## 8. Secrets and production configuration

Never commit real credentials, tokens, cookies, connection strings, rights evidence, or private storage URLs. `.env` is local and gitignored; `.env.example` holds placeholders only. Production config fails closed on missing, placeholder, or dev-only values. Config-failure messages name the setting and a safe reason, never the rejected value. Boolean/positive-integer parsing stays strict. Do not weaken host, CORS, CSRF, cookie, storage, or production-startup validation to make a test pass. Do not trust arbitrary proxy headers; trusted-proxy behaviour requires an explicit decision.

## 9. Authentication and CSRF invariants

Authentication uses Django database sessions only. Never add JWTs, refresh tokens, DRF token auth, browser-stored tokens (localStorage/sessionStorage/IndexedDB), Auth.js/NextAuth, or custom auth cookies. Cookies: `sitara_sessionid`, `sitara_csrftoken`.

Preserve: HttpOnly session cookie; `SameSite=Lax`; Secure cookies outside debug; JSON CSRF failure responses; session-key rotation on login; server-confirmed logout before the frontend clears authenticated state; generic login failure messages; Redis-backed auth throttling with hashed identifiers and fail-closed cache-outage behaviour.

DRF `SessionAuthentication` alone does not protect anonymous unsafe requests — any anonymous POST/PATCH/PUT/DELETE needs explicit normal Django CSRF enforcement; never `csrf_exempt` to bypass it. `GET /api/v1/auth/csrf/` intentionally materialises the Django session so anonymous design operations can coordinate.

## 10. Same-origin frontend transport

Browser requests use relative `/api/...` paths through the Next.js rewrite. Preserve `API_INTERNAL_BASE_URL`, `credentials: "same-origin"`, `cache: "no-store"`, a 5-second request timeout. Never reintroduce `NEXT_PUBLIC_API_BASE_URL` or expose the internal Django host in the browser bundle. CSRF tokens are held in memory only; unsafe requests send `X-CSRFToken`; at most one CSRF retry. Next.js middleware is a navigation optimisation only — Django permissions and ownership queries are the security boundary.

## 11. Private design ownership

- Anonymous designs belong to the current Django session workspace; authenticated designs belong to the user via `DesignSession` rows. The workspace UUID lives in session data under `sitara_design_session_id`; domain tables never store a raw session key.
- Login preserves the anonymous workspace pointer; the next design request lazily claims it for the authenticated user. A workspace owned by another user is never transferred or reused.
- Inaccessible, nonexistent, and foreign designs all return the same 404, not 403. Ownership filtering happens before object lookup. A list request must not create an empty workspace.
- Concurrent first creates sharing one session serialise on the database session row. Never add a public design slug or public-by-default sharing without a separate approved phase.
- Use transactions and row locks for lifecycle operations where concurrency could split ownership, exceed a limit, or duplicate numbering.
- **Generating requires an account (Phase 21, ADR 0023).** `POST /designs/<id>/generate/` and `POST /designs/<id>/refine/` require an authenticated user and answer an anonymous caller with `401 authentication_required`. Everything before them stays anonymous — creating a design, answering the whole questionnaire, saving a draft, attaching references, validating — and removing that is prohibited: nobody is asked to register before they can see what they would be registering for. The account check runs BEFORE ownership resolution, so an anonymous caller learns nothing about whether a design id exists; a signed-in caller's foreign design is still an indistinguishable 404.
- ADR 0023 re-frames ADR 0004 rather than replacing it. Anonymous session-workspace ownership still exists and still works, because a draft is still anonymous. The lazy claim-at-next-request window is **accepted, not removed** — an anonymous workspace is claimed on the next design request after login, not eagerly at login.
- **A concept produced on a shop's iPad is the shop's work product (Phase 22, ADR 0027).** One account per boutique; the walk-in customer never registers and has no account. This supersedes an assumption ADR 0004 and ADR 0023 were built on — that a design's owner is the person who answered the questionnaire — so one account now owns work produced for many different customers, permanently and by design. The mechanics are unchanged (anonymous session workspaces, the lazy claim, the indistinguishable 404); what they protect is now a boutique's book of work. Owning the work and being the data controller for the photographs and annotation notes inside it are both true and not in tension.
- The account gallery is gated by `ACCOUNT_GALLERY_ENABLED`, **default false** — a third independent operator gate on the same pattern as the two provider/mail gates. Being signed in never turns it on. `GET /designs/` answers `503 gallery_disabled`; `POST /designs/` is deliberately NOT gated, because the walk-in flow starts there. Gated, not deleted — everything ADR 0024 built stays tested and returns by setting one flag.
- **"Finish and hand back"** (`POST /designs/end-session/`) drops the workspace pointer, revokes any live handoff grant, and deletes nothing. It deliberately does **not** sign the shop out — the account is the boutique's and the next customer using it is the intended state. Behind it, `WALK_IN_IDLE_TIMEOUT_SECONDS` does the same unprompted, enforced **server-side** on the workspace's own `last_seen_at` in both the read and the create resolution paths; a browser timer is not a boundary. Both paths coordinate on the browser's `django_session` row exactly as the create path does, and revoke **before** clearing the pointer — the pointer is the only handle back to a workspace, so a failed revoke must leave something a retry can find.
- What the hand-back does **not** end is the shop's ownership: a signed-in account keeps every design it has produced, so a design URL still in that browser's history still resolves afterwards. That residual is **accepted and bounded, not removed** (ADR 0027) — bounded by the gallery being gated off, by needing a deliberate back-navigation, and by an anonymous browser losing the workspace outright to an indistinguishable 404. The control performs a full document load rather than a soft navigation so no in-memory copy of the previous customer survives; that is mitigation, not closure, and must not be described as closing it. Closing it properly is the deferred gallery redesign.
- The cost-control benefit of an authenticated generation caller is a **consequence**, not the justification. ADR 0017's ceilings and throttles are unchanged and must not be relaxed on the grounds that callers are now identified.
- The account gallery's list payload (ADR 0024) carries no signed URL — each card mints its own through the ownership-checked images endpoint. It carries a version's own `job_status` but never the `latest_job` snapshot, and exactly one piece of spec-derived text: `display_title`, the concept's NAME. Its DESCRIPTION stays out and is asserted field by field (`concept_summary`, `garment_breakdown`, `colour_story`, `fabrics_and_texture`, `embroidery`, `image_alt_text`, `image_prompt`, `design_spec`, `inspiration_context`). Widening that payload must delete a named assertion rather than satisfy a general one. Nothing here touches ADR 0014's provider-facing prohibition: `display_title` goes to the owner's own browser, never to a provider.

## 12. Questionnaire rules

The active backend schema is authoritative — do not duplicate its rules in frontend code; frontend validation is derived from the machine-readable schema. Django revalidates and remains authoritative. Stable machine IDs are persistence contracts; do not casually rename them. Published versions are immutable (corrections need a new draft + activation); at most one version is active. Schema validation is total over arbitrary JSON — malformed input becomes a controlled schema error, never a raw `TypeError`/`KeyError`/traceback. No `eval`, executable expressions, imports, or generic rules engine in questionnaire JSON.

Cultural distinctions to keep intact: gharara vs. sharara are different constructions; saree draping is distinct from lehenga styling; regional influences are optional and non-prescriptive; modest coverage options remain represented; designer/brand names are not part of the controlled taxonomy.

## 13. Inspiration catalogue and image rights

**Retired from the product (Phase 22, ADR 0025), not deleted.** No approved asset ever existed, the three public endpoints are gone, and the reference step is the customer's own photographs alone. The app below — models, rights records, ingest, the sanitiser, `publicly_eligible()`, services and admin — stays intact and staff-only, and every rule in this section still binds it. Removing the stronger, staff-verified rights model does **not** upgrade the weaker per-upload self-affirmation that remains; never describe an upload affirmation as verified, cleared or approved rights.

Staff-managed only. Never add without a separately approved phase: user uploads **into this shared catalogue** (a user's private per-design reference upload is a separate, ADR 0018/0019-authorised path and never enters the catalogue), remote URL imports, scraping, automatic rights verification, public object ACLs, or unverified images sent to AI providers.

Public eligibility requires, on every request: approved status, verified and unexpired rights, and public-display, AI-input, derivative-generation, and commercial-use all allowed. The central `publicly_eligible()` queryset is the single definition — use it for catalogue JSON and every image variant.

Ingestion: staff bytes only; decoded JPEG/PNG/single-frame WebP only; reject corrupt/animated/multi-frame/oversized/decompression-bomb input; apply EXIF orientation then strip EXIF/GPS/XMP/ICC/comments; composite transparency; encode clean RGB WebP; retain no raw original; server-generated keys with no filename/identity; private storage; clean up partial objects on failure.

Never expose storage keys, object-store URLs, image hashes, rights evidence, internal notes, verifier identities, or staff details through public APIs. Before any rights API, importer, or additional non-admin write path, add model/service-level immutability for rights records backing approved assets — the current admin freeze alone must not become the only protection.

The manual checkpoint using three genuine rights-cleared images is still pending. Never substitute downloaded/unlicensed images or fabricate rights evidence; locally generated synthetic images are fine for clearly labelled engineering tests.

**Reference images — the one deliberate override (Phase 16B, ADR 0019).** The bytes of the references a user actually selected for a design — since Phase 22 their own uploads only, plus any curated preset a design picked *before* ADR 0025 retired the catalogue — are sent to the image provider, as short-TTL signed URLs minted inside the Celery job by `generation/reference_images.py` and never persisted, returned or logged. That module is the only place a reference URL may be produced; `ImageGenerationRequest` bounds-checks what it is handed (count ceiling, https only, length ceiling) and raises `ReferenceImagesRejected` before any provider call rather than silently dropping a bad entry. The decision overrides ADR 0014's absolute prohibition; it is the project owner's decision, taken with the provider terms in view and reaffirmed after they were put explicitly: BFL's terms take a **perpetual, irrevocable licence over Inputs** to train and improve its technologies, coverage of Replicate-routed traffic is **unresolved**, and Replicate publishes **no input retention window**. The upload UI must say so before a user uploads. Nothing leaves the machine while `LIVE_GENERATION_ENABLED=false` or in demo mode — the demo path never builds a reference URL at all. Everything else in this section still binds: a storage key, bucket name, endpoint, asset UUID, title, attribution, rights record or verifier identity must never reach a provider; nor may any asset the user did not select or that fails `publicly_eligible()` at generation time — eligibility is re-checked when the URL is minted, not trusted from the earlier selection.

Apart from that override, selected inspiration image bytes, URLs and storage keys must never be sent to any AI provider (Phase 13, ADR 0014). Provider-facing inspiration influence is restricted to the frozen `garment_type`/`alt_text`/`cultural_context` fields, built only through `generation/inspiration_context.py`'s versioned, hashed `InspirationContextSnapshot` — re-validated against `publicly_eligible()` and the generated-content safety scan every time a design is generated, never trusted from a prior selection. Asset UUID, title and public attribution may be persisted for private audit/acknowledgement display but must never reach a provider. A `DesignVersion`'s persisted `inspiration_context`/`_schema_version`/`_sha256` is immutable historical audit data (read-only in admin, all-or-none database constraints) — a later asset retirement, expiry or rights revocation blocks future selection but must never rewrite an existing design's stored snapshot or acknowledgement. The "separately approved phase with its own rights, pricing and provider-terms review" that reference-image conditioning required is ADR 0019. Its rights and provider-terms answer is *the exposure is accepted and recorded*, not *the exposure was removed* — do not paraphrase it as the latter anywhere.

**Reference upload grants — the QR phone handoff (Phase 22, ADR 0026).** A `ReferenceUploadGrant` is a short-lived, revocable, **upload-only** bearer credential naming exactly one design, so the customer can send photographs from her own phone instead of handing it over. It is the same category of accepted exposure as a signed design-image URL (§14): anyone who can see the iPad's screen can photograph the QR — that is **accepted and bounded, not removed**, and describing the bounds as removing it is prohibited. The bounds: `secrets.token_urlsafe(32)`, stored **only** as a SHA-256 digest and returned exactly once to the owner who minted it; `REFERENCE_UPLOAD_GRANT_TTL_SECONDS`; uses bounded by the design's remaining reference slots, decided under the same row lock as the upload itself rather than by a second check that could disagree; at most one live grant per design (partial unique constraint plus a `Design` row lock, so re-minting kills the earlier code); explicit revocation plus automatic revocation on leaving the reference step, on the design filling up and on the walk-in session ending; and per-address and per-code throttles that both run **before** the code is resolved, reusing `accounts/rate_limits.py` and failing closed. The plaintext travels in the request **body** (never a path, query string or log, so it cannot reach an access log, a `Referer` or browser history) and reaches the phone in the URL **fragment**, which browsers do not send to servers — the Sentry scrubber therefore cuts at the first `?` **or** `#`, breadcrumbs included. Expired, revoked, spent and never-existed are one indistinguishable 404 (§15); a rejection about the caller's own file is answered honestly. The success body is a constant — never a remaining-slots count, which would let a phone recover how many references the design already had. **No read capability exists behind a grant and none may be added** — that is a permanent non-goal with a structural test. The ADR 0019 disclosure and affirmation are shown and taken **on the phone**, per upload, from the one shared `RightsDisclosure` component; an affirmation ticked on the shop's device never carries across, because a rights affirmation made by someone who never saw the photograph is worse than none. Since the 2026-08-12 amendment the phone's is the **only** one: the iPad's camera capture, file picker, disclosure copy and affirmation checkbox are removed, the code is minted and shown as soon as the reference step opens (bounded by the same TTL, single-live-grant rule, revocation triggers and mint throttles), and an explicit "Stop accepting photos" is never undone by the auto-show — one-shot per mount AND remembered for the tab, so stepping away and back does not hand the code back either; a stop that re-minted itself would make ADR 0026's revocability claim false. Because each visit mints, a mint is shared across mounts and a departing panel revokes only a grant it still holds (`handoff-coordination.ts`, memory only, never a token cache): two mints in flight are resolved in row-lock order and each revokes the other's grant, so an unshared one can leave a live-looking QR the server has already killed. Removing the weaker, wrongly-placed affirmation does **not** upgrade the phone's: it is still a per-upload self-affirmation, never verified, cleared or approved rights. `POST /designs/<id>/inspiration-uploads/` remains a live, tested, owning-session endpoint; only the browser controls are gone.

## 14. Storage rules

Object storage is private by default. Preserve `default_acl = None`, `querystring_auth = True`, `file_overwrite = False`. Never expose MinIO/S3 endpoints or credentials via API responses, schemas, logs, or browser code. Catalogue images stream through eligibility-checked Django endpoints.

Permanent design images (`media/` package, Phase 11+):

- All permanent generated-image operations use the `design_images` storage alias resolved at call time via `django.core.files.storage.storages` — never a module-level storage instance.
- `DesignVersion` image provenance is immutable audit data (all-or-none). A changed processor requires a `DESIGN_IMAGE_PROCESSOR_VERSION` bump plus a reviewed golden-manifest update, producing new `DesignVersion`s — never rewrites.
- Signed design-image URLs are issued only by the ownership-checked images endpoint (`GET /designs/<uuid>/versions/<uuid>/images/`, with an inline/attachment `disposition` param). They are temporary bearer URLs: short TTL, never persisted/cached/logged anywhere, never presented as revocable or non-shareable. A backend proxy is the documented upgrade path.
- The filesystem design-image backend is development-only: no public base URL, browser delivery fails closed, production refuses it.
- Phase 10 staging objects/metadata are retained after ingest for crash recovery; purging them is Phase 16 work.

Annotations and annotated renders (Phase 19, ADR 0020/0021):

- An annotation document is **additive**. It never modifies `image_storage_key`, image bytes, hashes, `DESIGN_IMAGE_PROCESSOR_VERSION`, the DesignSpec, or anything else on the `DesignVersion`; deleting it returns the version to its exact pre-annotated state. A refined version has its own separate document, never copied from its parent.
- Coordinates are normalised to `[0, 1]` against the version's **canonical server-side** dimensions, never the browser's `<img>`. Client-supplied `image_width`/`image_height` are validated against the server's values and rejected on mismatch, never trusted. The overlay must never use `object-fit: cover` — a crop silently invalidates every stored coordinate while looking correct.
- Saves use revision-based optimistic concurrency; a stale `expected_revision` returns `409 annotation_conflict` and leaves the stored document untouched. Nothing is ever overwritten silently, and both client recovery actions end at the server's copy.
- The annotated PNG is composed **in memory** by `media/annotation_render.py` and never persisted, never given a storage key, and never returned as a URL — its only destination is the email attachment. It is bounded by `ANNOTATION_RENDER_MAX_BYTES`/`_MAX_PIXELS`/`_READ_DEADLINE_SECONDS` and `ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES`; an oversized render is refused with a controlled code rather than sent.
- The email recipient is **always** `request.user.email`, read server-side. A client-supplied address is never accepted in any field, and the frontend client has no address parameter. An anonymous owner gets `409 email_recipient_unavailable` with no fallback. Only `media/account_delivery.py` may reach `django.core.mail`, enforced by an AST test that also catches proper-prefix imports and attribute chains. Since Phase 21 (ADR 0022) the send endpoints DO accept a request body, carrying exactly one field — the attachment filename. The rule that body carried was never "no body"; it was **no client-supplied recipient**, and that is unchanged and absolute. Do not restore the wider claim, and do not let the narrowing become a reason to accept a second field.
- The delivery marker row stores state, counters, timestamps and — since ADR 0022 — the owner's chosen attachment filename, which is the one piece of user-authored text on it. Never a recipient address, note text or rendered bytes. A durable row survives into backups and admin views, so it is a worse place to leak an address than a cache key; the stored filename is protected accordingly (never logged, never sent to Sentry, never returned to anyone but its owner, never AI input).
- A caller-named attachment gives up ADR 0021's "headers carry nothing private", knowingly and with the exposure stated to the user before they incur it (ADR 0022). That exposure is **accepted, not mitigated or removed** — describing it as removed is prohibited exactly as for ADR 0021's three and ADR 0019's rights override. The header-injection surface it opens *is* closed: control characters and lone surrogates are refused rather than stripped, path separators removed, NFC applied, length bounded, extension server-owned. Only bidi controls are stripped — never the whole `Cf` category, since ZWNJ/ZWJ are load-bearing in Devanagari, Urdu and Bengali.
- Each render may be sent at most `ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER` times for its whole life, counted in a durable column against messages the backend accepted — never against a rate limiter or a cache, because a cache eviction must not hand back more sends. It is a lifetime total, distinct from and additional to the per-hour/per-day/per-IP rate limits.
- Annotation data is **memory-only in the browser**: never `localStorage`, `sessionStorage` or IndexedDB.
- Removing the result screen's Download link is **UX, not a privacy control** — the signed URL still exists and is still a bearer URL. Never describe it as the latter.

Phase 12 results (`GET /designs/<uuid>/versions/<uuid>/result/`) return a curated, DesignSpec-derived result independent of the signed-image endpoint — frontend fetches result data and the signed image via two independent queries so one failing doesn't block the other. Job status is polled at `GET /jobs/<uuid>/`; `Design` detail responses carry an additive `latest_job` field for resume navigation.

## 15. API conventions

Keep global DRF permissions authenticated by default; public/anonymous-session endpoints opt into `AllowAny` explicitly and document why. Identity-free public GET endpoints use `authentication_classes = []` and must not create sessions or `DesignSession` rows. Return JSON for API errors, never Django HTML error pages. Use stable machine error codes and safe user-facing messages. Sensitive/revocable responses use `Cache-Control: no-store`. Private-resource enumeration failures return indistinguishable 404s. Reject unknown/immutable write fields rather than ignoring them. Validate content type, malformed JSON, bounds, and exact response shapes. Never expose model serializers wholesale when they include internal fields. Sensitive-failure-path logs contain only safe operation names, row UUIDs, and exception types — avoid exception text/tracebacks that may carry secrets, input, storage keys, or rights data. Broad exception containment is only for a deliberate API/admin boundary; domain code uses narrow exceptions and transaction rollback.

Runtime routes support slash-optional forms where the Next.js rewrite needs them; documentation/generated contracts expose one canonical route.

## 16. Backend implementation style

Standard Django/DRF patterns: models hold durable state and constraints; services hold multi-row transactions, lifecycle transitions, storage coordination, and concurrency-sensitive operations; serializers validate shapes; views stay thin; QuerySet helpers centralise security-sensitive visibility; admin actions call the same services as any future trusted write interface. Use `transaction.atomic()`/`select_for_update()` where invariants span concurrent requests. Database constraints are final backstops, not substitutes for clear application errors. Do not add repository/command-bus/generic-handler/use-case layers around simple Django operations. Avoid signals when an explicit service call is clearer. Do not bypass invariants with `QuerySet.update()` except in migrations, narrow concurrency logic, or tests intentionally simulating corruption. UUIDs for externally referenced domain objects; all timestamps timezone-aware.

## 17. Frontend implementation style

Next.js App Router, strict TypeScript. Prefer small local context/hooks over global state dependencies. Provide accessible labels, focus handling, loading/error states, and `aria` relationships. Never persist passwords, CSRF tokens, cookies, or session state in browser storage. Do not treat route guards as authorization. Keep server wire types generated from the OpenAPI contract; do not hand-maintain competing interfaces. Client-only result unions may stay handwritten when they describe frontend behaviour rather than API wire contracts. TanStack Query (`@tanstack/react-query`) is approved and in use for the Phase 12 generation-progress/result polling flow only (backoff 1s/2s/5s) — do not expand it into a general data layer, and do not add Axios/Redux or other large dependencies without an explicit phase need.

## 18. Dependency and generated-file workflow

**Python**: direct deps in `apps/api/requirements.in`; pinned hash-verified lock in `apps/api/requirements.txt`. Regenerate only after a genuine direct dependency change, using the exact pinned toolchain:

```powershell
docker run --rm -v "${PWD}\apps\api:/app" -w /app python:3.12.7-slim-bookworm `
  sh -c "python -m pip install --upgrade pip==26.0.1 && python -m pip install pip-tools==7.5.3 && python -m piptools compile --generate-hashes --output-file requirements.txt requirements.in"
```

Regenerate a second time and verify determinism; do not allow unrelated upgrades.

**Node**: `npm`, commit `apps/web/package-lock.json`; CI installs with `npm ci`.

**Generated files**: never hand-edit generated OpenAPI schemas, generated TypeScript types, migrations after generation, or dependency locks — change the source and regenerate.

## 19. Database and migrations

```powershell
docker compose exec api python manage.py makemigrations
docker compose exec api python manage.py migrate
docker compose exec api python manage.py makemigrations --check --dry-run
```

Keep migrations deterministic and reviewable; add named constraints for durable invariants; test constraints against PostgreSQL, not only SQLite/mocks. Do not rewrite or delete applied migrations without an explicit strategy. Avoid migrations importing mutable runtime application functions.

## 20. Testing commands

Start with `git status --short`, `git log -5 --oneline`, `docker compose ps`. Run targeted tests during development; run the full regression suite before committing a substantive phase.

**Backend**: `docker compose config`; `docker compose build api`; `docker compose up -d`; then inside `api`: `manage.py check`, `manage.py makemigrations --check --dry-run`, `pip check`, `pytest`, `ruff check .`, `ruff format --check .`.

**Frontend**: `npm --prefix apps/web run lint`, `... run typecheck`, `... test -- --run`, `... run build`.
Run these on the HOST, not inside the `web` container. Four tests deliberately read files outside
the `apps/web` Docker build context, and none of the four is reachable from the `web` service's
mounts (`apps/web/src`, `apps/web/public`, `apps/api/openapi`, `contracts`):

| Test | Reads |
| --- | --- |
| `app/design-tokens.test.ts` | the vendored design system at `design_handoff_sitara_flow/` |
| `features/annotations/render-parity.test.ts` | `apps/api/sitara/media/annotation_render.py` |
| `features/questionnaire/screens.test.ts` | `apps/api/sitara/questionnaire/fixtures/questionnaire_v4.json` |
| `lib/sentry-scrub-parity.test.ts` | `apps/api/config/sentry.py` |

So `docker compose exec web npm test` reports a batch of failures — the design-token contrast
assertions, the mark-palette parity ones, the v4 screen-plan ones and the Sentry-scrubber parity
ones — that exist neither on the host nor in CI. All four fail LOUDLY rather than skipping, on
purpose: a silently-passing transcription check is worse than a loud environment-specific failure.
Adding a fifth such test means adding a row here.

`features/questionnaire/validation.test.ts` also reads outside `apps/web`, but is **not** in that
list: `contracts/` is bind-mounted into the container precisely so the shared Django/Vitest
validation contract works on both sides. Lint covers `src`, `e2e` and `playwright.config.ts` at
`--max-warnings 0`.

**End-to-end (Phase 17, extended by Phase 19 — Phase 18 was skipped, so this is the
whole E2E story)**: CI's `e2e` job runs the **functional** specs against the real
stack in demo mode (`needs: [backend, frontend]`); the visual project stays a local
gate because the committed baselines are `-win32`. Locally, with the stack up and `DEMO_MODE=true
ALLOW_PAID_AI_CALLS=false LIVE_GENERATION_ENABLED=false
ACCOUNT_EMAIL_DELIVERY_ENABLED=false DEMO_STAGE_DELAY_MS=5000`, and after
`docker compose exec api python manage.py install_demo_asset_pack --dev-synthetic`:
`npm --prefix apps/web run e2e` (journeys + accessibility + annotations) and
`... run e2e:visual` (platform-suffixed baselines, a local gate only). See
`apps/web/e2e/README.md`.

Note on the local `.env`: it may have live generation ON. Backend runs therefore need
`-e DEMO_MODE=true -e ALLOW_PAID_AI_CALLS=false -e LIVE_GENERATION_ENABLED=false`
explicitly, or a handful of `ai_gateway`/`health` tests fail as an environment
artefact rather than a regression.

**Celery**: `docker compose exec api python -c "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"`.

**Phase 2 evidence integrity**: `cd experiments\model-eval && .venv\Scripts\python -m pytest tests\test_model_decision.py -q && cd ..\.. && git status --short -- experiments/`.

Do not claim checks passed unless they were actually run and their results observed.

## 21. Phase 2 evidence is frozen

Never modify, regenerate, delete, stage, or reformat anything under `experiments/model-eval/outputs/`, or alter locked evaluation evidence/hashes, unless the user explicitly commissions a new evaluation phase with a documented budget and review process.

## 22. CI expectations

Runs on every push and PR. Backend: Python 3.12.7, Postgres and Redis services, hash-verified install, dependency-lock freshness, Ruff lint/format, Django checks, migration consistency, an OpenAPI schema freshness/contract-drift check (`spectacular ... --validate --fail-on-warn` + `git diff --exit-code`), pytest. Frontend: Node 22, `npm ci`, a generated-API-types drift check, lint, typecheck, tests, production build. A local green run is not a substitute for hosted CI when the task requires CI confirmation.

## 23. Standard task workflow

Read this file, the task spec, relevant ADRs, and affected code. Run `git status --short` and note HEAD; never overwrite unrelated user changes. Establish a focused test baseline. Plan the smallest safe implementation and avoid scope creep. Implement the requested slice only. Add tests for success, failure, privacy, security, concurrency, and rollback where relevant. Run targeted tests, then required full regression checks. Review the diff for secrets, unsafe logs, provider calls, public storage, and accidental evidence changes. Update docs/ADRs when an architectural decision or delivered phase changes. Commit only when requested; never amend/squash/rewrite history unless explicitly instructed; push only when explicitly requested or already authorised.

When a genuine blocker exists, stop before an unsafe assumption and report the exact blocker.

## 24. Commit policy

One focused commit per independently reviewable concern; separate prerequisite fixes from feature work. Conventional, descriptive messages (e.g. `fix(questionnaire): validate enum field types`, `feat(catalogue): add rights-controlled inspiration catalogue`). Do not mix dependency upgrades, formatting sweeps, or unrelated refactors into a feature commit. Keep the working tree clean after a requested commit.

## 25. Response format

Keep final reports compact; do not repeat the task specification. Return only: outcome and unresolved blockers; files/areas changed; important security/architecture decisions; tests and checks actually run with results; provider-call, secret, storage, and Phase 2 integrity confirmation where relevant; commit SHA and hosted CI result when requested. No long numbered report unless explicitly requested.

## 26. Prohibited actions

Never do the following without explicit, task-specific authorisation: `docker compose down --volumes`; delete/reset dev volumes; force-push or rewrite Git history; commit real secrets or `.env`; make paid AI calls; enable provider calls because a token exists; send real email or open an SMTP connection from tests or CI; enable email delivery because mail configuration exists; accept a client-supplied email recipient anywhere; widen the send endpoints' request body beyond the single filename field ADR 0022 admits, or restore the superseded claim that they accept no body at all; strip the whole `Cf` category from a filename (ZWNJ/ZWJ are load-bearing in Devanagari, Urdu and Bengali); check the send cap against a rate limiter or cache instead of its durable column; log or expose a chosen attachment filename; send annotation note text to an AI provider or a log; use a note as a default filename; require an account before the questionnaire, or otherwise remove anonymous questionnaire use; relax ADR 0017's ceilings on the grounds that generation callers are now authenticated; put a signed image URL in the design LIST payload, or add DesignSpec description fields to it beside the admitted name; describe removing the download link as a privacy control; describe ADR 0021's three accepted exposures, ADR 0022's fourth (a caller-named attachment in a mail header), ADR 0023's accepted lazy-claim window, ADR 0019's rights override, ADR 0026's bearer credential, ADR 0027's accepted consequence that a concept is reachable only during its own session, or ADR 0027's accepted residual that a signed-in shop can still reach a handed-back design by deliberate back-navigation, as removed rather than accepted; describe the hand-back control's full document load as closing that residual; log a handoff grant's plaintext, return it in any response but the one that mints it, put it in a URL path or query string, or store it unhashed; add ANY read capability behind a handoff grant; let an affirmation made on the shop's device satisfy an upload made from the customer's phone; describe a per-upload self-affirmation as verified, cleared or approved rights; restore the public inspiration-catalogue endpoints or delete the catalogue app's models, rights machinery or stored snapshots; enable the account gallery because a caller is signed in; sign the shop out from "Finish and hand back", or enforce the walk-in idle timeout in the browser instead of the server; change the image model without evaluation; scrape/import unlicensed images; fabricate rights evidence; create public S3/MinIO ACLs; expose private storage keys/URLs; weaken CSRF/ownership/cookie/rate-limit/production validation; introduce JWT or browser-stored auth tokens; reveal whether an inaccessible private object exists; modify Phase 2 output evidence; claim tests/manual checks/provider-call absence/CI success without evidence.

## 27. Efficient task prompt

```text
Read CLAUDE.md, docs/phases/PHASES.md, the relevant ADRs, and <task-spec-file>.
Inspect the current repository and implement the task exactly as specified.
Run the required checks, create the requested focused commit(s), and return only
outcome, changed areas, test results, unresolved issues, commit SHA(s), and CI status.
```

## 28. Automated phase development — `/run-phase`

`/run-phase <phase-identifier> <requirements-file-or-description>` is the normal phase-development command. The workflow is provided by the user-level phase-council installation under `~/.claude/` (skills `run-phase`/`resume-phase`, six council reviewers and a chair under `agents/phase-council/`, and phase-gated safety hooks in user settings). This repository contributes only `.claude/phase-council.json` (base branch, protected branches, exact build/test/lint/format/typecheck commands from §20); runtime state and reports stay under `.claude/review/` (see its `README.md` for the pointer map).

Once started, Claude acts as phase orchestrator and continues without routine user intervention through planning, implementation, per-commit review, fixing, committing, full-phase verification, pushing, and draft-PR creation, until reaching exactly one terminal state: `PR_READY`, `BLOCKED`, or `ABORTED_SAFELY`.

Binding rules, in addition to every rule above:

- Every commit must pass the six-reviewer read-only council (functionality, clean-code, architecture, security, testing, reliability) plus the chair, on the exact staged diff whose SHA-256 still matches the approved hash. Every phase must pass the full council over the whole `base..HEAD` diff.
- Reviewers and the chair are read-only; only the orchestrator edits application files.
- Fix blocking findings automatically (add regression tests, re-review) rather than accepting or downgrading them without evidence. Unresolved P3s become PR technical debt.
- Never commit or push directly to `main`, never merge the PR or mark it ready — the outcome is a fully-reviewed draft PR into `main` for manual merge.
- Requirements, executable evidence, and code evidence override any implementation summary or done-claim.
- Retry limits (then `BLOCKED`): 3 implementation attempts/task, 4 council cycles/commit, 5 full-phase cycles, 3 CI cycles, 3 attempts/finding.

`/resume-phase` recovers an interrupted run from `.claude/review/runtime/active-phase.json` — not part of the normal workflow. While a phase is active, the user-level Stop hook prevents ending the turn early, and the PreToolUse git-guard hook blocks protected-branch writes, force-pushes, history rewrites, hard resets, destructive cleans, PR merges, and unapproved commits. Both hooks are inert when no phase is active; the `run-phase` skill is the workflow engine, the hooks are deterministic safety nets.

# Compact instructions

When compacting, preserve:

- The current phase requirements and acceptance criteria
- Architectural and implementation decisions
- Every modified or newly created file
- Completed and remaining work
- Test commands, failures and final results
- Unresolved defects and reviewer findings
- Important user corrections and constraints
- The precise next action

Discard repetitive command output, superseded plans, failed exploratory approaches,
and information that can be recovered directly from the repository.