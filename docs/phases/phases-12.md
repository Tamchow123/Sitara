# Sitara Phase 12 — Generation progress and private concept results

Expected starting commit:

9a969dbf520e28dd18a9029a79e7659895d7444b

Before changing anything, confirm that the current `main` is this commit or a
clean descendant containing the merged Phase 11 implementation. Report any
unexpected application-code commits before proceeding.

## Read first

Read the current files rather than relying on older roadmap assumptions:

- `CLAUDE.md`
- `.claude/phase-council.json`
- `.claude/review/README.md`
- `README.md`
- `docs/PROPOSAL.md`
- `docs/phases/PHASES.md`
- `docs/phases/phases-10.md`
- `docs/phases/phases-11.md`
- `docs/decisions/0004-private-design-ownership.md`
- `docs/decisions/0007-openapi-generated-client.md`
- `docs/decisions/0008-questionnaire-draft-and-wizard.md`
- `docs/decisions/0009-structured-design-spec-generation.md`
- `docs/decisions/0010-deterministic-image-prompt-builder.md`
- `docs/decisions/0011-asynchronous-generation-pipeline.md`
- `docs/decisions/0012-private-design-image-storage.md`
- `apps/api/sitara/designs/models.py`
- `apps/api/sitara/designs/serializers.py`
- `apps/api/sitara/designs/openapi.py`
- `apps/api/sitara/designs/views.py`
- `apps/api/sitara/designs/urls.py`
- `apps/api/sitara/designs/jobs.py`
- `apps/api/sitara/generation/design_spec.py`
- `apps/api/sitara/generation/errors.py`
- `apps/api/sitara/media/delivery.py`
- `apps/web/package.json`
- `apps/web/src/app/layout.tsx`
- `apps/web/src/app/globals.css`
- `apps/web/src/app/design/[designId]/page.tsx`
- `apps/web/src/app/design/[designId]/review/page.tsx`
- `apps/web/src/features/questionnaire/QuestionnaireWizard.tsx`
- `apps/web/src/features/questionnaire/ReviewSummary.tsx`
- `apps/web/src/lib/api.ts`
- `apps/web/src/lib/transport.ts`

Use the current repository layout:

- Django: `apps/api/sitara/...`
- Next.js App Router: `apps/web/src/app/...`
- frontend feature code: `apps/web/src/features/...`
- central browser API wrappers: `apps/web/src/lib/api.ts`
- generated OpenAPI types: `apps/web/src/api/schema.d.ts`
- roadmap files: `docs/phases/...`
- decision records: `docs/decisions/...`

Do not introduce the old `frontend/`, `backend/`, `docs/PHASES.md`,
`docker-compose.yml`, local phase-agent, or local phase-skill paths.

## Phase boundaries

Implement this phase as three focused commits:

1. `feat(results): add private design result API`
2. `feat(frontend): add generation progress flow`
3. `feat(frontend): add private concept results page`

Do not combine the commits.

Part A must pass its focused checks before Part B. Part B must pass before
Part C.

## Non-goals

Do not implement:

- refinement;
- inspiration metadata influence;
- reference-image conditioning;
- demo-mode fixture selection;
- showcase galleries;
- account design galleries;
- public or shareable designs;
- a CDN;
- an authenticated image proxy;
- public image URLs;
- service workers;
- WebSockets or server-sent events;
- Playwright;
- analytics;
- retention or purge jobs;
- generation throttling;
- generation count limits;
- provider-spend ceilings;
- deployment;
- provider retries beyond the existing Phase 10 pipeline;
- changes to the selected FLUX model;
- questionnaire v2 activation.

Phase 14 owns refinement. Phase 15 owns demo generation. Phase 16 owns live
cost controls and retention. Phase 18 owns browser E2E tests.

Make zero Anthropic and Replicate calls during implementation, testing,
review, CI and manual checkpoints.

Keep:

- `DEMO_MODE=true`;
- `ALLOW_PAID_AI_CALLS=false`;
- `LIVE_GENERATION_ENABLED=false`;

unless an isolated test overrides settings with injected fakes. A present API
key must never be used.

Do not run:

    docker compose down --volumes

# Baseline

Run before editing:

    git status --short
    git log -15 --oneline
    git rev-parse HEAD
    docker compose config
    docker compose up -d
    docker compose ps

Run the current repository validation commands from
`.claude/phase-council.json`.

Also prove:

- questionnaire v1 fingerprint is unchanged;
- questionnaire v2 remains draft;
- `PROMPT_BUILDER_VERSION` remains `3.0.0`;
- `DESIGN_IMAGE_PROCESSOR_VERSION` remains `1.0.0`;
- the Phase 10/11 fixture pipeline completes with zero network calls;
- no Phase 2 evidence is modified.

