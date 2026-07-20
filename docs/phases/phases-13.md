# Sitara Phase 13 — Rights-safe inspiration metadata influence

Expected starting commit:

28ad30e8f627f473908b133aff3e984bc4a0b2cc

Before changing anything, confirm that the current `main` is this commit or a
clean descendant containing the merged Phase 12 implementation. Report any
unexpected application-code commits before proceeding.

## Phase decision

This phase implements **metadata-only inspiration influence**.

Selected inspiration image bytes are NOT sent to:

- Anthropic;
- Replicate;
- any other provider.

The provider-facing inspiration cues are built only from the catalogue fields
that already exist and are frozen once an asset is approved:

- `garment_type`;
- `alt_text`, exposed to generation as a curated visual description;
- `cultural_context`.

The following may be persisted for private audit and user acknowledgement but
must never be sent to the provider:

- inspiration asset UUID;
- asset title;
- public attribution text.

Reference-image conditioning remains disabled. The existing
`ReferenceImagesNotEnabled` boundary must remain fail-closed.

This is an MVP implementation limitation, not evidence that metadata influence
is superior to direct reference conditioning. Phase 2 recorded no conclusion
because the inspiration comparison was not run. Do not make a quality claim
until a separately authorised, human-reviewed evaluation is completed.

## Read first

Read the current files rather than relying on older roadmap assumptions:

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
- `docs/decisions/0001-image-model.md`
- `docs/decisions/0004-private-design-ownership.md`
- `docs/decisions/0006-rights-controlled-inspiration-catalogue.md`
- `docs/decisions/0009-structured-design-spec-generation.md`
- `docs/decisions/0010-deterministic-image-prompt-builder.md`
- `docs/decisions/0011-asynchronous-generation-pipeline.md`
- `docs/decisions/0012-private-design-image-storage.md`
- `docs/decisions/0013-generation-progress-and-results.md`
- `apps/api/sitara/catalogue/models.py`
- `apps/api/sitara/catalogue/services.py`
- `apps/api/sitara/catalogue/serializers.py`
- `apps/api/sitara/designs/models.py`
- `apps/api/sitara/designs/services.py`
- `apps/api/sitara/designs/result.py`
- `apps/api/sitara/designs/openapi.py`
- `apps/api/sitara/designs/views.py`
- `apps/api/sitara/generation/context.py`
- `apps/api/sitara/generation/design_spec.py`
- `apps/api/sitara/generation/input_safety.py`
- `apps/api/sitara/generation/prompting.py`
- `apps/api/sitara/generation/services.py`
- `apps/api/sitara/generation/pipeline.py`
- `apps/api/sitara/ai_gateway/image_generation.py`
- `apps/api/sitara/ai_gateway/replicate_provider.py`
- `apps/web/src/features/questionnaire/InspirationPicker.tsx`
- `apps/web/src/features/questionnaire/ReviewSummary.tsx`
- `apps/web/src/features/results/DesignResult.tsx`
- `apps/web/src/features/results/DesignBrief.tsx`
- `apps/web/src/features/results/result-brief.ts`
- `apps/web/src/lib/api.ts`
- `experiments/model-eval/src/model_eval/prompt_formats.py`
- `experiments/model-eval/prompts/briefs.yaml`

Use the current repository layout:

- Django: `apps/api/sitara/...`
- Next.js App Router: `apps/web/src/app/...`
- frontend feature code: `apps/web/src/features/...`
- browser API wrappers: `apps/web/src/lib/api.ts`
- generated OpenAPI types: `apps/web/src/api/schema.d.ts`
- phase documents: `docs/phases/...`
- decision records: `docs/decisions/...`

Do not introduce the old:

- `frontend/`;
- `backend/`;
- `docs/PHASES.md`;
- `docker-compose.yml`;
- local phase-agent or phase-skill paths.

## Commit boundaries

Implement the phase as three focused commits:

1. `feat(inspiration): add versioned inspiration context provenance`
2. `feat(generation): apply curated inspiration metadata to DesignSpec`
3. `feat(frontend): disclose inspiration influence and acknowledgements`

Do not combine them.

Part A must pass its focused tests before Part B. Part B must pass before
Part C.

## Non-goals

Do not implement:

- reference-image conditioning;
- sending catalogue image bytes to Anthropic;
- sending catalogue image bytes or URLs to Replicate;
- a new image model;
- a new Replicate model parameter;
- image-to-image generation;
- visual embeddings;
- image captioning AI;
- user-uploaded inspirations;
- automated cultural classification;
- a generic tags or ontology engine;
- arbitrary catalogue JSON metadata;
- refinement;
- demo fixture matching;
- showcase galleries;
- public or shareable designs;
- rate limits;
- cost ceilings;
- retention or deletion jobs;
- provider-term legal conclusions;
- deployment;
- Phase 2 live experiment execution.

