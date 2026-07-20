/run-phase phase-14

Create `docs/phases/phases-14.md` from the complete specification below, then
implement it end-to-end using the globally installed phase-council workflow.

Do not stop after creating the requirements file. Plan the phase, implement it
in the specified reviewed slices, run the six-member council after every
commit, resolve all blocking findings, run the full-phase council and
independent Codex review, push the phase branch, run hosted CI, fix failures,
and open a draft pull request into `main`.

Never merge the pull request and never commit directly to `main`.

# Sitara Phase 14 — Single-round constrained refinement

Expected starting commit:

3543e6c3f95425e4f642f6fdfc8e652af873e322

Before changing anything, confirm that the current `main` is this commit or a
clean descendant containing the merged Phase 13 implementation. Report any
unexpected application-code commits before proceeding.

## Main objective

Allow the owner of a successfully generated bridal concept to request exactly
one constrained refinement.

The refinement flow is:

    existing validated DesignSpec
      + one allowlisted refinement category
      + one short optional user note
      -> structured DesignSpec edit
      -> deterministic prompt rebuild
      -> fresh text-to-image generation
      -> canonical private image ingest
      -> side-by-side version comparison

This is NOT image editing.

The refined image is a completely fresh text-to-image generation. Reusing the
original seed may provide limited continuity, but does not preserve:

- pose;
- face;
- body;
- composition;
- camera angle;
- background;
- garment construction;
- embroidery placement;
- fabric folds;
- fine details.

The UI and documentation must explain this honestly before the user submits a
refinement and while the two versions are displayed.

## Read first

Read the current files instead of relying on older roadmap assumptions:

- `CLAUDE.md`
- `.claude/phase-council.json`
- `.claude/review/README.md`
- `README.md`
- `docs/PROPOSAL.md`
- `docs/phases/PHASES.md`
- `docs/phases/phases-8.md`
- `docs/phases/phases-9.md`
- `docs/phases/phases-10.md`
- `docs/phases/phases-11.md`
- `docs/phases/phases-12.md`
- `docs/phases/phases-13.md`
- `docs/decisions/0001-image-model.md`
- `docs/decisions/0004-private-design-ownership.md`
- `docs/decisions/0009-structured-design-spec-generation.md`
- `docs/decisions/0010-deterministic-image-prompt-builder.md`
- `docs/decisions/0011-asynchronous-generation-pipeline.md`
- `docs/decisions/0012-private-design-image-storage.md`
- `docs/decisions/0013-generation-progress-and-results.md`
- `docs/decisions/0014-inspiration-metadata-influence.md`
- `apps/api/config/settings.py`
- `apps/api/sitara/designs/models.py`
- `apps/api/sitara/designs/services.py`
- `apps/api/sitara/designs/jobs.py`
- `apps/api/sitara/designs/result.py`
- `apps/api/sitara/designs/serializers.py`
- `apps/api/sitara/designs/openapi.py`
- `apps/api/sitara/designs/views.py`
- `apps/api/sitara/designs/urls.py`
- `apps/api/sitara/generation/design_spec.py`
- `apps/api/sitara/generation/context.py`
- `apps/api/sitara/generation/inspiration_context.py`
- `apps/api/sitara/generation/input_safety.py`
- `apps/api/sitara/generation/prompting.py`
- `apps/api/sitara/generation/services.py`
- `apps/api/sitara/generation/prompt_builder.py`
- `apps/api/sitara/generation/prompt_service.py`
- `apps/api/sitara/generation/pipeline.py`
- `apps/api/sitara/generation/tasks.py`
- `apps/api/sitara/generation/errors.py`
- `apps/api/sitara/ai_gateway/structured_design.py`
- `apps/api/sitara/ai_gateway/image_generation.py`
- `apps/api/sitara/media/ingest.py`
- `apps/web/src/app/design/[designId]/page.tsx`
- `apps/web/src/app/design/[designId]/generation/[jobId]/page.tsx`
- `apps/web/src/app/design/[designId]/result/[versionId]/page.tsx`
- `apps/web/src/features/generation/GenerationProgress.tsx`
- `apps/web/src/features/results/DesignResult.tsx`
- `apps/web/src/features/results/DesignBrief.tsx`
- `apps/web/src/features/results/ResultImage.tsx`
- `apps/web/src/features/results/result-brief.ts`
- `apps/web/src/lib/api.ts`
- `apps/web/src/api/schema.d.ts`
- `experiments/model-eval/src/model_eval/prompt_formats.py`
- `experiments/model-eval/prompts/briefs.yaml`

Use the current repository layout:

- Django: `apps/api/sitara/...`
- Next.js App Router: `apps/web/src/app/...`
- frontend features: `apps/web/src/features/...`
- browser API wrappers: `apps/web/src/lib/api.ts`
- generated OpenAPI types: `apps/web/src/api/schema.d.ts`
- phase documents: `docs/phases/...`
- decision records: `docs/decisions/...`

Do not introduce the old:

- `backend/`;
- `frontend/`;
- `docs/PHASES.md`;
- `docker-compose.yml`;
- local phase-agent paths;
- local phase-skill paths.

## Commit boundaries

Implement this phase as four focused commits:

1. `feat(refinement): add versioned refinement request provenance`
2. `feat(refinement): add constrained DesignSpec edit service`
3. `feat(refinement): add durable asynchronous refinement pipeline`
4. `feat(frontend): add single refinement and version comparison`

Do not combine them.

Each part must pass its focused validation before the next begins.

## Non-goals

Do not implement:

