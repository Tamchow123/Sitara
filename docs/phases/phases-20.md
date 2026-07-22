# Sitara Phase 20 — Optional height and body representation

Known repository baseline when this specification was written:

```text
d7cd168091ea901863e03782f45ae8ab263399a9
```

Required starting point:

- the latest `main` must be a clean descendant of the baseline;
- Phases 1–19 must be delivered;
- the current questionnaire versioning, DesignSpec version dispatch, demo engine and annotation work must be green;
- no existing feature may already collect body measurements, weight, dress size or fit data.

Phase 20 adds optional visual representation preferences for the adult model used in a concept image. It does not claim to reproduce the user, predict fit, estimate measurements or produce a sewing pattern.

Before changing anything:

1. Run `git status --short`, `git log -20 --oneline`, `git rev-parse HEAD`, and `git branch --show-current`.
2. Confirm the working tree is clean and Phase 19 is merged.
3. Report any existing representation/body/height taxonomy, feature flag or DesignSpec fields.
4. Do not work directly on `main`; follow the repository's `/run-phase`, branch, per-commit council-review, push and draft-PR workflow.
5. Do not activate the feature publicly until the operator-only representation review and production demo-asset checkpoint pass.

## Main objective

Add two optional questionnaire choices:

- approximate height/proportion representation;
- broad body-frame representation.

Carry them safely through:

- questionnaire versioning and answer validation;
- a new DesignSpec schema version;
- structured DesignSpec generation;
- deterministic image prompting;
- deterministic demo selection;
- review and result descriptions;
- frontend visual controls;
- feature-flagged availability;
- privacy and representation safeguards.

The feature must be:

- optional;
- neutral and non-judgmental;
- visually descriptive rather than medical;
- private as part of the design answers;
- disabled by default until reviewed;
- explicit that it is an approximate concept direction only;
- incapable of generating fit, size, health or constructibility claims.

## Safety mode

During implementation, testing, council review and CI keep:

```text
DEMO_MODE=true
ALLOW_PAID_AI_CALLS=false
LIVE_GENERATION_ENABLED=false
MODEL_REPRESENTATION_ENABLED=false
```

The exact flag name may be refined for consistency but must default false and fail closed.

Use no real provider credentials and perform no paid calls.

Claude Code may prepare an operator-run live validation plan but must not execute it.

Never run:

```text
docker compose down --volumes
```

Tests use synthetic adult model descriptions and project-owned synthetic assets only. Never use a real user's photo, measurements or private design for evaluation.

## Read first

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
- questionnaire, DesignSpec, prompt, demo, UI-polish, deployment and annotation phase documents
- questionnaire and DesignSpec ADRs
- deterministic prompt-builder ADR
- deterministic demo ADR
- privacy, storage and live-generation security ADRs
- `apps/api/config/settings.py`
- public configuration endpoint and tests
- questionnaire schema/answer validation, fixtures, services, views and OpenAPI modules
- all committed DesignSpec classes and JSON schemas
- structured-design context/system prompt and providers
- prompt builder, snapshots and tests
- demo DesignSpec engine, manifest, selector and production-pack installer
- refinement schema/diff allowlists
- result validation and rendering
- generated OpenAPI and TypeScript
- questionnaire visual manifest and card components from Phase 16B
- review/result/refinement frontend components
- privacy/disclaimer pages
- zero-cost E2E suite.

Before implementation, report:

- the currently supported DesignSpec schema versions;
- the active/draft questionnaire versions;
- how feature flags reach the frontend;
- whether the production demo pack can represent the proposed options;
- how the demo selector handles exact versus soft matches;
- any current prompt wording that could cause slimming, idealisation or sexualisation;
- how results distinguish canonical selections from generated narrative;
- the exact activation blockers.

## Required commit boundaries

Implement as five independently reviewed commits:

1. `feat(questionnaire): add optional model representation taxonomy behind a flag`
2. `feat(generation): add DesignSpec representation version and safe prompting`
3. `feat(demo): add exact representation matching and fail-closed support`
4. `feat(frontend): add respectful representation controls and disclaimers`
5. `docs(phase-20): record body representation safeguards and validation plan`