Do not activate questionnaire v2.

Make zero Anthropic and Replicate calls during implementation, testing,
review, CI and offline checkpoints.

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

Run the current validation commands from `.claude/phase-council.json`.

Confirm before editing:

- questionnaire v1 fingerprint is unchanged;
- questionnaire v2 remains draft;
- `DESIGN_SPEC_SCHEMA_VERSION` remains `1`;
- `SPEC_TEMPLATE_VERSION` is currently `1.0.0`;
- `PROMPT_BUILDER_VERSION` remains `3.0.0`;
- `DESIGN_IMAGE_PROCESSOR_VERSION` remains `1.0.0`;
- the provider-free generation fixture succeeds;
- no Phase 2 evidence is modified.

# Part A — Versioned inspiration context provenance

## 1. Add a strict inspiration-context contract

Create a focused module such as:

    apps/api/sitara/generation/inspiration_context.py

Define:

    INSPIRATION_CONTEXT_SCHEMA_VERSION = 1

Use strict Pydantic v2 models with:

    ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

The persisted snapshot shape should be equivalent to:

    {
      "schema_version": 1,
      "items": [
        {
          "asset_id": "uuid",
          "position": 1,
          "provider_cues": {
            "garment_type": "gharara-or-null",
            "visual_description": "staff-authored alt text",
            "cultural_context": "staff-authored context-or-null"
          },
          "acknowledgement": {
            "title": "public catalogue title",
            "attribution": "public attribution text-or-empty"
          }
        }
      ]
    }

Requirements:

- maximum three items;
- positions begin at 1;
- positions are unique and contiguous;
- items remain in the user-selected order;
- asset UUIDs are unique;
- `garment_type` is a valid machine value or null;
- visual description is required and bounded to the catalogue alt-text limit;
- cultural context is nullable and bounded to the catalogue limit;
- title is bounded to the catalogue title limit;
- attribution is bounded to the existing rights attribution limit;
- no unknown fields;
- no arbitrary nested dictionaries;
- no image URL;
- no storage key;
- no image hash;
- no image dimensions;
- no rights UUID;
- no rights basis;
- no source or licence URL;
- no evidence reference;
- no verifier identity;
- no uploaded/approved staff identity;
- no provider data.

An empty item list is valid and represents a generation with no inspirations.

## 2. Canonical normalisation and hash

Before validation and persistence:

- apply Unicode NFKC;
- normalise CRLF/CR to LF;
- collapse internal whitespace;
- strip outer whitespace;
- preserve meaningful punctuation;
- preserve selection ordering.

Create one deterministic canonical JSON representation using:

- UTF-8;
- sorted object keys;
- fixed compact separators;
- no timestamps;
- no machine-dependent values.

Calculate SHA-256 over that canonical representation.

Provide pure helpers such as:

    build_inspiration_context_snapshot(design) -> InspirationContextSnapshot
    inspiration_context_sha256(snapshot) -> str
    provider_inspiration_cues(snapshot) -> list[dict]
    inspiration_acknowledgements(snapshot) -> list[dict]

`provider_inspiration_cues` must return only:

- position;
- garment type;
- visual description;
- cultural context.

It must omit:

- asset UUID;
- title;
- attribution.

## 3. Eligibility and safety

Every selected inspiration must still come from:

    InspirationAsset.objects.publicly_eligible()

This retains the existing requirement that the asset is:

- approved;
- rights-verified;
- unexpired;
- allowed for public display;
- allowed as AI input;
- allowed for derivative generation;
- allowed for commercial use.

Before any provider selection:

- scan `alt_text`;
- scan `cultural_context` when non-empty.

Use the existing generated-content safety semantics or a narrow wrapper around
them.

Reject provider-facing metadata containing:

- designer or brand names;
- imitation phrasing;
- URLs;
- prompt/system leakage;
- raw HTML or Markdown;
- disallowed control characters.

The error must be a generic pre-spend domain error such as:

    inspiration_metadata_unavailable

Never expose or log the rejected metadata.

Do not apply the provider safety scan to public attribution text merely because
it may contain a rights-holder name or attribution wording. Attribution is not
sent to the provider and React will render it as escaped text.

## 4. Approval-time defence

Extend `approve_inspiration_asset` so a newly approved asset must have
provider-facing metadata that passes the same safe validation:

- required alt text;
- optional cultural context;
- optional garment type.

Approval failure must use the existing safe `AssetApprovalError` boundary.

This is defence in depth. Existing approved assets are rechecked when selected
for generation, so a legacy unsafe asset still causes a zero-provider-call
failure.

Do not mutate or backfill approved asset text automatically.

Do not add new catalogue metadata fields in this phase. The existing frozen
fields are the Phase 13 metadata contract.

## 5. Extend DesignVersion provenance

Add:

    inspiration_context
    inspiration_context_schema_version
    inspiration_context_sha256

Suggested fields:

- `inspiration_context`: nullable JSONField;
- `inspiration_context_schema_version`: nullable positive small integer;
- `inspiration_context_sha256`: `CharField(max_length=64, blank=True)`.

Database constraints:

1. All three fields are absent, or all three are present.
2. When present:
   - schema version equals `1`;
   - hash is exactly 64 lowercase hexadecimal characters.
3. Inspiration context requires a DesignSpec.
4. Existing pre-Phase-13 DesignVersions remain valid with all three fields
   absent.
5. A Phase 13-generated DesignVersion records a snapshot even when no
   inspiration was selected; that snapshot contains an empty item list.

Do not place signed URLs, image bytes or internal rights information in the
snapshot.

Make all three fields read-only in Django admin.

## 6. Atomic snapshot persistence

Persist the exact snapshot and its hash in the same final transaction that
creates and populates the DesignVersion.

The same transaction must contain:

- final design-input freshness validation;
- final inspiration eligibility validation;
- DesignVersion creation;
- DesignSpec persistence;
- inspiration-context persistence;
- GenerationAttempt linkage;
- clearing the text-submission marker.

Do not hold a database transaction or row lock while waiting for Anthropic.

## 7. Final rights and metadata locking

At finalisation, after the provider response but before persistence:

1. enter the existing short atomic finalisation block;
2. lock the Design row;
3. lock the selected `DesignInspiration` rows in deterministic position order;
4. lock the referenced `InspirationAsset` rows in deterministic UUID order;
5. lock the associated `UsageRights` rows in deterministic UUID order;
6. re-run eligibility;
7. rebuild the canonical snapshot;
8. compare its hash and exact contents with the pre-provider snapshot;
9. persist only when they match.

This prevents:

- a selection change;
- asset retirement;
- rights revocation;
- rights expiry;
- metadata mutation;
- attribution mutation

from slipping between the final recheck and DesignVersion persistence.

Acquire locks in one documented order to avoid deadlocks.

Do not hold these locks during the provider request.

Any difference raises `DesignChangedDuringGeneration`, makes no new
DesignVersion and never retries the provider.

## 8. Migration and model tests

Test:

- legacy DesignVersions remain valid;
- null provenance is valid;
- complete empty snapshot provenance is valid;
- complete selected snapshot provenance is valid;
- every partial field combination fails;
- invalid schema version fails;
- malformed hash fails;
- context without DesignSpec fails;
- admin fields are read-only;
- migration applies without provider/storage work.

## 9. Snapshot tests

Test:

- zero, one, two and three selected inspirations;
- exact selection order;
- deterministic repeated JSON and hash;
- changing order changes the hash;
- changing any provider cue changes the hash;
- changing title or attribution changes the audit hash;
- provider cues omit UUID/title/attribution;
- acknowledgements omit provider cues and UUIDs;
- snapshot contains no storage or rights internals;
- unsafe visual description is rejected;
- unsafe cultural context is rejected;
- safe ordinary cultural prose is accepted;
- no image file is read.

Commit Part A as:

    feat(inspiration): add versioned inspiration context provenance

# Part B — Apply metadata to structured DesignSpec generation

## 10. Extend GenerationContext

Extend the current `GenerationContext` with the validated inspiration snapshot
and provider-facing cues.

A suitable shape is:

    GenerationContext(
        source_selections=...,
        trusted_answers=...,
        untrusted_texts=...,
        inspiration_context=...,
        inspiration_cues=...,
    )

Build the snapshot only after:

- questionnaire completeness validation;
- inspiration eligibility validation;
- metadata safety validation.

All of this must happen before:

- selecting a provider;
- constructing a provider client;
- setting a paid-submission marker;
- making a provider call.

## 11. Add cues to the trusted JSON message

Extend `build_user_message` so its trusted JSON contains:

    "curated_inspiration_cues": [...]

The provider-visible cue item contains exactly:

    {
      "position": 1,
      "garment_type": "string-or-null",
      "visual_description": "string",
      "cultural_context": "string-or-null"
    }

