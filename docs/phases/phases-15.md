# Sitara Phase 15 — Deterministic zero-cost demo pipeline

Known main commit before the Phase 15 roadmap correction:

    c87209ba85e48f759ac126a5af5883207ff9e315

Required starting point:

The latest `main` must be a clean descendant of that commit and must contain
the merged documentation revision titled approximately:

    docs(phases): revise deterministic Phase 15 demo architecture

If the documentation revision is not present on `main`, stop and report that
the roadmap PR must be merged first.

Report any unexpected application-code commits before proceeding.

# Main objective

Make the complete Sitara journey usable in `DEMO_MODE=true` with:

- zero paid calls;
- zero Anthropic calls;
- zero Replicate calls;
- zero external provider network requests;
- no construction of an Anthropic or Replicate client;
- deterministic outputs;
- honest user-facing demo labelling;
- the same application pipeline used by live generation.

The supported demo journey must include:

1. questionnaire completion;
2. optional approved inspiration selection;
3. generation submission;
4. queued, text and image progress;
5. deterministic DesignSpec creation;
6. deterministic image-prompt creation;
7. deterministic curated demo-image selection;
8. raw image staging;
9. canonical permanent image processing;
10. private result retrieval;
11. signed image delivery;
12. one constrained refinement;
13. version comparison.

The only substituted components are:

- live Anthropic structured generation;
- live Replicate image prediction and download.

Everything before and after those boundaries remains the normal production
application path.

# Required architecture

    validated questionnaire
              |
              v
    normal generation context and safety checks
              |
              v
    deterministic local DesignSpec adapter
              |
              v
    normal DesignSpec validation and atomic persistence
              |
              v
    normal deterministic prompt builder
              |
              v
    versioned deterministic demo-asset selector
              |
              v
    asynchronous local demo image adapter
              |
              v
    normal staging, verification and canonical ingest
              |
              v
    normal private results and signed-image delivery

Refinement must follow:

    version 1 DesignSpec
       + validated allowlisted refinement
       -> deterministic local constrained edit
       -> existing exact DesignSpec diff validation
       -> normal prompt rebuild
       -> deterministic asset selection
       -> normal image pipeline
       -> version 2 comparison

This is not a frontend mock and not a fake-result shortcut.

# Read first

Read the current versions of:

- `CLAUDE.md`
- `.claude/phase-council.json`
- `.claude/review/README.md`
- `README.md`
- `.env.example`
- `compose.yaml`
- `.github/workflows/ci.yml`
- `docs/PROPOSAL.md`
- `docs/phases/PHASES.md`
- `docs/phases/phases-8.md`
- `docs/phases/phases-9.md`
- `docs/phases/phases-10.md`
- `docs/phases/phases-11.md`
- `docs/phases/phases-12.md`
- `docs/phases/phases-13.md`
- `docs/phases/phases-14.md`
- `docs/decisions/0002-application-foundation.md`
- `docs/decisions/0006-rights-controlled-inspiration-catalogue.md`
- `docs/decisions/0009-structured-design-spec-generation.md`
- `docs/decisions/0010-deterministic-image-prompt-builder.md`
- `docs/decisions/0011-asynchronous-generation-pipeline.md`
- `docs/decisions/0012-private-design-image-storage.md`
- `docs/decisions/0013-generation-progress-and-results.md`
- `docs/decisions/0014-inspiration-metadata-influence.md`
- `docs/decisions/0015-single-round-refinement.md`
- `apps/api/config/settings.py`
- `apps/api/sitara/ai_gateway/policy.py`
- `apps/api/sitara/ai_gateway/providers.py`
- `apps/api/sitara/ai_gateway/structured_design.py`
- `apps/api/sitara/ai_gateway/image_generation.py`
- `apps/api/sitara/designs/models.py`
- `apps/api/sitara/designs/serializers.py`
- `apps/api/sitara/designs/views.py`
- `apps/api/sitara/designs/jobs.py`
- `apps/api/sitara/designs/result.py`
- `apps/api/sitara/designs/openapi.py`
- `apps/api/sitara/generation/context.py`
- `apps/api/sitara/generation/design_spec.py`
- `apps/api/sitara/generation/fixture_provider.py`
- `apps/api/sitara/generation/image_fixtures.py`
- `apps/api/sitara/generation/services.py`
- `apps/api/sitara/generation/refinement.py`
- `apps/api/sitara/generation/refinement_service.py`
- `apps/api/sitara/generation/prompt_builder.py`
- `apps/api/sitara/generation/prompt_service.py`
- `apps/api/sitara/generation/pipeline.py`
- `apps/api/sitara/generation/tasks.py`
- `apps/api/sitara/generation/errors.py`
- `apps/api/sitara/generation/management/commands/run_generation_fixture.py`
- the public configuration endpoint and its tests;
- `apps/web/src/lib/api.ts`
- `apps/web/src/api/schema.d.ts`
- the landing page;
- questionnaire review components;
- generation progress components;
- result components;
- refinement components;
- version-comparison components.

Use the current repository structure. Do not reintroduce:

- `backend/`;
- `frontend/`;
- `docker-compose.yml`;
- `docs/PHASES.md`;
- local phase-agent copies;
- local phase-skill copies.

# Commit boundaries

Implement as five reviewed commits:

1. `feat(demo): add versioned demo asset manifest and selector`
2. `feat(demo): add deterministic design and refinement engines`
3. `feat(demo): route the asynchronous pipeline through local adapters`
4. `feat(frontend): add honest deterministic demo experience`
5. `docs(phase-15): record deterministic demo-mode decisions`

Do not combine the commits.

Each commit must pass its focused validation and council review before
proceeding.

# Non-goals