Do not combine the commits. Each commit must pass focused tests and the per-commit council before continuing.

## Part A — Feature flag and questionnaire taxonomy

### 1. Add a fail-closed feature flag

Add an environment-driven boolean equivalent to:

```text
MODEL_REPRESENTATION_ENABLED=false
```

Requirements:

- defaults false in every environment;
- invalid values refuse startup without echoing raw input;
- expose only the boolean through the existing public configuration endpoint;
- the frontend hides the questions when false;
- the backend rejects new non-null representation answers when false;
- historical designs that already contain valid representation values remain readable;
- demo and live generation cannot claim support merely because the UI is hidden;
- production activation requires both the human live-review checkpoint and the production demo-asset checkpoint.

Do not make the flag depend on provider credentials.

### 2. Add optional height/proportion representation

Add an optional `single_choice` question with a stable id such as:

```text
model_height_band
```

Canonical options:

- `petite_proportions` — Petite proportions
- `average_height_proportions` — Average-height proportions
- `tall_proportions` — Tall proportions

Suggested descriptions:

- Petite: "A shorter overall adult frame and proportion direction."
- Average: "A mid-range adult height and proportion direction."
- Tall: "A taller overall adult frame and proportion direction."

Requirements:

- no exact height is promised;
- descriptions may mention approximate ranges only if user research requires them and the UI simultaneously states that image generation cannot reproduce exact measurements;
- no preference is null/absence through the Phase 16B no-preference control;
- do not collect exact centimetres/feet/inches;
- do not infer the selection from account data, uploaded imagery or prior designs.

### 3. Add optional body-frame representation

Add an optional `single_choice` question with a stable id such as:

```text
body_representation
```

Canonical options:

- `slender_frame` — Slender frame
- `straight_frame` — Straight frame
- `athletic_frame` — Athletic frame
- `softly_curved_frame` — Softly curved frame
- `fuller_figure` — Fuller figure

Requirements:

- present all options equally with neutral wording;
- no option is default, recommended, "ideal", "flattering" or ranked;
- no preference maps to null/absence;
- use broad visual representation only;
- do not collect weight, BMI, dress size, bust/waist/hip measurements or health information;
- do not use "obese", "overweight", "skinny", "problem area", "conceal", "correct" or similar judgmental language;
- do not add hourglass/pear/apple labels in this phase;
- do not infer pregnancy, disability, medical condition, age, ethnicity or gender identity;
- Sitara continues to render one adult bridalwear model.

### 4. Add respectful explanatory visuals

Use the Phase 16B rights-controlled visual-manifest mechanism.

Requirements:

- use project-owned neutral line illustrations or abstract proportion diagrams;
- do not use real body photographs unless separately rights-approved and reviewed;
- illustrations must avoid sexualised poses, face detail and body ranking;
- all options use consistent clothing, pose, crop and drawing style so the only intended distinction is representation;
- useful alt text;
- text fallback remains available;
- missing/unapproved visuals keep the feature disabled rather than substituting an unrelated body;
- no remote URLs in questionnaire JSON.

### 5. Questionnaire versioning

Create a new draft questionnaire version unless the immediately previous version is demonstrably still unpublished and safely editable.

Never mutate an active/retired version.

Keep the representation questions in their own clearly labelled optional step or subsection. Do not make them prerequisites for generating a design.

Server answer validation remains authoritative and must reject representation values when the feature flag is disabled.

## Part B — DesignSpec schema and structured generation

### 6. Add a new DesignSpec schema version

Adding canonical source selections requires a new committed schema version.

If Phase 16B created DesignSpec v2, this phase should normally add v3. If repository state differs, use the next integer version.

New `source_selections` fields:

```text
model_height_band: MachineValue | None
body_representation: MachineValue | None
```

Requirements:

- retain full validation and reading support for every historical schema version;
- commit the new JSON schema;
- dispatch strictly by integer `schema_version`;
- unknown versions fail safely;
- historical DesignSpecs and prompts remain immutable;
- no bulk migration of old JSON;
- all new strings remain bounded and unknown fields forbidden;
- generated TypeScript/result validation understands all supported versions;
- bump structured-design template version;
- do not add a free-form body-description narrative field.