# Part A — Private design result API

## 1. Add a dedicated result endpoint

Add:

    GET /api/v1/designs/<design-uuid>/versions/<version-uuid>/result/

Use the current slash-optional URL convention.

The endpoint must use:

- `SessionAuthentication`;
- `AllowAny`;
- ownership filtering before design lookup;
- a version lookup constrained to the owned Design;
- indistinguishable 404 responses for:
  - nonexistent design;
  - foreign design;
  - nonexistent version;
  - a version belonging to another design;
- no workspace creation for a failed anonymous GET;
- `Cache-Control: no-store`.

Responses:

- `200` — private result payload;
- `404 not_found`;
- `409 design_result_not_ready`;
- `503 design_result_unavailable`.

The endpoint must not issue image URLs. Phase 11's image endpoint remains the
only signed-image URL issuer.

## 2. Result readiness

Return `409 design_result_not_ready` when the selected DesignVersion does not
have every user-facing result prerequisite:

- persisted DesignSpec;
- supported DesignSpec schema version;
- persisted image prompt and prompt-builder version;
- complete permanent original-image provenance;
- complete thumbnail provenance;
- image processor version and ingestion timestamp.

Do not infer readiness solely from `Design.status`.

Before returning a result:

1. revalidate the persisted JSON through the authoritative Pydantic
   `DesignSpec`;
2. rerun the existing generated-content safety scan;
3. verify the persisted schema version is supported;
4. verify permanent-image provenance is structurally complete.

If persisted content is corrupt, unsupported or unsafe, return the controlled
`503 design_result_unavailable`.

Log only:

- operation name;
- DesignVersion UUID;
- exception type.

Never log the DesignSpec, title, narrative, prompt, answers, storage keys,
hashes or URLs.

## 3. Curated result wire shape

Return a purpose-built user-facing payload rather than the raw DesignVersion
model or raw DesignSpec dictionary:

    {
      "result": {
        "design_id": "uuid",
        "design_version_id": "uuid",
        "version_number": 1,
        "title": "string",
        "concept_summary": "string",
        "garment_breakdown": {
          "overall_form": "string",
          "garment_components": ["string"],
          "silhouette": "string",
          "drape_or_layering": "string",
          "key_proportions": "string"
        },
        "colour_story": {
          "palette_summary": "string",
          "placement": "string",
          "rationale": "string"
        },
        "fabrics_and_texture": [
          {
            "fabric": "string",
            "placement": "string",
            "finish_and_movement": "string"
          }
        ],
        "embellishment_plan": {
          "techniques": ["string"],
          "density": "string",
          "placement": ["string"],
          "motifs": ["string"],
          "restraint_notes": "string"
        },
        "coverage_and_drape": {
          "sleeves": "string",
          "neckline": "string",
          "back_and_midriff": "string",
          "head_covering": "string",
          "dupatta_or_saree_drape": "string"
        },
        "cultural_context": {
          "regional_direction": "string-or-null",
          "interpretation_notes": ["string"],
          "safeguards": ["string"]
        },
        "styling_notes": ["string"],
        "construction_caveats": ["string"],
        "image_alt_text": "string",
        "created_at": "ISO-8601"
      }
    }

Do not expose:

- `source_selections`;
- questionnaire answers;
- inspiration selections;
- image prompt;
- prompt-builder version;
- DesignSpec provider/model;
- token counts;
- provider prediction ID;
- provider name;
- model name;
- seed;
- image parameters;
- staged metadata;
- storage keys;
- hashes;
- internal byte sizes;
- user ID;
- DesignSession ID;
- questionnaire version UUID;
- signed URLs.

Use documentation-only DRF serializers in the existing
`apps/api/sitara/designs/openapi.py` convention and runtime payload functions
in the current design serializer/service layer.

Do not hand-maintain TypeScript result types.

## 4. Expose the latest public job on design detail

Extend `DesignDetailResponse` additively with:

    "latest_job": GenerationJob | null

It must use exactly the existing public `GenerationJob` shape and expose no
additional attempt fields.

Select the latest attempt deterministically using:

- newest `created_at`;
- UUID as the tie-breaker.

This supports durable navigation when:

- a browser returns to a generating design;
- the original generation POST succeeded but its response was lost;
- a generated design is revisited through `/design/<id>`;
- a non-editable failed design with a linked version is revisited.

Do not add job data to design-list responses.

Update stale comments that currently claim the design detail never exposes any
generation-attempt information. It now exposes only one sanitised public job
snapshot and still exposes no private provenance.