Do not include:

- asset UUID;
- title;
- attribution;
- image URL;
- image bytes;
- base64;
- storage key;
- rights details;
- image hash;
- thumbnail data.

The array must be absent or empty when no inspirations were selected.

Do not place inspiration cues inside the untrusted user-text delimiters. They
are staff-curated, validated data, but the system prompt must still say that
they are descriptive data rather than executable instructions.

## 12. Update the trusted system prompt

Update the source-controlled system prompt to state:

- questionnaire selections are always authoritative;
- inspiration cues are optional, secondary visual influences;
- use only cues compatible with the selected garment, ceremony, colours,
  fabrics, embellishment level, coverage and drape;
- ignore any cue that conflicts with a canonical selection;
- never change the selected garment type because an inspiration uses another
  garment;
- never weaken sleeves, neckline, back, midriff or head-covering preferences;
- never increase embellishment when the questionnaire selected none or minimal;
- never invent a regional or religious claim from an image description;
- use abstract design vocabulary rather than copying one garment;
- never copy or reproduce:
  - a person;
  - face or identity;
  - body;
  - pose;
  - background;
  - exact composition;
  - logo;
  - text;
  - watermark;
  - signature motif;
  - distinctive branded arrangement;
- never mention inspiration asset titles, IDs or attribution in the DesignSpec;
- never claim the output reproduces an inspiration image;
- selected inspiration images themselves are not available to the model.

Keep all existing:

- cultural-distinction rules;
- garment-integrity rules;
- coverage rules;
- designer/brand restrictions;
- construction caveats;
- untrusted free-text boundary.

## 13. Template versioning

The system prompt and trusted context layout are changing.

Deliberately bump:

    SPEC_TEMPLATE_VERSION = "2.0.0"

Update the deterministic prompt-template fingerprint and its recorded hash.

Tests must prove:

- the old fingerprint no longer matches;
- the new fingerprint matches exactly;
- a future prompt/context change without a version/hash update fails.

Do not change:

    DESIGN_SPEC_SCHEMA_VERSION = 1

No DesignSpec field is being added or removed.

Do not regenerate a new DesignSpec JSON schema version.

## 14. Canonical selections remain authoritative

The existing exact `source_selections` echo validation remains unchanged.

Add post-output semantic checks where deterministic checks are practical:

- the returned `source_selections` must match exactly;
- no selected inspiration identifier/title/attribution may appear in output;
- no designer/brand or imitation language;
- no prompt leakage;
- no reference to copying an image.

Do not build a general natural-language contradiction engine.

The system prompt plus exact canonical selection echo remain the primary
control.

## 15. Input snapshot and stale detection

Extend the generation input fingerprint so it covers:

- questionnaire version ID;
- normalised answers;
- ordered selected inspiration IDs;
- canonical inspiration-context hash.

The final atomic check must compare both:

- exact snapshot content;
- exact hash.

A provider response built from stale metadata must never be persisted.

## 16. Pipeline and resume behaviour

Preserve the Phase 10/11 state machine.

Requirements:

- no inspiration lookup during image-only resume once a DesignVersion already
  exists;
- the DesignVersion’s persisted snapshot is authoritative for audit;
- an existing DesignVersion never triggers another Anthropic call;
- prompt building remains based only on the validated DesignSpec;
- the image prompt does not directly interpolate inspiration metadata;
- Replicate still receives only the persisted image prompt;
- no reference-image URLs are added to `ImageGenerationRequest`;
- accepted prediction IDs, seeds and staging recovery remain unchanged.

The metadata influences the image only indirectly:

    curated metadata
      -> structured DesignSpec
      -> deterministic image prompt
      -> FLUX

Do not append raw inspiration descriptions to the Phase 9 image prompt.

Do not bump:

    PROMPT_BUILDER_VERSION = "3.0.0"

Prompt snapshots must remain byte-identical.

## 17. Keep reference conditioning disabled

Preserve the current fail-closed behaviour:

    ImageGenerationRequest(
        reference_image_urls=(...)
    )

with a non-empty tuple must raise `ReferenceImagesNotEnabled` before any
provider call.

Tests must prove:

- generation always constructs an empty tuple;
- no catalogue image endpoint is requested by the worker;
- no storage key is converted into a provider URL;
- no image bytes are read from catalogue storage;
- Replicate’s create method receives no reference parameter;
- non-empty references remain rejected even when all live gates are open;
- zero provider clients are instantiated in the rejection test.

Do not add a `REFERENCE_IMAGE_ENABLED` flag. There is no implementation to
enable.