The body choices should remain canonical machine selections. Do not ask the LLM to embellish or editorialise them.

### 7. Update structured-design instructions

The trusted system instructions must state:

- representation fields are approximate visual directions for an adult model;
- preserve canonical values exactly;
- do not infer measurements;
- do not describe the body as better, worse, ideal, flattering, slimming or corrective;
- do not change the selected body representation in concept prose;
- do not sexualise or exaggerate body shape;
- do not turn body preferences into garment-fit claims;
- garment coverage and cultural requirements remain higher-priority constraints;
- construction caveats remain present.

The generated `concept_summary`, styling notes and other narrative should remain garment-focused. Avoid repeated body commentary.

### 8. Add deterministic safe prompt mappings

Bump `PROMPT_BUILDER_VERSION`.

Render the selected representation from fixed source-controlled mappings rather than generated prose.

Example intent:

- `petite_proportions` -> "one adult model with petite overall height proportions";
- `average_height_proportions` -> "one adult model with average-height proportions";
- `tall_proportions` -> "one adult model with tall overall height proportions";
- `slender_frame` -> "a slender adult frame";
- `straight_frame` -> "a straight adult frame";
- `athletic_frame` -> "an athletic adult frame";
- `softly_curved_frame` -> "a softly curved adult frame";
- `fuller_figure` -> "a fuller-figure adult frame".

Final wording may be improved through review, but must remain:

- positive;
- neutral;
- non-medical;
- non-sexualised;
- free of exact measurements;
- free of fit guarantees;
- free of comparative/idealising language.

Prompt ordering:

1. composition/full-length catalogue framing;
2. canonical coverage and neckline;
3. optional model representation;
4. garment construction and remaining design details;
5. closing coverage/integrity reinforcement.

Requirements:

- representation must not push the garment out of frame;
- camera wording must not distort petite or tall models to force identical proportions;
- no negative prompt;
- no model switch;
- no reference-image conditioning;
- no raw note text;
- no user identity;
- snapshots and manifest regenerated through documented commands;
- existing persisted prompts never rewritten.

### 9. Result and review copy

Show a neutral section such as "Model representation preferences" only when one or both values are selected.

Always include nearby copy equivalent to:

> These are approximate visual directions for the concept model. They are not body measurements, sizing, fit advice or a guarantee of exact proportions.

Do not phrase the output as "your body" unless the user explicitly supplied that meaning; the system only knows a selected model representation.

Copy/download brief must include the same limitation.

### 10. Refinement behaviour

In this phase:

- existing refinement preserves both source-selection fields;
- do not add a body/height refinement category;
- refinement-generated prose cannot contradict the canonical representation;
- annotations do not feed into representation;
- changing body representation requires starting a new design/draft, not silently changing a generated version.

## Part C — Deterministic demo support

### 11. Extend the demo manifest

Add bounded fields equivalent to:

```text
model_height_band
body_representation
```

Requirements:

- null means the asset is not approved for a specific claimed representation;
- when the user selected a representation, demo matching is exact for that field;
- do not use soft fallback to a different body representation;
- do not claim a visual match based only on prompt text;
- preserve deterministic tie-breaking;
- bump manifest/selector versions;
- historical selections remain reproducible;
- production assets must be explicitly reviewed for the metadata assigned to them.

### 12. Fail closed when demo support is incomplete

Public demo mode is Sitara's default safe experience. The feature may not activate with a misleading asset pack.

Requirements:

- if selected height/body combination has no approved compatible asset, return a controlled `demo_representation_unavailable` result before presenting a mismatched concept;
- preferably keep the feature flag false until an adequate production pack exists;
- development synthetic assets may cover every code path but are rejected outside development;
- no generic/default body substitution;
- no provider fallback from demo mode;
- no paid call.

Define a practical production coverage matrix before activation. It need not contain every garment x ceremony x colour x body cross-product, but it must cover each enabled height band and body representation across enough garment families to avoid systematic bias. Exact selected representation remains mandatory.