Avoid unnecessary queries by selecting or prefetching the latest public job
where the current view structure makes that appropriate.

## 5. Add a reliable image-download URL

The current Phase 11 image endpoint issues inline original and thumbnail URLs.
Extend it additively so the original image also has a separately signed
attachment URL:

    {
      "images": {
        "original": {
          "url": "inline-signed-url",
          "download_url": "attachment-signed-url",
          "width": 1536,
          "height": 2048
        },
        "thumbnail": {
          "url": "inline-signed-url",
          "width": 384,
          "height": 512
        },
        "expires_at": "ISO-8601"
      }
    }

Requirements:

- all three URLs share the same declared expiry;
- attachment signing uses GET only;
- response content type is `image/webp`;
- attachment filename is the fixed server-owned
  `sitara-concept.webp`;
- no user-controlled title or filename enters signing;
- URLs are not persisted, cached or logged;
- the filesystem backend still returns the existing controlled 503;
- the existing inline original and thumbnail behaviour remains compatible.

Refactor the existing signer narrowly, for example with an allowlisted
`inline|attachment` disposition argument. Do not duplicate the whole signing
service.

Do not add an image proxy.

## 6. OpenAPI

Document:

- the result endpoint;
- the result response;
- `latest_job` on design detail;
- the original image `download_url`;
- all controlled 404/409/503 responses.

Derive the `GenerationJob.error_code` OpenAPI enum from the backend
`GENERATION_ERROR_CODES` allowlist rather than documenting it as an arbitrary
string. It remains nullable.

Regenerate and commit:

- `apps/api/openapi/schema.json`;
- `apps/web/src/api/schema.d.ts`.

The generated GET runtime client must remain GET-only.

## 7. Part A tests

Test at least:

### Result endpoint

- anonymous owner receives the complete curated result;
- authenticated owner receives it;
- anonymous workspace promoted on login retains access;
- another anonymous browser receives 404;
- another account receives 404;
- nonexistent design and version receive identical 404;
- a version from another owned design cannot be mixed into the URL;
- failed GET creates no workspace;
- incomplete version returns 409;
- missing permanent image returns 409;
- unsupported DesignSpec schema returns controlled 503;
- corrupt DesignSpec returns controlled 503;
- unsafe stored narrative returns controlled 503;
- response has `Cache-Control: no-store`;
- response contains every documented user-facing section;
- response omits `source_selections`;
- response omits prompt/provider/token/storage/staging provenance;
- errors and logs do not contain result narrative.

### Latest job

- no attempts produces `latest_job: null`;
- latest attempt is selected deterministically;
- only the public job shape is exposed;
- no provider/storage/private attempt fields leak;
- design-list payload remains unchanged.

### Download URL

- signer receives `attachment`;
- safe fixed filename is used;
- inline and attachment URLs share one expiry;
- secret keys never appear in the URL;
- URL is never persisted or logged;
- filesystem storage remains controlled 503;
- existing Phase 11 signing tests remain green.

Commit Part A as:

    feat(results): add private design result API

# Part B — Generation start and progress flow

## 8. Add TanStack Query

Add exactly:

    "@tanstack/react-query": "5.101.2"

to `apps/web/package.json`.

Regenerate `apps/web/package-lock.json` using the repository's npm and Node
toolchain.

Requirements:

- exact version pin;
- no unrelated dependency upgrades;
- deterministic second lock generation;
- `npm ci` succeeds;
- no Query Devtools dependency;
- no persistence plugin.

## 9. Add the application provider

Create:

    apps/web/src/app/providers.tsx

It must be a client component that owns one `QueryClient` per mounted browser
application and composes the existing `AuthProvider`.

Update `apps/web/src/app/layout.tsx` to use the new provider.

Requirements:

- query data remains memory-only;
- no localStorage;
- no sessionStorage;
- no IndexedDB;
- no server-side singleton QueryClient;
- no query-cache persistence;
- no signed URL persistence;
- no provider credentials in query metadata;
- conservative global defaults;
- feature-specific polling/refetch rules remain with the relevant query.

## 10. Harden generation API reads

Retain the existing central wrapper location:

    apps/web/src/lib/api.ts

Do not create a second generic API client.

Harden `fetchGenerationJob` so it validates the runtime response shape rather
than casting arbitrary JSON.

Distinguish safely between:

- owned job not found;
- temporary transport/backend unavailability;
- malformed response.

Never expose raw response bodies or backend exception messages.

Add:

    fetchDesignResult(designId, designVersionId)

using generated OpenAPI types and strict runtime shape validation.