## 18. Persist snapshot with the spec

Pass the pre-provider inspiration snapshot through the generation service and
persist it during finalisation.

Tests must assert:

- no-inspiration generation persists a versioned empty snapshot;
- selected-inspiration generation persists the exact snapshot;
- provider request cues equal the provider subset of the snapshot;
- persisted acknowledgement content equals the audit subset;
- snapshot and DesignSpec commit together;
- failure persists neither;
- an invalid first response followed by a valid response reuses the exact same
  inspiration context;
- a metadata or rights change during the provider call persists nothing;
- no provider retry occurs after a stale-context failure.

## 19. Fixture-provider tests

Use injected fakes only.

Capture the exact `StructuredDesignRequest` and prove:

- zero selected assets produces no cues;
- one to three assets preserve order;
- cues reach Anthropic’s user message as trusted structured JSON;
- image bytes do not;
- storage keys do not;
- public image URLs do not;
- rights evidence does not;
- asset IDs do not;
- titles do not;
- attribution does not;
- canonical questionnaire selections are unchanged;
- free-text notes remain in their separate untrusted delimiter block;
- no network socket is opened.

Add an adversarial fixture where an inspiration cue conflicts with:

- garment type;
- embellishment density;
- full-coverage selection.

The provider fake may return a compliant output, but the test must at least
prove the canonical selections and system instructions remain present and
authoritative.

## 20. Result API acknowledgements

Extend the current curated result payload additively with:

    "inspiration_acknowledgements": [
      {
        "position": 1,
        "title": "string",
        "attribution": "string"
      }
    ]

Requirements:

- use the persisted DesignVersion snapshot;
- never query the current catalogue to reconstruct historical attribution;
- preserve original selection order;
- omit asset UUIDs;
- omit provider cues;
- omit garment type;
- omit alt text;
- omit cultural context;
- omit rights records;
- omit storage data;
- omit URLs.

For legacy pre-Phase-13 DesignVersions with no inspiration context, return:

    "inspiration_acknowledgements": []

When inspiration-context provenance is present:

- validate it through the strict Pydantic snapshot model;
- verify its hash;
- corrupt or unsupported context causes controlled
  `503 design_result_unavailable`;
- do not expose corrupt snapshot content in errors or logs.

Do not make inspiration context a readiness requirement for legacy versions.

## 21. Rights revocation after generation

A generated DesignVersion retains the exact historical acknowledgement
snapshot even when the source asset is later:

- retired;
- expired;
- made ineligible;
- no longer displayed in the catalogue.

Future generation must be blocked by current eligibility, but an existing
private result remains reproducible and continues to display the attribution
captured when generation occurred.

Do not automatically delete generated designs in Phase 13.

Do not make legal conclusions about whether a later revocation requires
deletion. Record this as an unresolved policy question for Phase 16/operator
review.

## 22. OpenAPI

Update the result response serializer with
`inspiration_acknowledgements`.

Regenerate and commit:

- `apps/api/openapi/schema.json`;
- `apps/web/src/api/schema.d.ts`.

Do not expose inspiration context through:

- design list;
- design detail;
- jobs;
- image URL endpoint;
- public config;
- catalogue API beyond its existing public fields.

The generated GET runtime client remains GET-only.

## 23. Part B tests

Test at least:

### Context and provider boundary

- no inspiration;
- one inspiration;
- three inspirations;
- ordered cues;
- unsafe metadata blocks before provider selection;
- retired/expired asset blocks before provider selection;
- rights change during call rejects persistence;
- metadata change during call rejects persistence;
- no image reads;
- no URLs;
- no reference input;
- no provider call from validation failures.

### Provenance

- empty snapshot persisted;
- populated snapshot persisted;
- exact deterministic hash;
- context, spec and attempt linkage are atomic;
- context corruption is detected;
- unsupported context version is detected;
- admin fields read-only.

### Result API

- acknowledgement order;
- required attribution text;
- empty attribution;
- legacy empty list;
- retired source still renders stored acknowledgement;
- foreign owner remains 404;
- corrupt context returns controlled 503;
- no internal cue or asset UUID leakage;
- result logs contain no metadata narrative.

### Regression

- DesignSpec schema unchanged;
- prompt-builder snapshots unchanged;
- image processor golden files unchanged;
- reference-image rejection tests remain green;
- Phase 12 result page contract remains otherwise compatible.

Commit Part B as:

    feat(generation): apply curated inspiration metadata to DesignSpec

# Part C — User transparency and acknowledgements

## 24. Inspiration-picker explanation