Do not implement:

- a demo-only generate endpoint;
- a demo-only result endpoint;
- frontend mock responses;
- direct insertion of successful DesignVersions;
- bypassing Celery;
- bypassing ownership or CSRF;
- bypassing idempotency;
- bypassing image staging or canonical ingest;
- public showcase or gallery endpoints;
- landing-page image carousels;
- paid Anthropic or Replicate calls;
- production rate limits;
- cost ceilings;
- spend reconciliation;
- retention or purge;
- deployment;
- user-uploaded inspiration;
- direct reference-image conditioning;
- image-to-image editing;
- a new live model;
- automatic download or scraping of demo assets;
- fabricated image rights;
- automatic paid creation of the production demo pack;
- questionnaire v2 activation.

# Safety settings

During implementation, tests, review, CI and checkpoints keep:

    DEMO_MODE=true
    ALLOW_PAID_AI_CALLS=false
    LIVE_GENERATION_ENABLED=false

Tests must additionally prove that setting:

    DEMO_MODE=true
    ALLOW_PAID_AI_CALLS=true
    LIVE_GENERATION_ENABLED=true
    ANTHROPIC_API_KEY=<non-empty-test-value>
    REPLICATE_API_TOKEN=<non-empty-test-value>

still constructs no live client and makes no provider call.

Never use real credentials.

Never run:

    docker compose down --volumes

# Baseline

Run:

    git status --short
    git log -15 --oneline
    git rev-parse HEAD
    docker compose config
    docker compose up -d
    docker compose ps

Run the current commands from `.claude/phase-council.json`.

Confirm:

- Phases 1–14 are delivered;
- `DEMO_MODE` defaults true;
- `ALLOW_PAID_AI_CALLS` defaults false;
- `LIVE_GENERATION_ENABLED` defaults false;
- `DESIGN_SPEC_SCHEMA_VERSION == 1`;
- `SPEC_TEMPLATE_VERSION == "2.0.0"`;
- `REFINEMENT_TEMPLATE_VERSION == "1.0.0"`;
- `PROMPT_BUILDER_VERSION == "3.0.0"`;
- `INSPIRATION_CONTEXT_SCHEMA_VERSION == 1`;
- `REFINEMENT_REQUEST_SCHEMA_VERSION == 1`;
- `DESIGN_IMAGE_PROCESSOR_VERSION == "1.0.0"`;
- questionnaire v1 fingerprint is unchanged;
- questionnaire v2 remains draft;
- the existing offline fixture command works;
- no Phase 2 evidence is modified.

# Part A — Versioned demo asset manifest and selector

## 1. Focused module structure

Create a focused package such as:

    apps/api/sitara/generation/demo/
      __init__.py
      manifest.py
      selector.py
      storage.py
      synthetic_pack.py

Do not turn it into a separate Django service or microservice.

Define:

    DEMO_MANIFEST_SCHEMA_VERSION = 1
    DEMO_SELECTOR_VERSION = "1.0.0"

Use strict Pydantic v2 contracts with:

    ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

## 2. Manifest contract

Create a strict manifest equivalent to:

    {
      "schema_version": 1,
      "pack_id": "sitara-demo-v1",
      "assets": [
        {
          "asset_id": "lehenga-baraat-001",
          "filename": "lehenga-baraat-001.webp",
          "sha256": "<64 lowercase hex>",
          "size_bytes": 123456,
          "width": 1536,
          "height": 2048,
          "alt_text": "Accurate human-authored visual description.",
          "garment_types": ["lehenga"],
          "ceremonies": ["baraat", "reception"],
          "silhouettes": ["a_line"],
          "colours": ["deep_red", "antique_gold"],
          "fabrics": ["velvet", "silk"],
          "embellishment_styles": ["zardozi"],
          "embellishment_densities": ["heavy"],
          "coverage_preferences": ["full_sleeves"],
          "dupatta_styles": ["head_and_shoulder"],
          "saree_drapes": [],
          "regional_styles": [],
          "provenance_status": "verified_project_owned"
        }
      ]
    }

The exact option names must be derived from current questionnaire v1 machine
values. Do not invent incompatible machine IDs.

Requirements:

- unique `asset_id`;
- unique filename;
- unique content hash unless duplication is explicitly rejected;
- WebP only;
- portrait 3:4 output;
- bounded byte size and dimensions;
- accurate non-empty alt text;
- no path traversal;
- no absolute paths;
- no symlinks;
- no remote URLs;
- no arbitrary nested tags;
- no provider identifiers;
- no credentials;
- no storage keys in the committed manifest;
- no public rights evidence;
- no user or staff identity;
- no unverified provenance state accepted for an active pack.

Add canonical JSON and SHA-256 helpers.

## 3. Cultural and coverage validation

The production manifest validator must enforce:

- every supported garment has compatible assets;
- every supported ceremony is represented somewhere;
- gharara and sharara are distinct tags;
- saree assets use saree-compatible drape tags;
- non-saree assets do not claim saree drapes;
- garment tags cannot contradict one another;
- modest/full-coverage examples are represented;
- minimal and heavy embellishment are represented;
- a range of colours and fabrics is represented;
- regional labels are optional and may be used only where human-reviewed;
- one asset is not treated as universally compatible.

The target production pack is approximately 30–50 curated assets.

Do not weaken validation merely because that human-created pack is not yet
available.

## 4. Asset storage

Source demo assets must be imported into private object storage under
server-generated deterministic keys such as:

    demo-assets/<pack-id>/<manifest-hash>/<asset-id>.webp

The exact key builder must be centralised and unit-tested.

Never expose these source storage keys through:

- APIs;
- OpenAPI;
- frontend code;
- logs;
- exceptions.