Update `fetchDesignImageUrls` for `download_url`.

Do not expose arbitrary request headers or a generic unsafe transport API.

## 11. Enable generation from ReviewSummary

Update the existing:

    apps/web/src/features/questionnaire/ReviewSummary.tsx

Preserve all current distinctions between:

- genuine validation failure;
- validation service unavailable;
- design unavailable;
- design not found.

Also fetch the public configuration.

The "Generate my concept" button is enabled only when:

- authoritative design validation succeeded;
- `generation_enabled` is true;
- no start request is currently in flight.

When generation is disabled, show accurate wording:

- generation is not currently available;
- do not claim demo generation exists before Phase 15;
- do not suggest that adding a key would enable it.

### Idempotency behaviour

On the first deliberate Generate click:

1. create one UUID using `crypto.randomUUID()`;
2. retain it in component memory;
3. call the existing `startDesignGeneration`;
4. disable repeated clicks while pending;
5. after confirmed 202, route to:

       /design/<design-id>/generation/<job-id>

If the request fails due to timeout, network failure or malformed response:

- show an accessible retry action;
- retry with the exact same idempotency key;
- do not mint another key.

Reset the key only after a definitive outcome proving that no replay is
required.

A double click must never send two generation requests.

If the start response is:

- `generation_in_progress`;
- `design_already_generated`;

refetch the design detail and use `latest_job` to resume the correct progress
or result route where possible.

If no coherent latest job exists, show a controlled conflict state rather than
guessing an ID.

Never store the idempotency key in browser storage.

## 12. Lifecycle-aware design navigation

The existing route:

    apps/web/src/app/design/[designId]/page.tsx

currently always renders the questionnaire.

Update the current questionnaire load/navigation flow so owned designs resolve
as follows:

- `draft` → questionnaire;
- `generation_failed` with no linked DesignVersion → questionnaire remains
  editable;
- `generating` with a coherent latest job → progress route;
- `generated` with a latest succeeded job and DesignVersion ID → result route;
- `generation_failed` with a linked DesignVersion → failed progress route;
- inconsistent lifecycle data → controlled unavailable state.

Use `router.replace` for lifecycle redirects so Back does not bounce between a
stale wizard and the durable status page.

Apply equivalent protection to the review page so refreshing it after
generation starts cannot offer a second Generate action.

Avoid redirect loops.

Do not add a public design lookup or weaken ownership.

## 13. Progress route and feature structure

Add:

    apps/web/src/app/design/[designId]/generation/[jobId]/page.tsx

Suggested focused feature structure:

    apps/web/src/features/generation/
      GenerationProgress.tsx
      generation-errors.ts
      generation-status.ts
      GenerationProgress.test.tsx
      generation-errors.test.ts

Do not move questionnaire files unnecessarily.

## 14. TanStack Query polling

Use TanStack Query for the job.

Query key must include the job UUID.

Verify that the fetched job's `design_id` matches the route's `designId`.
A mismatch is treated as not found; never redirect using IDs from a mismatched
payload.

Polling schedule:

- less than 10 seconds since job creation: every 1 second;
- 10–30 seconds: every 2 seconds;
- after 30 seconds: every 5 seconds;
- terminal `succeeded` or `failed`: stop polling.

Use the server's `created_at` to calculate the polling band.

Requirements:

- `refetchIntervalInBackground: false`;
- refresh promptly when the tab becomes active again;
- no polling after unmount;
- no polling after terminal status;
- no fake percentage;
- no invented completion estimate;
- no permanent job storage;
- temporary fetch failures use bounded query retry/backoff;
- a temporary fetch failure is not rendered as a terminal generation failure;
- a manual "Try again" action refetches the same job.

A 404 displays an indistinguishable "Generation not found" state.

## 15. Progress presentation

Render the durable states honestly:

### queued

- heading: preparing your concept;
- explanation that the job is waiting to start.

### running_text

- heading: creating your design brief;
- explanation that the selected details are being converted into a structured
  concept.

### running_image

- heading: creating your visual concept;
- explanation that the image is being generated, verified and stored
  privately;
- do not expose the provider name or internal ingest stage.

### succeeded

Require a non-null `design_version_id`.

Use `router.replace` to:

    /design/<design-id>/result/<design-version-id>

If `succeeded` has a null or malformed version ID, stop polling and show a
controlled invalid-state message.

### failed

Render the source-controlled friendly message for the stable error code.

The progress UI must:

- use a semantic ordered stage list;
- mark the active/completed stage without relying on colour alone;
- use `aria-current="step"` where suitable;
- use `role="status"` and `aria-live="polite"` for changing progress copy;
- use `role="alert"` for terminal failures;
- avoid constant announcements on every poll when the visible status did not
  change;