Update the existing questionnaire inspiration step with concise, honest copy:

- selecting inspirations is optional;
- Sitara uses the selected images’ staff-curated descriptions as secondary
  visual cues;
- questionnaire choices remain authoritative;
- the inspiration image files themselves are not sent to the AI providers in
  this version;
- the generated concept will not be an exact copy.

Do not imply reference-image conditioning.

Do not imply guaranteed visual similarity.

Do not expose backend implementation details or provider names.

## 25. Review-page explanation

Near the selected inspirations in `ReviewSummary`, add a short note explaining:

- selected inspirations guide compatible details only;
- garment type, ceremony, colours, embellishment and coverage answers take
  priority;
- images are used through curated text metadata, not direct image
  conditioning.

The existing Generate button and Phase 12 lifecycle behaviour must remain
unchanged.

## 26. Results-page acknowledgements

Render a section only when
`inspiration_acknowledgements.length > 0`.

Suggested heading:

    Inspiration acknowledgements

For each item:

- display title;
- display attribution when non-empty;
- preserve selection order;
- do not create a link;
- do not refetch the catalogue image;
- do not show the source asset UUID;
- do not show provider cues.

Include nearby plain-language copy:

    Selected inspirations influenced this concept through staff-curated
    descriptions. The source images themselves were not sent to the generation
    models, and the result is not an exact reproduction.

React escaping remains enabled. Do not use `dangerouslySetInnerHTML`.

## 27. Copy and text-download behaviour

Update:

    formatDesignBrief(result)

When acknowledgements exist, include:

- the acknowledgement heading;
- each title;
- each non-empty attribution;
- the metadata-only limitation.

Do not include:

- asset UUIDs;
- provider cues;
- alt text;
- cultural context metadata;
- signed URLs;
- catalogue image URLs;
- rights internals.

The existing fixed text filename remains:

    sitara-design-brief.txt

## 28. Accessibility

Requirements:

- explanation text is associated with the inspiration picker;
- acknowledgements use semantic headings and lists;
- attribution text is ordinary selectable text;
- no colour-only meaning;
- no animation;
- no image URL in accessible text;
- no repeated live-region announcements.

Do not perform the full Phase 17 redesign.

## 29. Frontend tests

Test:

- picker displays metadata-only explanation;
- review page displays the same honest limitation;
- questionnaire values are described as authoritative;
- no copy claims exact visual matching;
- results render zero, one and three acknowledgements;
- selection order is preserved;
- empty attribution is handled;
- attribution text is escaped;
- no asset UUID appears;
- no provider cue appears;
- no catalogue image is fetched on the results page;
- copied brief contains acknowledgements;
- copied brief excludes IDs, URLs and internal metadata;
- downloaded brief uses the same safe formatter;
- existing result/image queries and signed-URL refresh remain unchanged;
- no browser storage is touched.

Commit Part C as:

    feat(frontend): disclose inspiration influence and acknowledgements

# Evaluation and decision record

## 30. Create ADR 0014

Create:

    docs/decisions/0014-inspiration-metadata-influence.md

Record:

- Phase 2 did not establish an inspiration-influence winner;
- metadata-only influence is the current MVP implementation;
- existing approved catalogue metadata is reused:
  - garment type;
  - alt text as visual description;
  - cultural context;
- titles and attribution are stored for private audit/display but not sent to
  providers;
- questionnaire selections override inspiration cues;
- DesignSpec remains schema version 1;
- spec template becomes version 2.0.0;
- image-prompt builder remains version 3.0.0;
- exact inspiration context is snapshotted and hashed on DesignVersion;
- rights and metadata are rechecked and locked before persistence;
- image bytes and URLs are never sent to Anthropic;
- reference-image input remains rejected before Replicate;
- metadata influences FLUX only through the validated DesignSpec;
- no promise of visible influence or similarity is made;
- result acknowledgements come from the historical snapshot;
- later rights revocation blocks new use but does not silently rewrite history;
- deletion obligations after revocation remain an unresolved policy question;
- direct image conditioning requires:
  - a scoped model evaluation;
  - current capability/pricing/terms verification;
  - rights review;
  - prompt and provider-contract review;
  - a separate approved phase.

Do not reuse ADR number 0002; that number already belongs to the application
foundation.

## 31. Update existing documents

Update:

- `README.md`;
- `docs/PROPOSAL.md`;
- `docs/phases/PHASES.md`;
- `docs/phases/phases-13.md`;
- ADR 0001 with a short Phase 13 implementation note;
- ADR 0006 with the metadata-use and historical-snapshot amendment;
- ADR 0009 with the new trusted-context shape and template version;
- ADR 0013 with the additive acknowledgement result field;
- `CLAUDE.md` only for permanent inspiration privacy/rights rules.