The selected source image is copied through the normal generation staging and
canonical ingest process. A result image must use its normal DesignVersion
storage key, not the shared demo-source key.

## 5. Import command

Create a command such as:

    python manage.py install_demo_asset_pack \
      --manifest /private/path/manifest.json \
      --source-dir /private/path/assets

Also support:

    --verify-only

Requirements:

1. validate the complete manifest before writing;
2. verify every source file is inside `source-dir`;
3. reject symlinks and traversal;
4. decode every image;
5. reject corrupt, animated, oversized or malformed content;
6. strip metadata using the current safe processing primitives;
7. re-encode canonical RGB WebP where necessary;
8. verify dimensions, size and SHA-256;
9. upload to private storage;
10. verify every uploaded object by read-back;
11. activate nothing until the full pack succeeds;
12. clean up newly written partial objects on failure;
13. treat an existing exact object as idempotent;
14. reject conflicting existing bytes;
15. print only safe counts, IDs and manifest hash;
16. never print paths, storage keys or rights evidence.

Do not make network calls other than the configured private object-storage
backend.

## 6. Development synthetic pack

Provide an explicit development/test-only command or helper that creates a
small deterministic synthetic pack locally.

It must:

- be created programmatically;
- make zero external calls;
- be visibly labelled as a development placeholder;
- cover all six garment categories sufficiently for engineering tests;
- use abstract/stylised synthetic visuals rather than copied imagery;
- be rejected when `APP_ENV=production`;
- never satisfy the production content-readiness check;
- never be described as the production-quality pack.

Do not commit 30–50 fabricated production images.

## 7. Selector

Implement:

    select_demo_asset(
        design_spec,
        image_prompt,
        manifest,
    )

Selection rules:

1. exact garment compatibility is a hard filter;
2. incompatible garments are never fallback candidates;
3. rank remaining assets using explicit source-controlled weights for:
   - ceremony;
   - silhouette;
   - colours;
   - fabrics;
   - embellishment;
   - coverage;
   - dupatta or saree drape;
   - compatible regional direction;
4. use both canonical source selections and controlled terms present in the
   deterministic image prompt;
5. do not use raw user text directly;
6. do not use a mutable database order;
7. do not use Python's process-randomised `hash()`;
8. ties use a stable SHA-256 over:
   - canonical design input;
   - selector version;
   - manifest hash;
   - asset ID;
9. same input and pack always select the same asset;
10. no compatible asset raises a controlled `DemoAssetUnavailable`;
11. never silently select the first asset;
12. never silently fall back to live generation.

Persist minimal private selection provenance:

- asset ID;
- manifest hash;
- manifest schema version;
- selector version.

Do not persist the source filename or source storage key in a public-facing
field.

## 8. Part A tests

Test:

- valid manifest;
- malformed top level;
- unsupported schema;
- duplicate IDs;
- duplicate filenames;
- malformed hashes;
- unsafe paths;
- remote URLs;
- unknown fields;
- unverified provenance;
- non-WebP files;
- invalid dimensions;
- corrupt images;
- metadata stripping;
- atomic pack installation;
- partial cleanup;
- idempotent re-install;
- conflicting object rejection;
- exact garment hard filtering;
- culturally incompatible fallback rejection;
- deterministic scoring;
- deterministic tie-breaking;
- same input across different process runs;
- manifest changes alter the selector fingerprint;
- missing asset;
- corrupt stored asset;
- production rejection of the synthetic pack;
- no external network use.

Commit Part A as:

    feat(demo): add versioned demo asset manifest and selector

# Part B — Deterministic DesignSpec and refinement engines

## 9. Engine versions

Define independent versions:

    DEMO_SPEC_TEMPLATE_VERSION = "1.0.0"
    DEMO_REFINEMENT_TEMPLATE_VERSION = "1.0.0"

Add deterministic template fingerprints.

Do not change:

    DESIGN_SPEC_SCHEMA_VERSION = 1
    SPEC_TEMPLATE_VERSION = "2.0.0"
    REFINEMENT_TEMPLATE_VERSION = "1.0.0"
    PROMPT_BUILDER_VERSION = "3.0.0"

The existing constants continue to describe live structured prompts and the
shared image-prompt builder.

## 10. Initial demo DesignSpec engine

Create an engine such as:

    build_demo_design_spec(generation_context)

It must consume the already validated `GenerationContext`, not parse the
rendered Anthropic prompt.

It may use:

- canonical questionnaire machine values;
- controlled taxonomy labels;
- curated inspiration provider cues;
- source-controlled narrative templates;
- a stable context fingerprint for selecting among curated phrase variants.

It must not use:

- a live provider;
- a network client;
- raw prompt parsing;
- arbitrary code execution;
- Python runtime randomness;
- current time;
- Design UUID;
- user/session identity;
- storage data;
- provider keys;
- inspiration image bytes or URLs;
- inspiration title or attribution in provider-style narrative fields.

The result must:

- be a complete valid `DesignSpec`;
- preserve `source_selections` exactly;
- retain garment and cultural distinctions;
- honour coverage choices;
- describe fabrics, colours, embellishment and drape meaningfully;
- include the required concept-only caveats;
- contain accurate image alt text;
- pass the existing generated-content safety scan;
- pass the existing inspiration-leakage checks;
- be byte-for-byte deterministic after canonical serialization.

Avoid generic text such as:

    Placeholder fabric
    Placeholder silhouette
    Offline test concept

The brief should be credible enough for a product demonstration.

The UI, not the DesignSpec prose itself, carries the demo disclosure.

## 11. Controlled phrase maps

Use bounded, source-controlled phrase maps keyed by questionnaire machine
values.