- provide a link back to the questionnaire only when recovery editing is
  meaningful;
- explain that private design data is not made public during generation.

## 16. Exhaustive friendly error mapping

Create one source-controlled frontend map derived from:

    NonNullable<GenerationJob["error_code"]>

Use a TypeScript `satisfies Record<...>` or equivalent exhaustive check.

Cover every current backend error code:

- `queue_unavailable`;
- `generation_unavailable`;
- `design_incomplete`;
- `design_changed`;
- `structured_generation_failed`;
- `structured_submission_ambiguous`;
- `structured_provider_refused`;
- `prompt_build_failed`;
- `image_provider_unavailable`;
- `image_submission_ambiguous`;
- `image_prediction_failed`;
- `image_prediction_canceled`;
- `image_prediction_aborted`;
- `image_poll_timeout`;
- `image_download_failed`;
- `image_output_invalid`;
- `image_staging_failed`;
- `image_staging_unverified`;
- `image_ingest_failed`;
- `image_ingest_unverified`;
- `internal_generation_error`.

Messages must:

- use plain user-facing language;
- never mention Anthropic, Replicate, model IDs, predictions, storage keys,
  hashes or billing internals;
- distinguish editable questionnaire problems from technical failures;
- explain ambiguous-submission states without encouraging an automatic second
  generation;
- explain storage/ingest failures as safe preparation failures rather than
  pretending no image work occurred;
- include one unknown-code fallback for runtime defence.

Tests must fail when the generated error-code union gains an unmapped value.

## 17. Part B tests

Test at least:

### Review generation

- valid + generation enabled enables the button;
- generation disabled keeps it disabled with accurate copy;
- invalid design keeps it disabled;
- validation unavailable does not enable it;
- double click submits once;
- retry after transport failure reuses the exact key;
- confirmed success routes to the job;
- in-progress conflict uses latest job to resume;
- generated design uses latest version to reach results;
- no local/session/IndexedDB storage is touched.

### Lifecycle navigation

- draft renders the wizard;
- generating redirects to progress;
- generated redirects to results;
- editable failed design remains in the wizard;
- failed design with a linked version redirects to failed progress;
- inconsistent lifecycle state fails safely;
- no redirect loop.

### Progress

- queued state;
- running-text state;
- running-image state;
- succeeded redirect;
- succeeded-without-version failure;
- failed state;
- temporary fetch outage;
- job not found;
- route/payload design mismatch;
- polling intervals back off at the documented boundaries;
- terminal states stop polling;
- background polling is disabled;
- unchanged status does not repeatedly announce itself;
- all stable error codes render friendly copy.

Commit Part B as:

    feat(frontend): add generation progress flow

# Part C — Private concept results page

## 18. Results route and feature structure

Add:

    apps/web/src/app/design/[designId]/result/[versionId]/page.tsx

Suggested focused structure:

    apps/web/src/features/results/
      DesignResult.tsx
      DesignBrief.tsx
      ResultImage.tsx
      result-brief.ts
      result-errors.ts
      DesignResult.test.tsx
      ResultImage.test.tsx
      result-brief.test.ts

Keep the code small and cohesive. Do not introduce a generic page-builder or
design-system layer.

## 19. Independent result and image queries

The results page uses two separate queries:

1. the stable result payload;
2. the short-lived signed-image payload.

This separation is required because image delivery may be temporarily
unavailable while the validated design brief remains readable.

### Result query

- fetch once while mounted;
- no interval polling;
- no browser persistence;
- `gcTime: 0` or explicit removal on unmount for private result data;
- a 409 shows "result still being prepared";
- a 503 shows a controlled retryable result-service error;
- a 404 shows an indistinguishable not-found state.

### Image query

- start only after the result payload is valid;
- use `fetchDesignImageUrls`;
- no browser persistence;
- no module-level cache;
- `gcTime: 0` or explicit removal when the page unmounts;
- `refetchIntervalInBackground: false`;
- refetch on window focus;
- keep result text usable when image delivery fails.

## 20. Signed URL refresh

Phase 12 owns refreshing the Phase 11 bearer URLs while the page stays open.

Calculate refresh from the returned `expires_at`, not from a hard-coded TTL.

Refresh at approximately 80% of the observed remaining lifetime, with:

- a minimum positive delay;
- no tight refresh loop;
- no refresh after unmount;
- no background-tab interval;
- immediate refresh on focus when the URL is near expiry;
- validation that `expires_at` is a real future timestamp.