### 13. Demo DesignSpec engine

Produce the same new DesignSpec version as the live path.

Requirements:

- exact canonical values;
- deterministic wording;
- no body judgment;
- no raw notes;
- same safety scan;
- same result disclaimer;
- zero provider client construction/network calls.

## Part D — Frontend UX

### 14. Add an optional representation step

Use existing schema-driven visual cards and no-preference controls.

Requirements:

- clearly label the section optional;
- explain it changes the concept model, not garment sizing;
- render height and body questions separately;
- use consistent neutral illustrations;
- allow clearing either answer;
- do not preselect an option;
- selected states do not rely only on colour;
- mobile grid remains compact;
- descriptions expand accessibly;
- feature flag false hides the complete section;
- server rejection when disabled maps to controlled user-facing copy.

### 15. Add privacy and representation copy

State clearly:

- preferences remain private with the design;
- Sitara does not infer the user's body;
- no exact measurements are collected;
- outputs may not match exact proportions;
- this is not sizing, fit or tailoring advice;
- generated imagery may contain model limitations.

Do not suggest uploading a body photo in this phase.

### 16. Accessibility and inclusive review

- keyboard-only flow;
- useful alt text;
- no ranking/order that implies value;
- same card size and prominence;
- screen-reader labels avoid judgmental shorthand;
- contrast and focus pass;
- test at 320px and large desktop widths;
- human copy review by at least one person outside the implementation author;
- cultural/representation review recorded before feature activation.

## Privacy and data handling

Treat these answers as private design inputs even though they are broad categories.

Requirements:

- no analytics event may include the selected values;
- no log or Sentry event may include them;
- no profile-level storage or cross-design preference memory;
- no inference from user identity, prior design, uploaded image or annotation;
- export-my-data behaviour includes them only as part of the user's private design record if such export exists;
- retention deletes them with the design;
- no advertising/personalisation use;
- no claim that the values describe the user.

## Operator-run live representation validation plan

Claude Code must write but not execute a bounded validation plan.

Use synthetic, non-user specifications only.

The matrix must cover:

- all body-representation options;
- all height bands;
- at least three garment families;
- both lower and fuller coverage;
- more than one ceremony;
- more than one colour/fabric combination.

A practical run may use a pairwise matrix rather than every cross-product, but every enabled option must be observed multiple times before public activation.

Score each output for:

- selected height-direction adherence;
- selected body-frame adherence;
- full-length framing;
- garment type and construction;
- coverage fidelity;
- anatomy;
- absence of body distortion;
- absence of slimming/corrective reinterpretation;
- absence of sexualisation;
- absence of caricature/exaggeration;
- equivalent garment detail quality across representations;
- respectful overall portrayal.

Record failures per criterion. Do not approve the feature from one successful image.

Activation remains blocked until:

- bounded live matrix reviewed;
- production demo pack coverage reviewed;
- copy/illustrations reviewed;
- no systematic representation failure remains;
- operator explicitly enables the feature flag.

## OpenAPI and generated client

Update:

- public config boolean;
- questionnaire nested option/answer types as required;
- DesignSpec/result version union;
- controlled disabled/unavailable errors.

Requirements:

- deterministic generation;
- no JWT/bearer auth;
- no private configuration values exposed;
- generated TypeScript not hand-edited;
- CI drift checks updated.

## Automated tests

Add focused tests for at least:

### Configuration and questionnaire

- flag defaults false;
- invalid flag fails safely;
- public config exposes only boolean;
- UI hides when false;
- backend rejects new non-null values when false;
- both questions optional and single-choice;
- no-preference clears to null;
- unknown values rejected;
- no exact measurements accepted;
- questionnaire version immutability preserved.

### DesignSpec and prompting

- every historical schema version still validates;
- new schema version validates;
- unsupported version fails safely;
- canonical representation values echoed exactly;
- generated narrative does not contain banned judgmental terms;
- prompt contains only fixed allowlisted representation wording;
- representation appears after coverage and before decorative details;
- prompt remains full-length and garment-focused;
- no exact measurements, BMI, weight, size, user identity or raw notes;
- deterministic snapshots and manifest;
- refinement preserves values.