Add tests proving all active questionnaire v1 values needed by the demo engine
are handled.

Do not duplicate questionnaire validation rules.

The questionnaire remains authoritative for whether a combination is valid.

Phrase maps must preserve:

- gharara versus sharara;
- saree drape versus dupatta styling;
- lehenga, anarkali and shalwar-kameez distinctions;
- optional rather than assumed regional identity;
- modest coverage;
- ceremony context without claiming universal religious rules.

## 12. Free-text behaviour

Initial questionnaire free text must not be copied blindly into the
DesignSpec.

For Phase 15:

- structured choices are authoritative;
- an optional small allowlisted keyword extractor may recognise safe,
  category-relevant terms;
- unrecognised prose may influence a stable variant hash but must not be
  reproduced;
- prompt-like instructions remain inert;
- no system/developer instruction text may enter output;
- the UI must not promise full natural-language interpretation in demo mode.

## 13. Deterministic refinement engine

Create a focused engine such as:

    build_demo_refined_spec(
        source_spec,
        refinement_request,
    )

It must return a complete updated DesignSpec and pass through the existing
Phase 14 validation:

- source selections remain exact;
- at least one field changes;
- every changed path is allowlisted for the category;
- immutable roots do not change;
- generated-content safety passes;
- process language does not leak.

Use the existing:

- `REFINEMENT_ALLOWED_PATHS`;
- exact recursive diff;
- output validation;
- atomic version persistence.

Do not implement a parallel refinement service.

## 14. Refinement note interpretation

Use small controlled keyword maps for category-relevant concepts such as:

- named colour families;
- softer/deeper/lighter tone;
- fabric choices;
- embellishment density;
- sleeve length;
- coverage;
- neckline shapes;
- dupatta or saree drape;
- silhouette detail;
- styling restraint.

Requirements:

- only the selected category may be affected;
- the note has already passed Phase 14 safety validation;
- unknown text selects a safe deterministic variant;
- no raw note is copied into output;
- no named designer/brand is introduced;
- no religious or regional identity is inferred;
- no coverage is reduced from an ambiguous note;
- ensure a genuine allowed diff;
- if the first deterministic candidate equals the original, select the next
  stable compatible variant;
- no provider retry is necessary for a deterministic valid local result;
- the existing retry/diff infrastructure must still remain compatible with
  live providers.

## 15. Provider-protocol adapters

Implement local classes conforming to the authoritative structured-generation
protocol.

Suggested identities:

    provider = "demo"
    model = "demo-spec-1.0.0"

and:

    provider = "demo"
    model = "demo-refinement-1.0.0"

Usage metadata:

- input tokens: null;
- output tokens: null;
- stop reason: deterministic/local safe value;
- refused: false.

Do not disguise a demo result as Anthropic output.

Do not pass the demo engine through the obsolete Phase 3A synchronous
`generate_design_spec()` interface.

Remove the obsolete synchronous Phase 3A demo provider/getter APIs when no
remaining code requires them.

Keep test-only fixture providers clearly separate and labelled `fixture`.

## 16. Provider selection

All ordinary design and refinement precondition/safety checks must run before
adapter selection.

After those checks:

- demo attempt -> local deterministic adapter;
- live attempt -> existing fail-closed `ai_gateway` live provider getter;
- injected test provider -> explicit test/management-command injection.

Do not let views or serializers select providers.

Do not let a current environment toggle silently change a persisted attempt's
execution mode.

## 17. Part B tests

Test:

- all garment types;
- all ceremonies;
- representative silhouettes;
- colours;
- fabrics;
- embellishment levels;
- coverage preferences;
- dupatta styles;
- saree drapes;
- regional direction absent and present;
- zero, one and three inspiration cues;
- same context gives byte-identical DesignSpec;
- different Design UUID gives the same output;
- different user/session gives the same output;
- system clock changes do not alter output;
- raw user prose is not copied;
- unsafe text does not leak;
- inspiration title/attribution does not leak;
- all eight refinement categories;
- recognised refinement keywords;
- unrecognised notes;
- empty notes;
- no-op avoidance;
- exact Phase 14 diff enforcement;
- original version remains unchanged;
- no live provider imports or calls;
- fixture providers remain test-only.

Commit Part B as:

    feat(demo): add deterministic design and refinement engines

# Part C — Shared asynchronous demo pipeline

## 18. Public generation modes

Refactor the generation availability policy so the public system has three
safe outcomes:

    demo
    live
    unavailable

Required precedence:

1. when `DEMO_MODE=true`, evaluate only demo readiness;
2. when `DEMO_MODE=false`, evaluate live readiness;
3. never evaluate live readiness as fallback from failed demo readiness.

Demo readiness requires:

- demo pipeline implemented;
- valid configured manifest;
- compatible installed asset pack;
- required storage available.

Live readiness retains:

- `DEMO_MODE=false`;
- `ALLOW_PAID_AI_CALLS=true`;
- `LIVE_GENERATION_ENABLED=true`;
- complete credentials;
- implemented capabilities.

`LIVE_GENERATION_ENABLED` gates only live generation.

A present provider key never enables anything by itself.

## 19. Controlled demo-unavailable failure

Add a stable error code such as:

    demo_assets_unavailable

Use it when:

- manifest missing;
- manifest invalid;
- required garment coverage absent;
- selected asset absent;
- selected asset hash mismatch;
- private demo storage unavailable.

Do not expose which internal object or path failed.

Do not fall back to paid providers.

## 20. Persisted demo identity

Persist whether work was created in demo mode so historical jobs and results
remain labelled correctly after settings change.

Use a minimal additive representation, such as:

    GenerationAttempt.is_demo
    DesignVersion.is_demo