- more than one refinement;
- arbitrary multi-turn chat;
- image-to-image editing;
- reference-image conditioning;
- sending the original generated image to Anthropic;
- sending the original generated image to Replicate;
- ControlNet;
- inpainting;
- pose preservation;
- face preservation;
- composition locking;
- user-uploaded images;
- a free-form field-path editing API;
- arbitrary JSON Patch;
- public sharing;
- showcase galleries;
- demo fixture matching;
- live rate limits;
- live cost ceilings;
- retention or purge;
- deployment;
- provider-model changes;
- questionnaire v2 activation.

Make zero live Anthropic and Replicate calls during implementation, automated
tests, review, CI and the offline checkpoint.

Keep:

- `DEMO_MODE=true`;
- `ALLOW_PAID_AI_CALLS=false`;
- `LIVE_GENERATION_ENABLED=false`.

Do not run:

    docker compose down --volumes

# Baseline

Run:

    git status --short
    git log -15 --oneline
    git rev-parse HEAD
    docker compose config
    docker compose up -d
    docker compose ps

Run the exact current commands from `.claude/phase-council.json`.

Confirm:

- questionnaire v1 fingerprint is unchanged;
- questionnaire v2 remains draft;
- `DESIGN_SPEC_SCHEMA_VERSION == 1`;
- `SPEC_TEMPLATE_VERSION == "2.0.0"`;
- `PROMPT_BUILDER_VERSION == "3.0.0"`;
- `INSPIRATION_CONTEXT_SCHEMA_VERSION == 1`;
- `DESIGN_IMAGE_PROCESSOR_VERSION == "1.0.0"`;
- `MAX_DESIGN_VERSIONS == 2`;
- the provider-free initial generation fixture succeeds;
- no Phase 2 evidence is modified.

# Part A — Versioned refinement request provenance

## 1. Strict refinement request contract

Create a focused module such as:

    apps/api/sitara/generation/refinement.py

Define:

    REFINEMENT_REQUEST_SCHEMA_VERSION = 1

Create a strict Pydantic v2 contract equivalent to:

    {
      "schema_version": 1,
      "change_type": "colour_story",
      "note": "Use a softer blush and champagne balance."
    }

Use:

    ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

Allow exactly one `change_type` from:

- `colour_story`;
- `fabric_and_texture`;
- `embellishment`;
- `sleeves_and_coverage`;
- `neckline`;
- `dupatta_or_saree_drape`;
- `silhouette_detail`;
- `styling_details`.

Do not allow:

- garment type changes;
- ceremony changes;
- broad regional or religious identity changes;
- a second refinement category;
- arbitrary field paths;
- nested client JSON;
- provider parameters;
- model names;
- seeds;
- image URLs;
- storage keys.

The `note` must be:

- optional;
- normalised with Unicode NFKC;
- CRLF/CR normalised to LF;
- internal whitespace collapsed;
- outer whitespace stripped;
- plain text only;
- maximum 300 characters;
- rejected when it contains:
  - designer or brand references;
  - imitation language;
  - URLs;
  - prompt/system leakage;
  - HTML or Markdown;
  - disallowed control characters;
  - sewing instructions;
  - measurements;
  - pattern-making instructions.

An empty note is valid. The selected chip still supplies the constrained edit
category.

Provide:

    normalise_refinement_request(...)
    refinement_request_canonical_json(...)
    refinement_request_sha256(...)

Canonical JSON requirements:

- UTF-8;
- sorted object keys;
- compact separators;
- deterministic;
- no timestamps;
- no user/session identity;
- no machine-dependent data.

## 2. Define the allowed DesignSpec edit surface

Create one source-controlled mapping from each `change_type` to the exact
DesignSpec paths that may change.

Suggested allowlist:

### `colour_story`

May change:

- `title`;
- `concept_summary`;
- `colour_story`;
- fabric descriptions only where necessary to describe the new colour finish;
- `styling_notes`;
- `image_alt_text`.

Must not change `source_selections`.

### `fabric_and_texture`

May change:

- `title`;
- `concept_summary`;
- `fabrics_and_texture`;
- compatible garment texture descriptions;
- compatible colour-story rationale;
- `styling_notes`;
- `construction_caveats`;
- `image_alt_text`.

### `embellishment`

May change:

- `title`;
- `concept_summary`;
- `embellishment_plan`;
- compatible fabric finish descriptions;
- compatible styling notes;
- `construction_caveats`;
- `image_alt_text`.

### `sleeves_and_coverage`

May change:

- `title`;
- `concept_summary`;
- `coverage_and_drape.sleeves`;
- `coverage_and_drape.back_and_midriff`;
- compatible garment component descriptions;
- compatible styling notes;
- `construction_caveats`;
- `image_alt_text`.

Must never reduce coverage when the request or note is ambiguous.

### `neckline`

May change:

- `title`;
- `concept_summary`;
- `coverage_and_drape.neckline`;
- compatible garment component descriptions;
- compatible embellishment placement;
- `construction_caveats`;
- `image_alt_text`.

### `dupatta_or_saree_drape`

May change:

- `title`;
- `concept_summary`;
- `coverage_and_drape.head_covering`;
- `coverage_and_drape.dupatta_or_saree_drape`;
- compatible garment drape/layering descriptions;
- compatible styling notes;
- `construction_caveats`;
- `image_alt_text`.

### `silhouette_detail`

May change:

- `title`;
- `concept_summary`;
- narrative silhouette, proportions and layering descriptions;
- compatible garment components;
- compatible fabric movement descriptions;
- `construction_caveats`;
- `image_alt_text`.

It must not change the canonical garment-type machine value.

### `styling_details`

May change:

- `title`;
- `concept_summary`;
- `styling_notes`;
- compatible cultural interpretation notes;
- `image_alt_text`.

It must not change the garment itself.

The exact mapping may be refined after inspecting the current DesignSpec, but
it must stay:

- explicit;
- narrow;
- source-controlled;
- unit-tested;
- free of wildcard field paths.

## 3. Preserve immutable baseline fields

Every refinement must preserve exactly:

- `schema_version`;
- the complete `source_selections` object;
- unrequested DesignSpec fields;
- the original inspiration-context snapshot;
- the original inspiration-context schema version;
- the original inspiration-context hash.

Do not rebuild inspiration metadata from the current catalogue for a
refinement.

The source version’s persisted historical inspiration snapshot is
authoritative. This means later catalogue retirement or rights expiry does not
silently rewrite an already-generated concept.

Do not send inspiration image bytes or URLs during refinement.

## 4. Extend DesignVersion lineage

Add to `DesignVersion`:

    parent_version
    refinement_request
    refinement_request_schema_version
    refinement_request_sha256

Suggested fields:

- `parent_version`: nullable self-FK using `PROTECT`,
  `related_name="refined_versions"`;
- `refinement_request`: nullable JSONField;
- `refinement_request_schema_version`: nullable positive small integer;
- `refinement_request_sha256`: `CharField(max_length=64, blank=True)`.

Rules:

- version 1 has no parent and no refinement request;
- version 2 must have parent version 1 and complete refinement provenance;
- parent must belong to the same Design;
- parent version number must be lower than child version number;
- a version cannot parent itself;
- no refinement chain beyond one child is allowed by application code;
- existing versions remain valid.

Database constraints where expressible:

1. refinement provenance is all absent or all present;
2. refinement schema version is exactly 1 when present;
3. refinement hash is blank or exactly 64 lowercase hexadecimal characters;
4. refinement provenance requires a parent;
5. a parent requires refinement provenance;
6. version 1 cannot have a parent;
7. version 2 must have a parent;
8. higher version numbers remain rejected by the application-level
   `MAX_DESIGN_VERSIONS=2` rule rather than a migration-dependent hard-coded
   database maximum.

Cross-row same-Design and ordering checks belong in the locking service and
tests where a database `CHECK` cannot reference another row.

Make all lineage and refinement fields read-only in Django admin.

## 5. Extend GenerationAttempt kind and source

Add:

    generation_kind
    source_design_version
    seed_reused

Suggested values:

    initial
    refinement

Suggested fields:

- `generation_kind`: choices, default `initial`;
- `source_design_version`: nullable FK to `DesignVersion`, `PROTECT`,
  related name such as `refinement_attempts`;
- `seed_reused`: BooleanField default false.

Rules:

- an initial attempt has no source version;
- a refinement attempt requires a source version;
- source version belongs to the same Design;
- source version has a complete successful result;
- source version must be version 1;
- a refinement attempt can output only version 2;
- `seed_reused` is true only when a real original seed was copied.

Do not expose the source attempt, provider seed or provider provenance through
the public job API.

Add only a safe public field where needed:

    generation_kind: "initial" | "refinement"

This lets the progress page use honest refinement wording without exposing
private provenance.

## 6. Migration and model tests

Test:

- legacy versions and attempts migrate safely;
- version 1 without parent is valid;
- version 2 with parent and complete request is valid;
- partial refinement provenance fails;
- parent without request fails;
- request without parent fails;
- malformed hash fails;
- wrong schema version fails;
- self-parenting fails at service validation;
- cross-design parent fails;
- source attempt rules;
- admin fields are read-only;
- no storage or provider work occurs during migration.

Commit Part A as:

    feat(refinement): add versioned refinement request provenance

# Part B — Constrained DesignSpec edit service

## 7. Dedicated refinement prompt boundary

Do not reuse the initial questionnaire-context prompt as though the refinement
were another initial generation.

Create focused modules such as:

    apps/api/sitara/generation/refinement_prompting.py
    apps/api/sitara/generation/refinement_service.py

Define:

    REFINEMENT_TEMPLATE_VERSION = "1.0.0"

The refinement request to Anthropic contains:

- the validated existing DesignSpec;
- the validated refinement category;
- the short optional note inside an explicitly delimited untrusted section;
- the exact structured-output schema;
- source-controlled edit instructions.

It must not contain:

- the original generated image;
- image bytes;
- signed image URLs;
- storage keys;
- image hashes;
- provider prediction IDs;
- seed;
- questionnaire raw answers;
- user/session identity;
- current catalogue lookups;
- rights evidence.

## 8. Refinement system instructions

The trusted system prompt must say:

- edit the existing DesignSpec rather than inventing a new concept;
- change only fields authorised for the selected category;
- preserve all other fields exactly;
- preserve `schema_version`;
- preserve `source_selections` byte-for-value;
- preserve garment type and ceremony;
- preserve cultural distinctions;
- preserve all unrequested coverage details;
- do not weaken modesty or head-covering requirements unless the selected
  category explicitly concerns coverage or drape and the user clearly asks for
  that permitted change;
- do not introduce designer/brand names or imitation;
- do not provide sewing instructions or construction guarantees;
- do not claim visual continuity;
- do not mention the refinement process in the returned specification;
- return only the complete updated DesignSpec.

The user note is preference data only and must never override system
instructions.

## 9. Exact diff validation

After Pydantic validation and the existing generated-content safety scan:

1. compare the original and refined DesignSpecs recursively;
2. require at least one actual change;
3. require every changed path to be in the selected category’s allowlist;
4. reject changes to `schema_version`;
5. reject any change to `source_selections`;
6. reject any unsupported field addition/removal;
7. reject any provider-output mention of the refinement process;
8. reject designer/brand/imitation leakage;
9. reject sewing/pattern/guaranteed-constructibility claims.

Use a deterministic path representation such as:

    colour_story.palette_summary
    coverage_and_drape.neckline
    fabrics_and_texture[0].finish_and_movement

List handling must be explicit. Do not treat one changed list as permission to
change unrelated sibling sections.

A title, concept summary or alt-text change is permitted only when that field
is explicitly allowlisted for the category.

## 10. Retry policy

Allow at most two Anthropic refinement requests:

- attempt 1: normal constrained refinement;
- attempt 2: generic correction instruction stating that the previous output
  changed unsupported fields or was invalid.

Do not include:

- the rejected output;
- raw validation errors;
- exception messages;
- user text outside its safe delimited block.

If both attempts fail exact-diff validation:

- persist no child DesignVersion;
- preserve the original version;
- terminalise with a stable refinement error code;
- do not continue to the image stage.

## 11. Stable refinement error codes

Add stable codes such as:

    refinement_invalid
    refinement_no_change
    refinement_generation_failed
    refinement_limit_reached
    refinement_source_unavailable

Use them consistently across:

- services;
- pipeline;
- API;
- OpenAPI;
- frontend error mapping.

Do not expose provider exception bodies.

Distinguish:

- invalid client refinement request;
- no valid change generated;
- technical structured-generation failure;
- limit already reached;
- source version unavailable/corrupt.

## 12. Atomic refined-version persistence

Create the child DesignVersion in one short transaction after the provider
request.

Under locks:

1. lock the Design;
2. lock the source DesignVersion;
3. revalidate the source DesignSpec;
4. verify source permanent image provenance;
5. revalidate the persisted inspiration context and hash;
6. verify there is still no child version;
7. verify `MAX_DESIGN_VERSIONS` has not been reached;
8. verify the canonical refinement request and hash still match the attempt;
9. persist version 2;
10. persist:
    - refined DesignSpec;
    - structured generation provenance;
    - parent version;
    - refinement request and hash;
    - copied historical inspiration context;
11. link the GenerationAttempt;
12. clear the text-submission marker.

Do not hold database locks across the provider call.

If anything changed after the provider call, persist no child and do not retry
the provider.

## 13. DesignSpec schema and template versions

Keep:

    DESIGN_SPEC_SCHEMA_VERSION = 1

A refinement returns the same DesignSpec shape.

Do not bump the initial-generation:

    SPEC_TEMPLATE_VERSION = "2.0.0"

The refinement prompt has its own:

    REFINEMENT_TEMPLATE_VERSION = "1.0.0"

Persist the refinement template version as the child version’s
`design_spec_template_version`, using a clearly namespaced value if the
existing field requires distinguishing prompt families, for example:

    refinement-1.0.0

Document the exact convention.

Do not weaken existing DesignSpec schema export checks.

## 14. Fixture tests

Use injected fake providers.

Test:

- each refinement category;
- one allowed field change;
- multiple allowed changes in one category;
- unrelated field change rejected;
- source selections changed by provider rejected;
- garment type change rejected;
- ceremony change rejected;
- no-op output rejected;
- unsafe output rejected;
- invalid first output then valid second output;
- two invalid outputs fail safely;
- optional note absent;
- optional note present and delimited;
- prompt injection in note rejected before provider construction;
- raw note is never logged;
- exact original inspiration snapshot copied;
- no catalogue query needed to rebuild inspiration data;
- no image bytes or URLs sent to Anthropic;
- source version unchanged after every outcome.

Commit Part B as:

    feat(refinement): add constrained DesignSpec edit service

# Part C — Durable asynchronous refinement pipeline

## 15. Refinement endpoint

Add:

    POST /api/v1/designs/<design-uuid>/refine/

Use:

- `SessionAuthentication`;
- `AllowAny`;
- explicit Django CSRF protection;
- JSON-only parsing;
- ownership filtering before UUID lookup;
- indistinguishable 404 for missing or foreign designs;
- `Cache-Control: no-store`;
- required `Idempotency-Key` UUID header.

Request:

    {
      "source_version_id": "uuid",
      "change_type": "colour_story",
      "note": "Use a softer blush and champagne balance."
    }

Unknown fields are rejected.

Responses:

- `202` — public job payload;
- `400 validation_failed`;
- `403` — CSRF failure;
- `404 not_found`;
- `409 refinement_limit_reached`;
- `409 refinement_in_progress`;
- `409 refinement_source_unavailable`;
- `409 design_not_refinable`;
- `503 generation_unavailable`;
- `503 queue_unavailable`.

A repeated idempotency key for the same Design returns the same attempt and
must enqueue no additional work.

The same key may be used independently on another Design.

## 16. Enqueue preconditions

Before creating an attempt, require:

- owned Design;
- Design status supports refinement;
- source version belongs to the Design;
- source version is version 1;
- source version has:
  - valid DesignSpec;
  - complete prompt provenance;
  - complete permanent image provenance;
  - valid inspiration-context provenance where present;
- no version 2 exists;
- no queued/running attempt exists;
- no prior successful refinement exists;
- request is valid and safe;
- live generation capability is available.

Perform all no-spend checks before selecting or constructing any provider.

Create the refinement attempt and move the Design to `generating` in a short
Design-row-locked transaction.

Submit the deterministic Celery task using `transaction.on_commit`.

## 17. Pipeline branching

Extend the existing resumable pipeline without duplicating it.