Do not rewrite historical experiment evidence.

Do not claim the inspiration-influence evaluation has been completed.

Do not mark the Phase 10 paid checkpoint complete.

# Validation

## 32. Build and dependencies

No dependency change is expected.

Run:

    docker compose config
    docker compose build api web
    docker compose up -d
    docker compose exec api python -m pip check
    docker compose exec web npm ci

Prove:

- no Python dependency drift;
- no npm dependency drift;
- no lockfile change unless genuinely required.

## 33. Backend

Run:

    docker compose exec api python manage.py check
    docker compose exec api python manage.py makemigrations --check --dry-run
    docker compose exec api python manage.py migrate
    docker compose exec api pytest
    docker compose exec api ruff check .
    docker compose exec api ruff format --check .

A DesignVersion migration is expected. No catalogue migration is expected.

## 34. DesignSpec and prompt guards

Run:

    docker compose exec api python manage.py export_design_spec_schema

Prove no DesignSpec schema drift:

    git diff --exit-code -- apps/api/sitara/generation/schemas/design_spec_v1.json

Run:

    docker compose exec api pytest \
      sitara/generation/tests/test_prompt_builder.py \
      sitara/generation/tests/test_prompt_snapshots.py

Confirm:

- `DESIGN_SPEC_SCHEMA_VERSION == 1`;
- `SPEC_TEMPLATE_VERSION == "2.0.0"`;
- `PROMPT_BUILDER_VERSION == "3.0.0"`;
- prompt snapshots are unchanged;
- only the structured-generation template fingerprint changes.

## 35. OpenAPI and frontend

Run:

    docker compose exec api python manage.py spectacular \
      --format openapi-json \
      --file openapi/schema.json \
      --validate \
      --fail-on-warn

Then, after committing the deliberate schema update, prove no uncommitted
drift.

Run:

    docker compose exec web npm run generate:api
    docker compose exec web npm run lint
    docker compose exec web npm run typecheck
    docker compose exec web npm test -- --run
    docker compose exec web npm run build

Prove no uncommitted generated-type drift.

## 36. Existing lifecycle and storage

Run:

    docker compose exec api pytest \
      sitara/questionnaire/tests/test_fixture_versions.py

Confirm:

- questionnaire v1 fingerprint unchanged;
- questionnaire v2 remains draft.

Run the image-processor golden tests and confirm:

    DESIGN_IMAGE_PROCESSOR_VERSION == "1.0.0"

Run the Phase 10/11 provider-free fixture pipeline with selected synthetic
inspirations.

Confirm:

- one DesignVersion;
- one versioned inspiration snapshot;
- one prompt;
- one permanent image;
- zero provider clients;
- no catalogue image reads.

## 37. Celery

Run:

    docker compose exec api python -c \
      "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"

Confirm:

- generation task remains registered;
- worker listens to `generation,celery`;
- image-only resume never rebuilds inspiration context;
- retry after image/storage interruption makes no text-provider call.

## 38. Phase 2 integrity

From `experiments/model-eval` run:

    .venv/Scripts/python -m pytest tests/test_model_decision.py -q

Confirm:

    git diff -- experiments/model-eval/outputs/

is empty.

Do not run a live inspiration experiment.

# Offline manual checkpoint

Keep all provider gates closed.

Use synthetic, rights-verified catalogue assets only.

1. Create at least three synthetic approved inspiration assets:
   - one garment-compatible cue set;
   - one cue set with a different garment type;
   - one full-coverage or head-drape cue set.
2. Confirm every asset uses a generated local test image and verified synthetic
   rights data.
3. Create two otherwise identical complete designs:
   - Design A with no inspirations;
   - Design B with one to three inspirations.
4. Inspect the provider-free GenerationContext for both.
5. Confirm Design B contains only:
   - position;
   - garment type;
   - visual description;
   - cultural context.
6. Confirm it contains no:
   - image bytes;
   - image URL;
   - storage key;
   - hash;
   - rights evidence;
   - asset title;
   - attribution;
   - asset UUID.
7. Run fixture generation for Design B.
8. Confirm:
   - DesignVersion has the exact snapshot and hash;
   - result API exposes only acknowledgement title/attribution;
   - result page displays acknowledgements;
   - copied/downloaded brief includes acknowledgements;
   - the prompt builder snapshots remain unchanged.