or an equally narrow reviewed `generation_mode` enum.

Requirements:

- new initial attempts freeze the current mode;
- refinements inherit the source version's mode;
- a demo source cannot be refined through the live path;
- a live source cannot be refined through the demo path;
- worker redelivery follows the attempt's persisted mode;
- legacy rows migrate safely;
- public APIs expose only a safe demo/live indicator;
- provider/model/manifest/storage details remain private.

## 21. Minimal private selection provenance

Add minimal attempt-level demo provenance, either as strict dedicated fields or
a strict versioned private snapshot:

- manifest schema version;
- manifest SHA-256;
- selector version;
- selected asset ID.

Rules:

- all absent for live attempts;
- all-or-none when selected;
- immutable after selection;
- successful demo attempt requires complete provenance;
- never expose source filename or storage key;
- never expose the manifest through public APIs;
- malformed persisted provenance fails closed on resume.

## 22. Demo DesignVersion provenance

Persist truthful values:

Initial demo version:

    design_spec_provider = "demo"
    design_spec_model = "demo-spec-1.0.0"
    design_spec_template_version = "demo-1.0.0"

Refined demo version:

    design_spec_provider = "demo"
    design_spec_model = "demo-refinement-1.0.0"
    design_spec_template_version = "demo-refinement-1.0.0"

Keep:

    design_spec_schema_version = 1
    prompt_builder_version = "3.0.0"
    image_processor_version = "1.0.0"

Do not label demo output as Claude, Anthropic, Replicate or FLUX.

## 23. Asynchronous demo image adapter

Implement a local adapter conforming to the current asynchronous
`ImageProvider` protocol.

It must support:

- create;
- poll;
- cancel;
- safe prediction IDs;
- starting;
- processing;
- succeeded.

It must not:

- contact a remote host;
- use an HTTP URL for actual fetching;
- construct a Replicate client;
- expose a source storage key;
- skip normal pipeline states.

Use a private internal reference scheme such as:

    demo-asset://<opaque-selection-reference>

Only the dedicated demo downloader may resolve that scheme.

The live downloader must reject it.

The demo downloader must reject ordinary HTTP/HTTPS provider URLs.

## 24. Demo image downloader

The local downloader:

1. resolves the persisted selected asset;
2. computes the deterministic private source key internally;
3. reads from private storage;
4. enforces size limits;
5. verifies SHA-256;
6. verifies decodability and dimensions;
7. returns bytes to the normal pipeline.

The normal pipeline then performs:

- raw staging;
- staging verification;
- canonical permanent ingest;
- original and thumbnail creation;
- immutable final provenance;
- signed-image delivery.

Do not copy the demo source key directly into the DesignVersion.

## 25. Deterministic demo seed

Demo image selection does not rely on a provider seed, but the existing
GenerationAttempt contract still requires reproducible image parameters.

Create a deterministic non-negative demo seed from:

- canonical persisted image prompt;
- manifest hash;
- selector version.

Do not include:

- Design UUID;
- attempt UUID;
- user identity;
- current time;
- process randomness.

The same prompt and manifest produce the same demo seed.

Live seed generation remains cryptographically random and unchanged.

Refinement seed reuse semantics remain truthful:

- a demo refinement may reuse the source demo seed when available;
- `seed_reused` remains private;
- the selected asset is still determined by the refined specification and
  selector;
- never claim visual continuity.

## 26. Pipeline reuse

Extend the existing state machine rather than duplicating it.

The demo path must use the current:

- enqueue services;
- idempotency;
- one-in-progress-attempt constraint;
- advisory lock;
- `text_submission_in_flight`;
- `image_submission_in_flight`;
- prompt persistence;
- prediction persistence;
- staging persistence;
- ingest;
- terminal success/failure;
- task retries;
- duplicate-delivery protection;
- refinement version limit.

Only provider/adaptor factories and download resolution differ.

## 27. Mode frozen at enqueue

At enqueue:

1. resolve public generation mode;
2. validate demo readiness or live readiness;
3. persist the chosen mode;
4. create the attempt;
5. submit Celery on commit.

At worker execution:

- use persisted mode;
- demo mode cannot turn into live;
- live mode cannot turn into demo;
- a live attempt still rechecks paid gates before every new paid submission;
- a demo attempt never checks or uses provider credentials;
- settings changes may make an unsafe live continuation fail closed;
- settings changes must never make a demo attempt spend money.

## 28. Progress delay

Add a strictly bounded setting such as:

    DEMO_STAGE_DELAY_MS

Requirements:

- non-negative integer;
- maximum 5000;
- default zero in tests;
- modest documented development default;
- applies only to demo attempts;
- injected sleeper in tests;
- no fake percentages;
- no busy waiting;
- no delay in live mode;
- no delay while holding database locks or transactions.

The delay may keep the genuine persisted:

    queued
    running_text
    running_image
    succeeded

states visible long enough for a demonstration.

## 29. API contract

Extend the public configuration payload additively with:

    generation_mode: "demo" | "live" | "unavailable"

Preserve the existing availability boolean.

Extend job and result payloads with a safe historical indicator such as:

    is_demo: true

or:

    generation_mode: "demo"

Do not expose:

- asset ID;
- manifest hash;
- selector version;
- source key;
- source filename;
- deterministic seed;
- provider model;
- internal prediction reference.

Update OpenAPI and generated frontend types.

## 30. Full HTTP journey

With demo mode ready, the existing endpoints must work unchanged:

- design creation and update;
- generation submission;
- job polling;
- result retrieval;
- signed image retrieval;
- refinement submission;
- version 2 polling;
- result comparison.

No `/demo/...` generation endpoints.

## 31. Resume and idempotency