The text stage branches by `generation_kind`:

### Initial

Retain the current initial DesignSpec generation path unchanged.

### Refinement

Use:

- the persisted source version;
- the persisted canonical refinement request;
- the constrained refinement service.

After the child DesignVersion is linked, reuse the existing stages:

- deterministic image-prompt build;
- image submission;
- polling;
- download;
- private raw staging;
- canonical permanent ingest;
- success finalisation.

Do not create a parallel image pipeline.

## 18. Seed reuse

For refinement:

1. find the succeeded initial GenerationAttempt linked to the source version;
2. require that it belongs to the same Design;
3. copy its persisted non-negative `image_seed` when present;
4. set `seed_reused=true`;
5. persist the seed before image submission.

If no usable source seed exists:

- generate one new secure non-negative seed;
- set `seed_reused=false`;
- proceed without claiming continuity.

Never expose the seed publicly.

Never derive a seed from:

- user data;
- IDs;
- prompt text;
- hashes.

A retry or worker redelivery must reuse the refinement attempt’s already
persisted seed and never choose another.

## 19. Resume and duplicate-delivery guarantees

A refinement redelivery must:

- never repeat Anthropic when the child DesignVersion is linked;
- never rebuild an existing stored prompt;
- never create a second provider prediction when prediction ID exists;
- treat an in-flight submission without a durable ID as ambiguous;
- verify staged output rather than downloading again where possible;
- verify final objects rather than regenerating them;
- never create version 3;
- never overwrite version 1 or version 2;
- retain the same idempotency key, request hash and seed.

Use the existing GenerationAttempt advisory lock.

## 20. Design lifecycle

During a refinement:

- Design moves `generated -> generating`;
- version 1 remains readable and its image remains accessible;
- successful version 2 ingest moves Design back to `generated`;
- a terminal refinement failure moves Design to `generation_failed`;
- version 1 remains readable after failure;
- no failed refinement may delete or corrupt version 1.

A later retry with a new idempotency key is permitted only when:

- no version 2 exists;
- prior spend is conclusively resolved under the existing conservative rules;
- no ambiguous text/image submission marker exists;
- no recoverable staged or permanent output exists.

Do not weaken the Phase 10 conservative spend semantics.

## 21. Public job payload

Add:

    generation_kind

to the existing public job response.

Do not expose:

- source version ID unless the frontend genuinely needs it;
- refinement request text;
- refinement hash;
- parent relation;
- seed;
- seed reuse;
- prompt;
- provider/model/prediction;
- storage provenance.

The existing `design_version_id` continues to identify the output version once
available.

## 22. Result payload lineage

Extend the curated result payload additively with:

    "lineage": {
      "kind": "initial",
      "parent_version_id": null,
      "refinement": null
    }

For version 2:

    "lineage": {
      "kind": "refinement",
      "parent_version_id": "uuid",
      "refinement": {
        "change_type": "colour_story"
      }
    }

Do not expose the raw optional note in the result API.

The note may contain private user-authored preference text and is not necessary
for display.

Do not expose:

- refinement hash;
- schema version;
- provider template version;
- seed;
- source attempt;
- internal provenance.

Validate persisted refinement provenance and hash before returning the result.
Corrupt lineage returns controlled `503 design_result_unavailable`.

Legacy versions return initial lineage.

## 23. OpenAPI

Document:

- refinement endpoint;
- request shape;
- controlled errors;
- `generation_kind` on jobs;
- result lineage;
- all enums.

Regenerate and commit:

- `apps/api/openapi/schema.json`;
- `apps/web/src/api/schema.d.ts`.

Do not hand-maintain duplicate frontend wire types.

## 24. Pipeline tests

Test at least:

- successful refinement creates version 2;
- second refinement rejected;
- concurrent refinement requests admit exactly one;
- idempotency replay returns the same attempt;
- foreign source version rejected;
- source version 2 rejected;
- incomplete source rejected;
- version 1 remains readable while refinement runs;
- successful child uses parent version 1;
- historical inspiration snapshot copied exactly;
- prompt builder runs against refined spec;
- prompt builder version remains 3.0.0;
- seed copied when available;
- new seed created when unavailable;
- redelivery reuses seed;
- linked child skips text provider;
- accepted prediction skips resubmission;
- staged output skips redownload;
- final image skips reprocessing where complete;
- failed refinement preserves version 1;
- no version 3;
- ambiguous submission blocks unsafe retry;
- no image-to-image input;
- no source-image bytes or URLs sent to Replicate;
- no live provider calls in tests.

Commit Part C as:

    feat(refinement): add durable asynchronous refinement pipeline

# Part D — Refinement UI and version comparison

## 25. Frontend API wrappers

Extend the existing:

    apps/web/src/lib/api.ts

Add:

    startDesignRefinement(
      designId,
      request,
      idempotencyKey,
    )

Use generated OpenAPI types and strict runtime response validation.

Reuse the same:

- same-origin transport;
- in-memory CSRF token;
- retry-once CSRF behaviour;
- five-second request timeout;
- typed not-found/conflict/unavailable errors.

Do not create a second generic API client.

Do not store the refinement request or idempotency key in:

- localStorage;
- sessionStorage;
- IndexedDB;
- cookies.

## 26. Refinement panel

Add a focused feature structure such as:

    apps/web/src/features/refinement/
      RefinementPanel.tsx
      RefinementProgress.tsx
      VersionComparison.tsx
      refinement-options.ts
      refinement-errors.ts
      RefinementPanel.test.tsx
      VersionComparison.test.tsx

Show the refinement panel only when:

- the result is version 1;
- no version 2 exists;
- no refinement is running;
- refinement generation is available.

The user selects exactly one refinement chip from:

- Colour story;
- Fabric and texture;
- Embellishment;
- Sleeves and coverage;
- Neckline;
- Dupatta or saree drape;
- Silhouette detail;
- Styling details.

Provide an optional note field:

- maximum 300 characters;
- remaining-character count;
- plain text;
- associated label and help text;
- validation before submission.

Do not provide an unrestricted chat box.

## 27. Pre-submit drift warning

Before enabling submission, display prominent copy equivalent to:

> Refinement creates a fresh AI-generated image. Sitara will ask for only your
> selected change, but the pose, composition, face, garment details and
> embroidery placement may still differ substantially. Reusing the original
> seed is only a continuity aid, not a guarantee.

The user must explicitly acknowledge this with a checkbox before submission.

The acknowledgement is UI-only and must not be persisted as legal consent.

The submit button is enabled only when:

- one chip is selected;
- note is valid;
- warning is acknowledged;
- no request is pending;
- generation is available.

## 28. Frontend idempotency

On the first deliberate refinement submission:

1. create one `crypto.randomUUID()` key;
2. retain it in component memory;
3. disable duplicate submission;
4. call the refinement endpoint;
5. route to the existing generation progress route.

On timeout, transport failure or malformed response:

- show an accessible retry action;
- reuse the exact same idempotency key;
- do not mint a new key.

Reset it only after a definitive non-replay outcome.

A double click must submit once.

## 29. Refinement progress

Reuse the current generation progress infrastructure.

When:

    generation_kind == "refinement"

render refinement-specific text:

### queued

    Preparing your refinement

### running_text

    Updating your design brief

### running_image

    Creating your refined visual concept

Explain that:

- only the requested brief change is being applied;
- the image is still a fresh generation;
- the original concept remains private and available.

Do not show:

- fake percentages;
- provider names;
- seed reuse;
- internal storage stages.

On success, route to the output version result.

## 30. Version comparison

When viewing version 2, use `lineage.parent_version_id` to load version 1.

Use independent queries for:

- version 1 result;
- version 1 signed image URLs;
- version 2 result;
- version 2 signed image URLs.

Preserve the existing short-lived URL rules:

- memory-only;
- `gcTime: 0`;
- no browser persistence;
- refresh from each returned expiry;
- no background interval;
- no logged URLs;
- no signed URL in route state.

Render a responsive side-by-side comparison:

- Original concept — version 1;
- Refined concept — version 2.

Each side shows:

- image;
- title;
- concept summary;
- key relevant brief sections;
- link or control to view the complete brief.

The detailed brief must remain available for both versions.

On mobile, stack versions vertically in chronological order.

Do not visually imply pixel-level continuity.

## 31. Comparison disclosure

Place prominent text near the comparison heading:

- the refined image is a new generation;
- visual drift is expected;
- only the DesignSpec edit is constrained;
- seed reuse does not guarantee the same pose, composition or garment details.

Display the selected refinement category in human-readable form.

Do not display the raw optional note.

## 32. Result-page lifecycle

### Viewing version 1 before refinement

Show the refinement panel where eligible.

### Refinement running

Keep version 1 readable.

Show a link to the running refinement job where the Design detail/latest job
data indicates one exists.

### Refinement succeeded

Lifecycle navigation should prefer version 2 as the latest result.

### Refinement failed

Version 1 remains readable.

Show a controlled refinement-failure state and only offer retry when the
backend permits it.

Do not redirect the user away from an accessible original result merely because
the Design status is `generation_failed`.

Update the Phase 12 lifecycle routing carefully to distinguish:

- failed initial generation with no version;
- failed initial generation with partial version;
- failed refinement with a complete version 1.

Avoid redirect loops.

## 33. Refined brief presentation

The existing full DesignBrief renderer should work for both versions.

For version 2, add a small non-live-region label:

    Refined concept

The copy/download brief formatter must add:

- version number;
- refinement category;
- fresh-generation drift disclaimer.

It must not add:

- raw refinement note;
- IDs;
- hashes;
- seed;
- provider data;
- signed URLs.

Keep the fixed filename:

    sitara-design-brief.txt

## 34. Accessibility

Requirements:

- refinement chips use a proper radio-group or equivalent single-selection
  semantics;
- keyboard selection works;
- note label and counter are associated;
- drift acknowledgement checkbox has visible descriptive text;
- pending state is announced once;
- errors use `role="alert"`;
- progress changes use polite live regions;
- version headings are semantic;
- comparison order is logical on mobile and desktop;
- no meaning relies only on colour;
- focus moves to the progress/error heading after submission outcome where
  appropriate.

Do not perform the full Phase 17 visual redesign.

## 35. Styling

Extend only:

    apps/web/src/app/globals.css

Add focused styles for:

- refinement chip group;
- note field and counter;
- drift warning;
- acknowledgement control;
- comparison layout;
- version cards;
- mobile stacking;
- version labels.

Do not add:

- Tailwind;
- shadcn;
- CSS-in-JS;
- a component library.

## 36. Frontend tests

Test at least:

### Panel

- shown on eligible version 1;
- hidden on version 2;
- hidden when limit reached;
- hidden when generation disabled;
- one chip only;
- 300-character limit;
- acknowledgement required;
- double click submits once;
- retry reuses idempotency key;
- no browser storage touched;
- accurate drift warning visible.

### Progress

- refinement-specific queued copy;
- running-text copy;
- running-image copy;
- success redirect;
- failure handling;
- no fake percentage;
- original remains accessible.