9. Retire one source asset after generation.
10. Confirm:
    - the existing private result still shows its stored acknowledgement;
    - a new design selecting that asset cannot generate;
    - the historical DesignVersion is not rewritten.
11. Test one unsafe metadata string.
12. Confirm generation rejects it before any provider/client construction.
13. Confirm a non-empty `reference_image_urls` tuple is still rejected before
    Replicate.
14. Confirm zero network calls.

# Paid influence checkpoint — pending, do not execute automatically

This checkpoint requires separate explicit authorisation and a fixed budget.

It does not block merging the code because public live generation remains
disabled, but it must be completed before Sitara claims that inspiration
selection causes useful visible influence or before live generation is publicly
enabled in Phase 16.

When separately authorised:

1. Re-verify current Anthropic and Replicate pricing, terms and model
   capabilities.
2. Use private local infrastructure only.
3. Use synthetic or genuinely rights-approved inspiration assets.
4. Create two designs with identical:
   - questionnaire version;
   - answers;
   - ordering;
   - generation parameters.
5. Design A selects no inspiration.
6. Design B selects curated inspirations.
7. Run the structured-spec stage first and compare:
   - canonical selections unchanged;
   - coverage unchanged;
   - compatible colour/fabric/embellishment/drape cues visible;
   - no source titles or attribution in the DesignSpec;
   - no copying language;
   - no tradition conflation.
8. Only with additional image-generation budget, run one paired image
   comparison.
9. Review manually for:
   - sensible visible influence;
   - no garment substitution;
   - no coverage loss;
   - no embellishment contradiction;
   - no over-literal copying;
   - no logos/text/designer marks;
   - no identity, pose or composition reproduction.
10. Record only safe aggregate observations in ADR 0014.
11. Do not commit prompts, user data, prediction IDs, generated images, signed
    URLs, keys or billing details.
12. Close all paid gates after the checkpoint.

A disappointing or ambiguous result must be recorded honestly. It does not
justify enabling reference-image conditioning.

# Integrity requirements

Before phase approval, confirm:

- zero Anthropic calls;
- zero Replicate calls;
- no provider client instantiated in CI;
- no inspiration image bytes read during generation context construction;
- no inspiration image bytes sent to any provider;
- no catalogue URL sent to any provider;
- no storage key sent to any provider;
- no rights evidence sent to any provider;
- no asset UUID/title/attribution sent to Anthropic;
- no reference-image parameter sent to Replicate;
- non-empty references remain rejected;
- questionnaire selections remain authoritative;
- no DesignSpec schema change;
- spec template deliberately bumped to 2.0.0;
- image-prompt builder remains 3.0.0;
- prompt snapshots unchanged;
- image processor remains 1.0.0;
- no questionnaire v1 change;
- questionnaire v2 remains draft;
- no Phase 2 evidence change;
- no Docker volume deletion;
- no public design route;
- no new catalogue upload path;
- no user-uploaded inspiration;
- no arbitrary metadata engine;
- no refinement;
- no demo fixture matching;
- no rate-limit or spend-control implementation;
- `LIVE_GENERATION_ENABLED` remains false by default;
- hosted CI is green.

# Pull request

Use a phase branch such as:

    phase/phase-13-inspiration-metadata

Open a draft pull request into `main` with a title such as:

    phase-13: rights-safe inspiration metadata influence

Do not merge it.

# Final response

Return only:

1. phase branch;
2. Part A full SHA;
3. Part B full SHA;
4. Part C full SHA;
5. inspiration-context schema and version;
6. canonical provider cue shape;
7. persisted DesignVersion provenance fields;
8. migration and constraints;
9. approval-time metadata safety;
10. pre-spend eligibility and safety behaviour;
11. final rights/asset locking behaviour;
12. stale-context protection;
13. system-prompt changes;
14. `SPEC_TEMPLATE_VERSION` and fingerprint;
15. confirmation that DesignSpec schema remains version 1;
16. confirmation that prompt builder remains version 3.0.0;
17. provider-request contents;
18. reference-image rejection behaviour;
19. result API acknowledgement shape;
20. frontend disclosure and acknowledgement behaviour;
21. copy/download behaviour;
22. backend test results;
23. frontend test results;
24. OpenAPI/generated-type drift;
25. Celery and fixture-pipeline results;
26. questionnaire lifecycle result;
27. Phase 2 integrity result;
28. zero-provider-call confirmation;
29. offline checkpoint results;
30. paid checkpoint status;
31. council decisions and resolved findings;
32. independent Codex decision;
33. hosted CI status;
34. draft PR URL;
35. unresolved issues.