Test and preserve:

- repeated HTTP idempotency key returns the same attempt;
- duplicate Celery delivery does not duplicate text work;
- linked version skips local spec regeneration;
- persisted prompt skips prompt rebuild;
- persisted demo selection skips reselection;
- persisted prediction reference skips create;
- staged image skips source reread where safe;
- permanent image skips reprocessing;
- no version 3;
- no duplicate final objects;
- no mode switch on resume.

## 32. Part C tests

At minimum:

- demo availability with a valid pack;
- demo unavailable with no pack;
- demo unavailable with corrupt pack;
- demo precedence over paid flags and keys;
- no live provider factory called;
- no Anthropic client constructed;
- no Replicate client constructed;
- socket-blocked full journey;
- initial generation through API and Celery;
- refinement through API and Celery;
- exact progress state order;
- same API route shapes;
- result and signed-image delivery;
- private source key never exposed;
- source asset copied through staging and ingest;
- final image uses DesignVersion key;
- deterministic seed;
- deterministic asset selection;
- idempotency replay;
- duplicate task delivery;
- worker restart/resume;
- settings changed after enqueue;
- demo source cannot become live refinement;
- live source cannot become demo refinement;
- live generation regression suite unchanged;
- test fixtures remain distinct from public demo.

Commit Part C as:

    feat(demo): route the asynchronous pipeline through local adapters

# Part D — Honest frontend demo experience

## 33. API integration

Use the existing API layer and generated OpenAPI types.

Do not create:

- a demo API client;
- mocked fetch responses;
- browser-side asset selection;
- local fixture JSON;
- demo route aliases.

Continue using:

- same-origin relative `/api/...`;
- in-memory CSRF;
- five-second browser timeout;
- no-store;
- retry-once CSRF handling;
- TanStack Query lifecycle;
- memory-only signed URLs.

## 34. Global demo indication

When public configuration says `generation_mode == "demo"`, display a clear
persistent but unobtrusive banner:

> Demo mode — no paid AI services are being called. Sitara creates a
> deterministic design brief locally and selects a pre-generated concept image
> that best matches your choices.

It must:

- be accessible;
- not rely only on colour;
- not be dismissible in a way that hides required disclosure;
- not imply a provider generated the displayed image;
- not expose technical configuration or keys.

## 35. Questionnaire and review disclosure

Near generation submission, explain:

- structured selections determine a deterministic design brief;
- approved inspiration descriptions may influence that brief;
- free-text interpretation is limited in demo mode;
- the visual is selected from a curated pack;
- it may not show every detail in the brief.

Do not weaken the existing Phase 13 inspiration disclosure.

## 36. Progress copy

Use the existing progress route and components.

For a demo job, keep the normal stage meaning while using honest wording:

Queued:

    Preparing your demo concept

Running text:

    Building your deterministic design brief

Running image:

    Selecting and processing your demo visual

Do not say:

- contacting Claude;
- generating with FLUX;
- Replicate is rendering;
- newly generating your image.

No fake percentage.

## 37. Result disclosure

On a demo result show:

> This visual was selected from Sitara's curated demo pack to resemble your
> choices. It was not newly generated for this design and may not reflect every
> detail in the design brief.

Keep:

- complete DesignSpec brief;
- concept-only disclaimer;
- constructibility disclaimer;
- private image delivery;
- inspiration acknowledgements;
- copy/download functions.

The downloaded brief should include a concise demo disclosure but no internal
provenance.

## 38. Refinement disclosure

For demo refinement explain:

- the deterministic brief will be updated within the selected category;
- another curated image may be selected;
- the image is not edited;
- the original image is not sent anywhere;
- visual differences may be substantial.

Preserve the existing Phase 14 drift acknowledgement.

Do not claim seed continuity or image editing.

## 39. Version comparison

Each version must use its persisted demo/live indicator.

A demo version remains labelled demo even if the current environment later
runs in live mode.

Mixed demo/live parent-child versions must be impossible server-side.

The comparison view must not infer mode only from current public settings.

## 40. Missing asset-pack UX

When demo mode is configured but the asset pack is unavailable, show a
controlled unavailable state.

Do not:

- offer a paid fallback;
- silently switch mode;
- display a generic crash;
- expose storage or manifest details.

Suggested user-facing message:

> Demo generation is temporarily unavailable because its visual library is not
> ready.

## 41. Accessibility

Test:

- demo banner is announced appropriately but not repeatedly;
- status updates use polite live regions;
- unavailable errors use `role="alert"`;
- disclosure is associated with the submission action;
- keyboard-only generation and refinement remain functional;
- no meaning relies only on colour;
- comparison order remains logical;
- no focus regressions.

Do not perform the full Phase 17 visual redesign.

## 42. Frontend tests

Test:

- demo banner shown in demo mode;
- banner absent in live mode;
- unavailable mode;
- initial submission uses unchanged endpoint;
- demo progress wording;
- no provider claims;
- result disclosure;
- refinement disclosure;
- version comparison uses persisted mode;
- raw internal provenance absent;
- no browser storage used;
- signed URL behaviour unchanged;
- no demo-specific mock fetch layer;
- no public gallery or carousel introduced.

Commit Part D as:

    feat(frontend): add honest deterministic demo experience

# Part E — Documentation and cleanup

## 43. ADR 0016

Create:

    docs/decisions/0016-deterministic-demo-mode.md

Record:

- same public APIs;
- same Celery state machine;
- same persistence;
- same deterministic prompt builder;
- same staging and canonical ingest;
- local deterministic structured adapters;
- local asynchronous image adapter;
- versioned manifest;
- private demo-source storage;
- deterministic selector;
- exact garment hard filtering;
- stable tie-breaking;
- persisted demo identity;
- persisted minimal selection provenance;
- demo precedence over all paid flags;
- `LIVE_GENERATION_ENABLED` applies only to live mode;
- no live fallback;
- honest frontend labelling;
- production demo pack as a separate human content prerequisite;
- development synthetic pack is not production content;
- no public showcase gallery;
- obsolete Phase 3A synchronous demo path removed;
- test fixtures remain separate from public demo functionality.

## 44. Update documentation

Update:

- `README.md`;
- `.env.example`;
- `docs/PROPOSAL.md`;
- `docs/phases/PHASES.md`;
- `docs/phases/phases-15.md`;
- ADR 0002;
- ADR 0009;
- ADR 0010;
- ADR 0011;
- ADR 0012;
- ADR 0013;
- ADR 0014 where relevant;
- ADR 0015;
- `CLAUDE.md`.

Document:

- asset installation;
- development synthetic pack;
- demo readiness check;
- progress delay;
- mode precedence;
- local run commands;
- zero-provider guarantees;
- production pack prerequisite.

Do not claim the production-quality pack exists unless its real files and
verified provenance were provided and checked.

## 45. Remove obsolete scaffolding

After verifying no users remain, remove:

- obsolete synchronous Phase 3A demo provider protocols;
- obsolete synchronous demo getters;
- placeholder demo response shapes not used by the async pipeline;
- stale comments saying the authoritative demo path is not implemented.

Do not remove:

- test-only fixture providers;
- offline fixture commands;
- injected provider support used by tests;
- any live fail-closed gateway.

Commit Part E as:

    docs(phase-15): record deterministic demo-mode decisions

# Validation

## 46. Build and dependencies

No new runtime dependency should be necessary.

Run:

    docker compose config
    docker compose build api web
    docker compose up -d
    docker compose exec api python -m pip check
    docker compose exec web npm ci

Confirm:

- no unnecessary dependency;
- no opportunistic upgrade;
- no unexplained lockfile drift.

## 47. Backend

Run:

    docker compose exec api python manage.py check
    docker compose exec api python manage.py makemigrations --check --dry-run
    docker compose exec api python manage.py migrate
    docker compose exec api pytest
    docker compose exec api ruff check .
    docker compose exec api ruff format --check .

A small migration for persisted demo identity/provenance may be expected.

## 48. OpenAPI and frontend

Run:

    docker compose exec api python manage.py spectacular \
      --format openapi-json \
      --file openapi/schema.json \
      --validate \
      --fail-on-warn

Then:

    docker compose exec web npm run generate:api
    docker compose exec web npm run lint
    docker compose exec web npm run typecheck
    docker compose exec web npm test -- --run
    docker compose exec web npm run build

Commit deliberate generated outputs and prove no remaining drift.

## 49. Contract/version guards

Confirm unchanged:

    DESIGN_SPEC_SCHEMA_VERSION = 1
    SPEC_TEMPLATE_VERSION = "2.0.0"
    REFINEMENT_TEMPLATE_VERSION = "1.0.0"
    PROMPT_BUILDER_VERSION = "3.0.0"
    INSPIRATION_CONTEXT_SCHEMA_VERSION = 1
    REFINEMENT_REQUEST_SCHEMA_VERSION = 1
    DESIGN_IMAGE_PROCESSOR_VERSION = "1.0.0"

Confirm new:

    DEMO_MANIFEST_SCHEMA_VERSION = 1
    DEMO_SELECTOR_VERSION = "1.0.0"
    DEMO_SPEC_TEMPLATE_VERSION = "1.0.0"
    DEMO_REFINEMENT_TEMPLATE_VERSION = "1.0.0"

Run all existing fingerprint, snapshot and golden-manifest tests.

Initial live prompt snapshots must remain byte-identical.

## 50. Questionnaire lifecycle

Run:

    docker compose exec api pytest \
      sitara/questionnaire/tests/test_fixture_versions.py

Confirm:

- questionnaire v1 fingerprint unchanged;
- questionnaire v2 remains draft;
- active schema rules unchanged.

## 51. Celery

Run:

    docker compose exec api python -c \
      "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"

Confirm:

- worker listens to expected queues;
- demo jobs execute in Celery;
- initial and refinement branches work;
- status transitions persist in order;
- duplicate delivery remains safe;
- no provider client is constructed.

## 52. Zero-network proof

Add a test fixture that blocks external socket creation.

Run the complete initial and refinement journey with:

    DEMO_MODE=true
    ALLOW_PAID_AI_CALLS=true
    LIVE_GENERATION_ENABLED=true

and non-empty test-only provider credential strings.

Prove:

- no Anthropic client;
- no Replicate client;
- no provider HTTP request;
- no DNS lookup;
- no fallback;
- no credential read beyond configuration parsing where avoidable;
- journey still succeeds through local adapters.

## 53. Development fixture journey

Install the development-only synthetic pack.

Run a complete journey through normal HTTP/Celery APIs:

1. create design;
2. save questionnaire;
3. select optional inspiration;
4. generate;
5. poll;
6. retrieve result;
7. retrieve image;
8. refine;
9. poll;
10. retrieve version 2;
11. compare versions.

Run the same canonical initial input twice using different Design UUIDs.

Confirm:

- byte-identical DesignSpec;
- identical image prompt;
- identical demo asset ID;
- identical selector provenance;
- equivalent final image content after canonical processing;
- independent private result objects;
- no shared signed URL persisted.

Print only safe output:

- Design IDs;
- attempt IDs;
- version IDs;
- statuses;
- version numbers;
- dimensions;
- demo/live indicator;
- selector version;
- manifest version.

Do not print:

- questionnaire answers;
- prompts;
- free text;
- source paths;
- storage keys;
- hashes;
- signed URLs;
- provider keys;
- seeds.

## 54. Live-path regression

With:

    DEMO_MODE=false
    ALLOW_PAID_AI_CALLS=false
    LIVE_GENERATION_ENABLED=false

confirm live generation remains unavailable and no demo fallback occurs.

Run mocked live-provider suites to prove the existing live path still behaves
as before.

Make no real calls.

## 55. Phase 2 integrity

Run the existing Phase 2 evidence-integrity tests.

Confirm:

    git diff -- experiments/model-eval/outputs/

is empty.

Do not modify model-selection evidence.

# Offline manual checkpoint

Keep all provider access blocked.

1. Install the development synthetic demo pack.
2. Start the full stack with `DEMO_MODE=true`.
3. Remove all real provider credentials from the environment.
4. Complete the questionnaire in the browser.
5. Generate a concept.
6. Observe queued, text and image stages.
7. Confirm the progress wording is demo-specific.
8. Open the result.
9. Confirm:
   - complete meaningful DesignSpec;
   - demo badge;
   - curated-pack disclosure;
   - no claim that FLUX generated it;
   - image delivered through normal signed URL;
   - image stored under the version's normal private key;
   - source demo key is never exposed.
10. Repeat the same canonical answers in a second design.
11. Confirm deterministic spec and asset selection.
12. Change one meaningful questionnaire choice.
13. Confirm the deterministic output changes where expected.
14. Run one refinement.
15. Confirm:
   - existing Phase 14 exact-diff checks are used;
   - version 2 is created;
   - demo refinement disclosure is shown;
   - another curated asset may be selected;
   - no image editing is claimed.
16. Attempt a second refinement and confirm rejection.
17. Restart the worker during a demo attempt and confirm safe resume.
18. Set paid flags and non-empty fake credentials while keeping demo mode true.
19. Repeat one journey and confirm no live client is constructed.
20. Inspect logs and confirm no:
   - prompts;
   - answers;
   - notes;
   - keys;
   - storage paths;
   - source filenames;
   - signed URLs.
21. Confirm zero external network calls.

# Production demo asset-pack checkpoint

This is a separate human content checkpoint.

Do not complete it automatically.

Target:

- approximately 30–50 rights-clean, curated concept images;
- all supported garments represented;
- all ceremonies represented;
- modest/full-coverage examples;
- minimal and heavy embellishment;
- varied fabrics, colours, silhouettes and drapes;
- accurate human-authored alt text;
- verified project ownership or documented acceptable rights;
- no copied logos, watermarks or designer signatures;
- private-storage installation verified;
- complete manifest validation.

The pack may be generated separately only after explicit budget authorisation
and a provider-terms review.

Do not:

- download random web images;
- scrape bridalwear sites;
- use catalogue assets without compatible rights;
- fabricate ownership;
- claim readiness when only the synthetic development pack exists.

If no production pack is supplied during Phase 15, record:

    production demo asset pack: pending

Phase 18 public deployment must remain blocked on that content prerequisite.

# Integrity requirements

Before approval confirm:

- demo uses normal generate/refine endpoints;
- demo uses normal Celery tasks;
- demo uses normal DesignVersion lifecycle;
- demo uses normal prompt builder;
- demo uses normal staging;
- demo uses normal canonical ingest;
- demo uses normal result API;
- demo uses normal signed image delivery;
- no frontend mock service;
- no fake successful result insertion;
- deterministic initial DesignSpec;
- deterministic refinement;
- deterministic asset selection;
- exact garment compatibility;
- culturally incompatible fallbacks rejected;
- zero Anthropic calls;
- zero Replicate calls;
- zero provider clients;
- zero external provider network calls;
- demo mode overrides every paid flag;
- no fallback between modes;
- persisted historical demo identity;
- source storage keys private;
- production synthetic-pack use refused;
- production asset-pack readiness reported honestly;
- no public gallery;
- no questionnaire v2 activation;
- no Phase 2 evidence changes;
- no live model changes;
- no image-to-image;
- no reference-image conditioning;
- no Docker volume deletion;
- hosted CI green.

# Pull request

Use:

    phase/phase-15-deterministic-demo

Open a draft pull request into `main`.

Suggested title:

    phase-15: deterministic zero-cost demo pipeline

Never merge it.

# Final response

Return only:

1. phase branch;
2. Part A full SHA;
3. Part B full SHA;
4. Part C full SHA;
5. Part D full SHA;
6. Part E full SHA;
7. manifest schema and version;
8. selector version and scoring rules;
9. asset import and verification behaviour;
10. development synthetic-pack behaviour;
11. production asset-pack readiness;
12. deterministic initial DesignSpec engine;
13. deterministic refinement engine;
14. demo template versions;
15. provider-adapter selection;
16. public generation-mode semantics;
17. demo precedence proof;
18. persisted demo identity and provenance;
19. asynchronous demo image lifecycle;
20. local image download and normal ingest path;
21. deterministic seed behaviour;
22. pipeline resume and idempotency results;
23. public API additions;
24. frontend demo disclosure;
25. progress wording;
26. result and refinement disclosure;
27. migration results;
28. backend validation;
29. frontend validation;
30. OpenAPI/generated-type drift;
31. Celery validation;
32. complete fixture-journey result;
33. same-input determinism proof;
34. zero-client and zero-network proof;
35. live-path regression result;
36. questionnaire lifecycle result;
37. Phase 2 integrity result;
38. offline checkpoint result;
39. production content checkpoint status;
40. council findings and resolutions;
41. independent Codex decision;
42. hosted CI status;
43. draft pull-request URL;
44. unresolved issues.