When the current URLs expire and refresh failed:

- stop rendering the expired image URL;
- show a controlled retry action;
- keep the result brief visible.

On image load failure:

- attempt one signed-URL refresh for that URL;
- do not create an infinite refetch loop.

Never put signed URLs into:

- localStorage;
- sessionStorage;
- IndexedDB;
- cookies;
- route parameters;
- query strings controlled by Sitara;
- logs;
- analytics;
- copied design-brief text.

## 21. Render the complete DesignSpec-derived result

Render every user-facing section returned by the result API:

1. title;
2. concept summary;
3. garment breakdown:
   - overall form;
   - garment components;
   - silhouette;
   - drape/layering;
   - key proportions;
4. colour story:
   - palette summary;
   - placement;
   - rationale;
5. fabrics and texture;
6. embellishment plan:
   - techniques;
   - density;
   - placement;
   - motifs;
   - restraint notes;
7. coverage and drape:
   - sleeves;
   - neckline;
   - back and midriff;
   - head covering;
   - dupatta or saree drape;
8. cultural context:
   - regional direction when present;
   - interpretation notes;
   - safeguards;
9. styling notes;
10. construction caveats.

Use semantic:

- headings;
- sections;
- lists;
- description lists where suitable.

React's normal escaping remains enabled. Do not use `dangerouslySetInnerHTML`.

Do not render `source_selections` or internal provenance.

## 22. Result image

Use a plain HTML `<img>`, not `next/image`, because the URL is:

- short-lived;
- signed;
- dynamically hosted;
- deliberately not part of Next's remote-image cache.

Requirements:

- `src` uses the current original inline URL;
- `alt` uses the DesignSpec-derived `image_alt_text`;
- width and height come from the image API;
- responsive sizing preserves aspect ratio;
- `referrerPolicy="no-referrer"`;
- no URL appears in accessible text;
- no URL appears in error messages;
- loading and failure states are accessible;
- opening the full-size image uses `rel="noreferrer noopener"` where a new tab
  is used.

The thumbnail may be used as a lightweight placeholder or secondary preview,
but do not add a blur-generation dependency.

## 23. Prominent disclaimers

Place a concise disclaimer block near the page heading, before the detailed
brief and without requiring the user to search at the bottom of the page.

It must state:

- this is an AI-assisted visual concept;
- it is not a photograph of a finished garment;
- it is concept visualisation only;
- it is not a sewing pattern;
- it does not guarantee constructibility;
- colours, materials and fine details may differ when interpreted physically.

Also render the DesignSpec's own `construction_caveats` in the detailed result.

Do not claim cultural or historical authenticity beyond the generated
specification.

## 24. Copy and download actions

Create one pure deterministic formatter:

    formatDesignBrief(result) -> string

The formatted brief must include:

- title;
- concept summary;
- every rendered DesignSpec section;
- construction caveats;
- the generic concept-only disclaimer.

It must not include:

- IDs;
- signed URLs;
- source selections;
- questionnaire answers;
- provider details;
- storage metadata;
- prompt text.

### Copy brief

Use the Clipboard API.

Requirements:

- explicit user click;
- accessible success/failure status;
- no automatic clipboard access;
- no copy of signed URLs;
- no raw HTML.

### Download brief

Create a client-side UTF-8 text file using:

    sitara-design-brief.txt

Use a fixed filename, not the generated title.

Revoke the temporary object URL immediately after use.

### Download image

Use the signed `original.download_url`.

Requirements:

- fixed safe filename;
- no backend proxy;
- no fetch-to-Blob requirement;
- no signed URL persistence;
- `referrerPolicy="no-referrer"`;
- accessible link text;
- disabled/hidden while the signed URL is unavailable or expired.

## 25. Result error states

Provide distinct accessible states for:

- loading result;
- result not found;
- result still being prepared;
- result service unavailable;
- malformed result response;
- image URLs loading;
- image not ready;
- image delivery unavailable;
- malformed image response;
- expired signed URLs;
- copy failure;
- download action unavailable.

Do not turn image-delivery failure into total result-page failure.

Do not display backend exception messages.

## 26. Styling

Extend the current vanilla CSS in:

    apps/web/src/app/globals.css

Do not introduce Tailwind, shadcn, CSS-in-JS or another component library.

Add only focused styles for:

- progress stages;
- active/completed/error indicators;
- result header;
- disclaimer callout;
- responsive image container;
- result section grid;
- fabrics and styling lists;
- action buttons;
- copy status;
- mobile layouts.

Preserve:

- existing colour variables;
- visible focus indicators;
- reduced-motion behaviour;
- readable single-column mobile layout.