### Comparison

- loads both versions from lineage;
- original and refined headings;
- correct images and alt text;
- both complete briefs available;
- mobile ordering remains original then refined;
- selected category displayed;
- raw note absent;
- drift disclaimer visible;
- one image-delivery failure does not hide the other brief;
- signed URL refresh remains independent;
- caches clear on unmount;
- no browser storage touched.

### Lifecycle

- failed initial generation still follows existing recovery;
- failed refinement preserves version 1 route;
- generated design prefers version 2;
- no redirect loop.

Commit Part D as:

    feat(frontend): add single refinement and version comparison

# Documentation

## 37. Create ADR 0015

Create:

    docs/decisions/0015-single-round-refinement.md

Record:

- exactly one refinement;
- `MAX_DESIGN_VERSIONS=2`;
- strict refinement request schema;
- one allowlisted category per request;
- optional bounded untrusted note;
- exact DesignSpec diff allowlists;
- same DesignSpec schema version;
- separate refinement template version;
- parent-child DesignVersion lineage;
- historical inspiration snapshot copied from version 1;
- one durable asynchronous pipeline reused for both generation kinds;
- seed reuse when available;
- seed reuse is not a continuity guarantee;
- fresh text-to-image generation;
- no image-to-image editing;
- no original image sent to either provider;
- version 1 remains readable during and after refinement failure;
- side-by-side comparison;
- honest visual-drift copy;
- raw refinement note is not exposed in result payloads;
- Phase 2 refinement evaluation remains incomplete;
- image editing requires a later scoped evaluation and separate decision.

## 38. Update existing documentation

Update:

- `README.md`;
- `docs/PROPOSAL.md`;
- `docs/phases/PHASES.md`;
- `docs/phases/phases-14.md`;
- ADR 0001 with the Phase 14 implementation status;
- ADR 0009 with the separate refinement structured-output prompt;
- ADR 0010 clarifying the same deterministic image-prompt builder handles both
  versions;
- ADR 0011 with the refinement pipeline branch and resume rules;
- ADR 0013 with version comparison behaviour;
- ADR 0014 clarifying historical inspiration context is copied into a refined
  version;
- `CLAUDE.md` only for genuinely permanent refinement invariants.

Do not rewrite historical Phase 2 evidence.

Do not state that image continuity has been validated.

Do not mark the Phase 10 paid checkpoint complete.

# Validation

## 39. Build and dependencies

No dependency change is expected.

Run:

    docker compose config
    docker compose build api web
    docker compose up -d
    docker compose exec api python -m pip check
    docker compose exec web npm ci

Confirm no dependency or lockfile drift.

## 40. Backend

Run:

    docker compose exec api python manage.py check
    docker compose exec api python manage.py makemigrations --check --dry-run
    docker compose exec api python manage.py migrate
    docker compose exec api pytest
    docker compose exec api ruff check .
    docker compose exec api ruff format --check .

A DesignVersion/GenerationAttempt migration is expected.

## 41. DesignSpec and prompt guards

Run:

    docker compose exec api python manage.py export_design_spec_schema

Prove:

    git diff --exit-code -- apps/api/sitara/generation/schemas/design_spec_v1.json

Run existing prompt-builder snapshots.

Confirm:

- DesignSpec schema remains version 1;
- initial spec template remains 2.0.0;
- refinement template is 1.0.0;
- prompt builder remains 3.0.0;
- existing initial prompt snapshots remain unchanged;
- new refined-spec prompt fixtures have reviewed snapshots only if their
  DesignSpec content intentionally differs.

## 42. OpenAPI and frontend

Run:

    docker compose exec api python manage.py spectacular \
      --format openapi-json \
      --file openapi/schema.json \
      --validate \
      --fail-on-warn

Run:

    docker compose exec web npm run generate:api
    docker compose exec web npm run lint
    docker compose exec web npm run typecheck
    docker compose exec web npm test -- --run
    docker compose exec web npm run build

After committing deliberate generated outputs, prove no remaining drift in:

- `apps/api/openapi/schema.json`;
- `apps/web/src/api/schema.d.ts`.

## 43. Existing lifecycle contracts

Run:

    docker compose exec api pytest \
      sitara/questionnaire/tests/test_fixture_versions.py

Confirm:

- questionnaire v1 fingerprint unchanged;
- questionnaire v2 remains draft.

Run:

- inspiration-context tests;
- Phase 13 provider-boundary tests;
- image-processor golden tests;
- initial generation pipeline tests;
- signed-delivery tests;
- result API tests;
- generation progress tests.

Confirm:

- inspiration context schema remains 1;
- image processor remains 1.0.0;
- no regression in initial generation.

## 44. Celery

Run:

    docker compose exec api python -c \
      "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"

Confirm:

- generation task remains registered;
- worker listens to `generation,celery`;
- initial attempts still use the initial branch;
- refinement attempts use the refinement branch;
- image-only refinement resume never calls the text provider;
- duplicate deliveries do not create duplicate versions or predictions.

## 45. Provider-free fixture journey

Extend or add a provider-free fixture command that can run:

1. initial DesignSpec fixture;
2. initial prompt;
3. initial synthetic image staging and ingest;
4. constrained refinement fixture;
5. refined prompt;
6. refined synthetic image staging and ingest.

It must produce exactly:

- version 1;
- version 2;
- one parent-child relationship;
- one refinement request snapshot;
- one initial attempt;
- one refinement attempt.

Print only safe fields:

- Design UUID;
- version UUIDs;
- attempt UUIDs;
- statuses;
- version numbers;
- refinement category;
- seed-reused boolean;
- image dimensions.