### Demo

- exact representation match required when selected;
- mismatch fails controlled;
- missing production support fails closed;
- development synthetic pack rejected outside development;
- deterministic selection independent of manifest order;
- all enabled values represented in manifest validation;
- zero provider client/network calls.

### Frontend

- feature flag behaviour;
- visual cards and descriptions;
- no default selection;
- clear to no preference;
- neutral result/review copy;
- disabled/unavailable error mapping;
- keyboard and screen-reader behaviour;
- axe checks;
- no selected values sent to analytics mocks/logging.

### E2E

With development synthetic assets:

1. enable the feature locally;
2. select height and body representation;
3. complete demo generation;
4. confirm review and result limitation copy;
5. confirm exact demo metadata match;
6. disable the flag and confirm the section disappears and API rejects bypassed values;
7. prove zero provider calls.

## Commands and validation

At minimum run:

```bash
docker compose build api web
docker compose up -d
docker compose ps

docker compose exec api python manage.py check
docker compose exec api python manage.py makemigrations --check --dry-run
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .

docker compose exec web npm run generate:api
docker compose exec web npm test -- --run
docker compose exec web npm run lint
docker compose exec web npm run typecheck
docker compose exec web npm run build

docker compose exec web npx playwright test
```

Run OpenAPI, TypeScript, DesignSpec schema and prompt-snapshot generation twice and prove no second-run diff.

Do not modify frozen Phase 2 outputs.

## Manual checkpoint

In local development with the feature explicitly enabled and no provider credentials:

1. Verify no option is preselected.
2. Select petite proportions and fuller figure.
3. Complete a demo design and verify exact manifest metadata match.
4. Confirm review/result copy says model representation, not "your body".
5. Clear both answers and generate another design.
6. Attempt an unsupported exact combination and confirm controlled fail-closed behaviour.
7. Disable the flag and confirm the questions disappear.
8. Bypass the frontend and confirm backend rejection.
9. Inspect logs, Sentry test transport and analytics mocks for absence of selected values.
10. Complete the flow keyboard-only and at 320px.
11. Confirm no provider wrapper/client/network call.
12. Confirm public activation remains blocked pending operator-run live and production-pack review.

## Non-goals

Do not implement:

- exact height entry;
- weight, BMI or dress size;
- bust/waist/hip or tailoring measurements;
- fit prediction;
- sewing patterns;
- body-photo upload;
- face likeness or avatar creation;
- skin-tone, age, disability or pregnancy inference;
- medical or health advice;
- body ranking or "flattering" recommendations;
- saving representation as an account preference;
- automatic inference from previous designs;
- representation refinement;
- image-to-image editing;
- new provider/model selection;
- public sharing;
- paid calls during implementation.

## Documentation and decision record

Add the next available ADR documenting:

- optional model representation rather than user-body modelling;
- disabled-by-default feature flag;
- exact taxonomy and neutral wording;
- no exact measurements or fit claims;
- new DesignSpec schema version and historical support;
- fixed allowlisted prompt mappings;
- prompt ordering;
- exact demo matching and fail-closed behaviour;
- private data handling;
- human representation-review requirement;
- activation gates;
- deferred measurements, avatars and image uploads.

Update:

- `docs/phases/PHASES.md`;
- proposal/README capability wording;
- privacy and disclaimer pages;
- runbook feature-activation checklist;
- demo asset-pack documentation;
- operator live-validation document;
- relevant DesignSpec, prompt and demo ADRs.

## Completion report

Report:

- starting and ending commit;
- feature flag and defaults;
- questionnaire version and exact option copy;
- DesignSpec and prompt-builder versions;
- fixed prompt mappings;
- demo manifest/selector versions and coverage;
- production asset gaps;
- privacy/logging/analytics verification;
- generated artifacts changed;
- tests and commands run;
- manual local checkpoint;
- council findings and resolutions;
- explicit confirmation of zero paid/provider calls;
- explicit list of public activation blockers;
- each commit SHA and draft PR URL.