Do not perform the full Phase 17 visual redesign.

## 27. Part C tests

Test at least:

### Result rendering

- all result sections render from one representative fixture;
- multiple fabric entries render;
- nullable regional direction is handled;
- cultural safeguards render;
- construction caveats render;
- generic disclaimers are near the heading;
- image alt text is exact;
- no source selection or internal provenance appears;
- no `dangerouslySetInnerHTML`;
- malicious-looking text is rendered as text.

### Signed URLs

- image query begins only after result success;
- original URL is used for the image;
- download URL is used for image download;
- thumbnail and original dimensions are respected;
- refresh occurs before expiry using fake timers;
- refresh stops on unmount;
- background polling is disabled;
- focus near expiry triggers refresh;
- malformed expiry is rejected;
- expired URL is no longer rendered;
- one image load failure triggers one refresh;
- repeated image failure does not loop;
- image delivery failure leaves the brief visible;
- query cache does not retain URLs after unmount;
- no browser storage is touched;
- `no-referrer` attributes are present.

### Actions

- copy text contains every section;
- copy text excludes URLs and IDs;
- copy success and failure are announced;
- brief download uses the fixed filename;
- object URL is revoked;
- image download uses the current attachment URL;
- actions are unavailable when URLs have expired.

### API wrappers

- result shape validation;
- malformed result body rejection;
- image `download_url` validation;
- controlled 404/409/503 mapping;
- generated OpenAPI types are used;
- no duplicate hand-maintained wire interfaces.

Commit Part C as:

    feat(frontend): add private concept results page

# Documentation

## 28. Decision record

Create:

    docs/decisions/0013-generation-progress-and-results.md

Record:

- dedicated private result endpoint;
- purpose-built result payload rather than raw DesignVersion exposure;
- DesignSpec revalidation before delivery;
- latest public job on design detail for durable resume navigation;
- TanStack Query as an in-memory polling mechanism;
- polling backoff and terminal-stop rules;
- why polling is used instead of WebSockets/SSE;
- no fake percentages;
- exhaustive stable-error mapping;
- independent result and signed-image queries;
- short-lived URL refresh while mounted;
- signed URLs remain temporary bearer URLs;
- attachment signing for image download;
- no browser persistence of private result data or URLs;
- plain `<img>` instead of Next image optimisation;
- copy and text-download behaviour;
- concept-only and constructibility disclaimers;
- Phase 14 owns refinement;
- Phase 15 owns demo flow;
- Phase 16 owns live cost controls and retention;
- Phase 17 owns the complete accessibility/visual polish pass.

## 29. Update current documentation

Update:

- `README.md`;
- `docs/PROPOSAL.md`;
- `docs/phases/PHASES.md`;
- `docs/phases/phases-12.md`;
- ADR 0012 only where the additive attachment URL changes its signed-delivery
  contract;
- `CLAUDE.md` only for genuinely permanent privacy or signed-URL rules.

Do not rewrite historical Phase 2 evidence.

Do not mark the Phase 10 paid checkpoint complete.

Do not claim demo generation exists before Phase 15.

# Validation

## 30. Dependency and build

Run:

    docker compose config
    docker compose build api web
    docker compose up -d
    docker compose exec api python -m pip check
    docker compose exec web npm ci

Regenerate the npm lock twice and prove the second run has no diff.

## 31. Backend

Run:

    docker compose exec api python manage.py check
    docker compose exec api python manage.py makemigrations --check --dry-run
    docker compose exec api python manage.py migrate
    docker compose exec api pytest
    docker compose exec api ruff check .
    docker compose exec api ruff format --check .

No model change is expected in Phase 12. Do not create an empty migration.

## 32. OpenAPI

Run:

    docker compose exec api python manage.py spectacular \
      --format openapi-json \
      --file openapi/schema.json \
      --validate \
      --fail-on-warn

Then:

    git diff --exit-code -- apps/api/openapi/schema.json

after the deliberate regenerated schema has been staged or committed.

Run:

    docker compose exec web npm run generate:api

Then prove no uncommitted generated-type drift:

    git diff --exit-code -- apps/web/src/api/schema.d.ts

## 33. Frontend

Run:

    docker compose exec web npm run lint
    docker compose exec web npm run typecheck
    docker compose exec web npm test -- --run
    docker compose exec web npm run build

Use fake timers where polling/expiry timing is tested. Do not add arbitrary
real sleeps.

## 34. Existing contracts

Run:

    docker compose exec api python manage.py export_design_spec_schema

Prove no drift:

    git diff --exit-code -- apps/api/sitara/generation/schemas/design_spec_v1.json