Do not print:

- prompts;
- refinement note;
- answers;
- inspiration metadata;
- hashes;
- storage keys;
- signed URLs;
- provider metadata;
- seed.

## 46. Phase 2 integrity

From `experiments/model-eval` run:

    .venv/Scripts/python -m pytest tests/test_model_decision.py -q

Confirm:

    git diff -- experiments/model-eval/outputs/

is empty.

Do not run a live refinement experiment automatically.

# Offline manual checkpoint

Keep all provider gates closed.

Use injected fixtures and synthetic images only.

1. Create a complete design and run the provider-free initial pipeline.
2. Open version 1 in the browser.
3. Confirm the refinement panel appears.
4. Select one refinement category.
5. Enter a short safe note.
6. Confirm submission remains disabled until the drift warning is acknowledged.
7. Submit the provider-free refinement fixture.
8. Confirm:
   - exactly one refinement attempt;
   - exactly one version 2;
   - version 2 parents version 1;
   - historical inspiration context is copied exactly;
   - only allowed DesignSpec paths changed;
   - the original version is untouched;
   - the same prompt builder generated the version 2 prompt;
   - no image-to-image input exists;
   - no original image was read for provider input;
   - seed was reused when available;
   - the refined image was ingested under its own deterministic version key.
9. Confirm the progress screen uses refinement wording.
10. Confirm success routes to version 2.
11. Confirm the comparison displays version 1 and version 2.
12. Confirm the visual-drift warning is prominent.
13. Confirm copy/download brief excludes:
    - raw note;
    - IDs;
    - hashes;
    - seed;
    - provider data;
    - URLs.
14. Attempt a second refinement and confirm server-side rejection.
15. Simulate a terminal refinement failure and confirm version 1 remains
    readable.
16. Confirm zero Anthropic calls.
17. Confirm zero Replicate calls.
18. Confirm no provider clients are constructed.

# Paid refinement checkpoint — pending

Do not run this checkpoint without separate explicit user authorisation and a
fixed budget.

It is not required for code merge because public live generation remains
disabled. It is required before Sitara makes any claim about refinement
continuity or before public live refinement is enabled.

When separately authorised:

1. Re-verify current Anthropic and Replicate pricing, terms and capabilities.
2. Run on private local infrastructure.
3. Generate one initial concept.
4. Apply one constrained refinement.
5. Compare:
   - requested change success;
   - unrelated DesignSpec changes;
   - pose drift;
   - composition drift;
   - garment-structure drift;
   - embroidery-placement drift;
   - face/body drift;
   - coverage adherence;
   - cultural coherence.
6. Record seed reuse truthfully.
7. Record that the output was a fresh generation.
8. Do not commit:
   - prompts;
   - user data;
   - provider IDs;
   - generated images;
   - signed URLs;
   - credentials;
   - billing details.
9. Update ADR 0015 only with safe aggregate observations.
10. Close all paid gates immediately afterward.

A poor or inconsistent result must be recorded honestly. It does not justify
silently enabling image editing.

# Integrity requirements

Before phase approval, confirm:

- zero Anthropic calls in tests and CI;
- zero Replicate calls in tests and CI;
- no provider client constructed in CI;
- exactly one refinement maximum;
- no version 3;
- no arbitrary JSON Patch;
- no arbitrary DesignSpec field path from the client;
- no raw refinement note in public result/job payloads;
- no original generated image sent to Anthropic;
- no original generated image sent to Replicate;
- no reference-image URL sent to Replicate;
- no image-to-image parameter;
- original version remains immutable;
- failed refinement preserves original result;
- source selections remain unchanged;
- inspiration snapshot copied exactly;
- DesignSpec schema remains version 1;
- initial spec template remains 2.0.0;
- refinement template is versioned separately;
- prompt builder remains 3.0.0;
- image processor remains 1.0.0;
- questionnaire v1 unchanged;
- questionnaire v2 remains draft;
- no Phase 2 evidence change;
- no Docker volume deletion;
- no demo fixture matching;
- no rate-limit or cost-ceiling implementation;
- no deployment work;
- `LIVE_GENERATION_ENABLED` remains false by default;
- hosted CI is green.

# Pull request

Use a phase branch such as:

    phase/phase-14-refinement

Open a draft pull request into `main` with a title such as:

    phase-14: single-round constrained refinement

Do not merge it.

# Final response

Return only:

1. phase branch;
2. Part A full SHA;
3. Part B full SHA;
4. Part C full SHA;
5. Part D full SHA;
6. refinement request schema and version;
7. allowed refinement categories;
8. exact DesignSpec diff enforcement;
9. DesignVersion lineage fields;
10. GenerationAttempt refinement fields;
11. migration and constraints;
12. refinement endpoint and response contract;
13. pre-spend validation;
14. refinement prompt and template version;
15. retry behaviour;
16. atomic child-version persistence;
17. seed reuse behaviour;
18. pipeline resume and idempotency behaviour;
19. stable refinement error codes;
20. public result lineage;
21. frontend refinement panel;
22. drift acknowledgement behaviour;
23. refinement progress wording;
24. version comparison behaviour;
25. copy/download behaviour;
26. backend test results;
27. frontend test results;
28. OpenAPI/generated-type drift;
29. Celery and fixture-journey results;
30. questionnaire lifecycle result;
31. Phase 2 integrity result;
32. zero-provider-call confirmation;
33. offline checkpoint results;
34. paid checkpoint status;
35. council decisions and resolved findings;
36. independent Codex decision;
37. hosted CI status;
38. draft PR URL;
39. unresolved issues.