Run prompt snapshot tests and image-processor golden tests.

Run:

    docker compose exec api pytest \
      sitara/questionnaire/tests/test_fixture_versions.py

Confirm questionnaire v2 remains draft.

## 35. Celery and provider-free fixture

Run the health ping:

    docker compose exec api python -c \
      "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"

Confirm:

- generation task remains registered;
- worker listens to `generation,celery`;
- fixture generation completes through permanent ingest;
- exactly one DesignVersion exists;
- zero Anthropic client construction;
- zero Replicate client construction;
- socket-denial tests remain green.

## 36. Phase 2 integrity

Run from `experiments/model-eval`:

    .venv/Scripts/python -m pytest tests/test_model_decision.py -q

Confirm:

    git diff -- experiments/model-eval/outputs/

is empty.

# Manual checkpoint

Use MinIO/S3-compatible design-image storage because filesystem delivery
deliberately returns 503.

Keep all provider gates closed.

1. Start PostgreSQL, Redis, MinIO, API, web and worker.
2. Complete a synthetic questionnaire Design without provider calls.
3. Run the existing provider-free fixture generation command with a fixed
   idempotency key.
4. Record only the safe attempt UUID and DesignVersion UUID printed by the
   command.
5. Open:

       /design/<design-id>/generation/<attempt-id>

6. Confirm the terminal succeeded job routes to:

       /design/<design-id>/result/<version-id>

7. Confirm:
   - the private original image renders;
   - the result title and every specification section render;
   - the prominent disclaimers appear near the heading;
   - construction caveats appear in the detailed brief;
   - Copy brief copies no URL or internal identifier;
   - Download brief produces `sitara-design-brief.txt`;
   - Download image uses `sitara-concept.webp`;
   - the signed URLs refresh while the page remains open;
   - leaving the page removes signed URLs from the query cache;
   - returning issues fresh URLs;
   - another browser/session receives 404 from the result and image endpoints;
   - an already-issued bearer URL remains usable only until its expiry;
   - no provider call occurs.

8. Temporarily exercise image-delivery unavailability and confirm the brief
   remains readable.
9. Confirm the normal default review page accurately says generation is
   unavailable while `LIVE_GENERATION_ENABLED=false`.
10. Do not open the live generation gates merely to test the button.

Intermediate queued/running states may be validated through deterministic
component tests; do not mutate a real paid attempt or call providers for a
visual checkpoint.

# Integrity requirements

Before the phase can be approved, confirm:

- zero Anthropic calls;
- zero Replicate calls;
- no provider client instantiated in CI;
- no questionnaire v1 change;
- questionnaire v2 remains draft;
- no prompt snapshot drift;
- no image-processor golden drift;
- no Phase 2 evidence change;
- no Docker volume deletion;
- no raw DesignSpec endpoint;
- no `source_selections` in the result response;
- no provider/storage provenance in result or job responses;
- no signed URL in design detail, result payload or job payload;
- no signed URL persisted or logged;
- no signed URL in browser storage;
- no idempotency key in browser storage;
- no Next image cache for signed images;
- no public design route;
- no image proxy;
- no CDN;
- no service worker;
- no WebSocket/SSE infrastructure;
- no refinement;
- no demo fixture selection;
- no rate-limit or spend-control implementation;
- `LIVE_GENERATION_ENABLED` remains false by default;
- hosted CI is green after push.

# Pull request

Use a phase branch such as:

    phase/phase-12-results-page

Open a draft pull request into `main` with a title such as:

    phase-12: generation progress and private concept results

Do not merge it.

# Final response

Return only:

1. phase branch;
2. Part A full SHA;
3. Part B full SHA;
4. Part C full SHA;
5. private result endpoint and response shape;
6. DesignSpec validation and result-readiness behaviour;
7. latest-job resume behaviour;
8. signed attachment URL change;
9. TanStack Query version and provider setup;
10. generation start/idempotency behaviour;
11. polling/backoff behaviour;
12. stable error-code mapping;
13. lifecycle redirects;
14. result route and rendered sections;
15. signed-URL refresh and cache-removal behaviour;
16. copy/download behaviour;
17. accessibility and disclaimer behaviour;
18. backend test results;
19. frontend test results;
20. OpenAPI/generated-type drift;
21. Celery and fixture-pipeline results;
22. questionnaire lifecycle result;
23. Phase 2 integrity result;
24. zero-provider-call confirmation;
25. manual checkpoint results;
26. council decisions and resolved findings;
27. independent Codex decision;
28. hosted CI status;
29. draft PR URL;
30. unresolved issues.
