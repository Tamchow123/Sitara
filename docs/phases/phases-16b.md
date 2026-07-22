# Sitara Phase 16B — Questionnaire feedback, cultural expansion and visual choice UX

Known repository baseline when this specification was written:

```text
d7cd168091ea901863e03782f45ae8ab263399a9
```

The latest `main` must be a clean descendant of that commit and must contain:

- the delivered Phase 16 security and live-generation cost controls;
- the merged generated-image composition and coverage-first prompt work;
- `PROMPT_BUILDER_VERSION` 5.0.0 or a reviewed later descendant;
- the complete Phase 15 deterministic demo pipeline.

Phase 16B is an inserted phase. It does **not** replace or reopen the delivered numbered Phase 16. It must be completed before Phase 17's final visual-polish and accessibility pass.

Before changing anything:

1. Run `git status --short`, `git log -20 --oneline`, `git rev-parse HEAD`, and `git branch --show-current`.
2. Confirm the working tree is clean.
3. Confirm the latest main contains the composition/coverage prompt change and that the current roadmap still places Phase 17 after this work.
4. Report unexpected application-code commits, active questionnaire-version conflicts, or documentation conflicts before proceeding.
5. Do not work directly on `main`; follow the repository's `/run-phase`, branch, per-commit council-review, push and draft-PR workflow.
6. Use the current repository structure. Do not reintroduce `backend/`, `frontend/`, `docker-compose.yml`, old provider names, local phase-agent copies or duplicated generation pipelines.

## Main objective

Implement the first substantial user-feedback revision to Sitara's questionnaire and wizard:

- add satin as a fabric;
- add a culturally reviewed Sikh wedding ceremony option centred on Anand Karaj;
- add a dedicated, mutually exclusive neckline question;
- expand the curated bridal colour vocabulary without creating an unusable scrolling list;
- give optional single-choice questions an explicit, reversible "No preference — let Sitara decide" interaction;
- prevent contradictory head-covering, midriff, neckline and dupatta selections;
- carry every new canonical answer safely through validation, DesignSpec generation, deterministic demo generation, prompt building, results and historical version reading;
- introduce rights-controlled, schema-driven visual option cards with expandable descriptions;
- provide a compact, accessible grouped swatch selector for colours;
- preserve the existing privacy, cultural-safety, zero-cost demo and paid-provider controls.

This phase is not a cosmetic reskin. It changes questionnaire schema capabilities and canonical generation inputs, so the backend contract, generated TypeScript, demo engine and prompt builder must remain aligned.

## Safety mode throughout this phase

During implementation, tests, council review, CI and manual checkpoints keep:

```text
DEMO_MODE=true
ALLOW_PAID_AI_CALLS=false
LIVE_GENERATION_ENABLED=false
```

Use no real Anthropic or Replicate credentials.

Claude Code must not perform paid generations. Any future live visual-adherence check is operator-run only, with explicit budget authorisation, current provider pricing and synthetic non-user designs.

Never run:

```text
docker compose down --volumes
```

Do not delete or reset development volumes.

Do not place real user images, private generated images, signed URLs, provider responses, credentials or rights evidence into tests, fixtures, logs, screenshots or commits.

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
- `docs/phases/phases-5*.md`
- `docs/phases/phases-6.md`
- `docs/phases/phases-7.md`
- `docs/phases/phases-8.md`
- `docs/phases/phases-9.md`
- `docs/phases/phases-13.md`
- `docs/phases/phases-14.md`
- `docs/phases/phases-15.md`
- `docs/phases/phases-16.md`
- the generated-image composition and live-validation phase documents
- `docs/decisions/0005-versioned-questionnaire-schema.md`
- `docs/decisions/0006-rights-controlled-inspiration-catalogue.md`
- `docs/decisions/0009-structured-design-spec-generation.md`
- `docs/decisions/0010-deterministic-image-prompt-builder.md`
- `docs/decisions/0015-single-round-refinement.md`
- `docs/decisions/0016-deterministic-demo-mode.md`
- `apps/api/sitara/questionnaire/schema_validation.py`
- `apps/api/sitara/questionnaire/answer_validation.py`
- `apps/api/sitara/questionnaire/services.py`
- `apps/api/sitara/questionnaire/views.py`
- `apps/api/sitara/questionnaire/openapi.py`
- `apps/api/sitara/questionnaire/fixtures/questionnaire_v1.json`
- `apps/api/sitara/questionnaire/fixtures/questionnaire_v2.json`
- all questionnaire fixture, schema, answer-validation and API tests
- `apps/api/sitara/generation/context.py`
- `apps/api/sitara/generation/design_spec.py`
- committed DesignSpec JSON schemas
- structured-design prompting and provider adapters
- `apps/api/sitara/generation/prompt_builder.py`
- prompt snapshots, manifests and prompt-builder tests
- `apps/api/sitara/generation/demo/`
- `apps/api/sitara/generation/refinement.py`
- `apps/api/sitara/generation/refinement_service.py`
- `apps/api/sitara/designs/result.py`
- `apps/api/sitara/designs/openapi.py`
- `apps/api/openapi/schema.json`
- `apps/web/src/api/schema.d.ts`
- `apps/web/src/features/questionnaire/QuestionField.tsx`
- questionnaire rules, validation, wizard, review and draft-persistence components
- results/design-brief components
- refinement option and comparison components
- existing global styles and accessible form primitives.

Before implementation, report:

- which questionnaire version is active in fixtures and local development data;
- whether questionnaire v2 has ever been published or remains safely editable as a draft;
- the current DesignSpec schema-version strategy;
- how optional radio answers are currently cleared, if at all;
- how compatibility rules behave when the source question is multi-choice;
- where demo-manifest fields and selector weights must change;
- which existing refinement categories could conflict with a new canonical neckline;
- the exact files and generated artifacts expected to change.

## Required commit boundaries

Implement as five independently reviewed commits:

1. `feat(questionnaire): extend taxonomy and option presentation metadata`
2. `feat(generation): add versioned neckline and ceremony semantics`
3. `feat(demo): support the expanded questionnaire taxonomy`
4. `feat(frontend): add visual choices, no-preference controls, and compact colours`
5. `docs(phase-16b): record questionnaire feedback architecture and safeguards`

Do not combine the commits. Each commit must pass focused tests and the per-commit council before moving to the next.

## Part A — Questionnaire schema and taxonomy

### 1. Preserve questionnaire-version immutability

Never modify an active or retired questionnaire version.

Inspect the real repository and local database state before choosing the new fixture version:

- If questionnaire v2 is still a draft everywhere represented by the repository and local development data, it may be extended in place.
- If there is any evidence that v2 has been activated, retired, copied into deployed data or relied upon as published history, create v3 instead.
- Never rewrite v1.
- Record the decision and evidence in the completion report and ADR.
- Fixture loading must remain deterministic and must not create two active versions.
- Activation remains transactional and must retire the previous active version exactly as the existing service requires.

### 2. Extend option presentation metadata narrowly

Extend the strict questionnaire option shape with optional, bounded fields equivalent to:

```json
{
  "visual_key": "neckline_classic_crew",
  "group": "necklines"
}
```

Requirements:

- `visual_key` and `group` are optional lower-case machine identifiers, not URLs or file paths.
- Reuse the existing machine-id validation rules and sensible maximum lengths.
- Unknown option keys remain rejected.
- Do not allow arbitrary HTML, CSS, colours, remote URLs, storage keys, Markdown or executable presentation data in questionnaire JSON.
- Update nested OpenAPI serializers and regenerate the committed OpenAPI schema and TypeScript types.
- Existing v1/v2 options without presentation fields remain valid.
- The frontend must fall back to a normal text option when a visual key is absent or unknown.

### 3. Add satin

Add the canonical option:

```json
{
  "value": "satin",
  "label": "Satin",
  "description": "A smooth fabric with a lustrous face that catches light cleanly."
}
```

Preserve the existing maximum fabric selection count unless repository evidence shows a separate user-validated reason to change it.

Carry `satin` through:

- questionnaire fixture and tests;
- answer validation;
- DesignSpec source selections;
- deterministic demo phrasing;
- prompt construction;
- result/review labels;
- demo manifest matching where fabric metadata is used.

Do not treat satin and silk as synonyms.

### 4. Add Sikh ceremony inclusion carefully

Add one initial, culturally reviewed ceremony option:

```json
{
  "value": "anand_karaj",
  "label": "Anand Karaj",
  "description": "The Sikh marriage ceremony centred on the Anand Karaj rites."
}
```

Requirements:

- Do not label this merely "Sikh wedding".
- Do not silently map it to Nikah, Pheras, Baraat, Walima or a generic reception.
- Add bounded source-controlled cultural guidance and safeguards to the structured-design context.
- The generated DesignSpec must preserve `ceremony == "anand_karaj"` exactly.
- Add tests against religious and ceremonial conflation.
- Do not add Jaggo, Maiyan or other community events in this phase unless an explicit reviewed definition and product behaviour are supplied during implementation.
- The production demo pack must contain at least one approved, culturally reviewed Anand Karaj-compatible asset before this questionnaire version is activated in production demo mode.
- If exact demo support is absent, fail closed or keep the new questionnaire version inactive; never show a misleading nearest ceremony.
- Record a manual cultural-review checkpoint. Automated tests cannot replace human review.

### 5. Add a dedicated neckline question

Add an optional `single_choice` question with a stable id such as:

```text
neckline_style
```

Include these canonical options, subject to final copy review:

- `classic_crew` — Classic crew
- `curved_scoop` — Curved scoop
- `v_neck` — V-neck
- `deep_v_neck` — Deep V-neck
- `boat_neck` — Boat neck
- `square_neck` — Square neck
- `sweetheart_neck` — Sweetheart
- `high_neck` — High neck
- `band_collar` — Band or mandarin collar

Requirements:

- Exactly one neckline may be chosen.
- The question is optional; no preference is represented by an absent/null answer, not a persisted `"no_preference"` option.
- Give each option an accurate concise description and an approved `visual_key`.
- Do not use the old multi-select `high_neckline` coverage value as a second competing neckline choice.
- Migrate the draft taxonomy so there is one authoritative neckline decision.
- Historical answers containing `coverage_preferences=["high_neckline"]` remain readable and generate correctly.
- New questionnaire versions should use `neckline_style="high_neck"` instead of adding `high_neckline` to the coverage list.
- Add compatibility rules or authoritative answer-validation checks so complete/high coverage cannot coexist with `deep_v_neck`, `sweetheart_neck` or another deliberately low/open neckline.
- Do not silently rewrite a user's invalid combination. The UI should restrict or explain it, and the server must reject any bypassed invalid submission with a field-safe error.

### 6. Correct coverage and dupatta compatibility

Preserve the current coverage-first prompt behaviour and add authoritative input-level consistency.

At minimum:

- `full_midriff` means no exposed waist.
- `full_back` means no open-back construction.
- `full_sleeves` means wrist-length sleeves.
- a selected covered-head preference must produce a head-compatible drape;
- `dupatta_style="head_drape"` always means fabric visibly over the head;
- `double_dupatta` may satisfy head covering only when one layer is explicitly assigned to the head;
- one-shoulder, arm-only or other non-head drapes cannot contradict a mandatory covered-head selection;
- saree-specific pallu behaviour remains distinct from dupatta behaviour.

Use the existing declarative rule engine where it can express the behaviour safely. If it cannot, add the smallest explicit allowlisted validation needed. Do not introduce a general expression language.

Server validation is authoritative. Frontend restriction alone is insufficient.

### 7. Expand the curated colour vocabulary

Keep the current colours and add a reviewed set approximately equivalent to:

- ruby
- burgundy
- coral
- rose
- dusty rose
- mauve
- lavender
- lilac
- plum
- sage
- mint
- olive
- forest green
- turquoise
- powder blue
- royal blue
- bronze
- copper
- taupe

Requirements:

- Stay within the schema's bounded option limit.
- Keep stable lower-case machine identifiers.
- Retain the current maximum of four selected lead colours unless separately justified.
- Assign every colour to a source-controlled group such as neutrals, reds, pinks, yellows/metallics, greens, blues/teals or purples.
- Use curated swatches, not an unrestricted colour picker.
- Do not send hex values to Anthropic or Replicate as the canonical answer.
- Prompt and result text continue to use human-readable colour names derived from canonical machine values.
- Review adjacent colours for distinguishability and accessible labelling.

## Part B — DesignSpec and generation semantics

### 8. Introduce a new DesignSpec schema version

A dedicated neckline answer changes `source_selections`; do not mutate the committed DesignSpec v1 contract in place.

Implement a version-aware DesignSpec strategy:

- retain full reading and validation support for schema version 1;
- add schema version 2 with `source_selections.neckline_style: MachineValue | None`;
- generate and commit `design_spec_v2.json`;
- dispatch validation by persisted `schema_version`;
- never rewrite historical v1 DesignSpecs;
- new designs using the revised questionnaire produce v2;
- old questionnaire versions may continue to produce/read v1 where appropriate;
- update result rendering, safety scans, persistence, OpenAPI-facing result validation and test factories to understand both;
- keep the structure bounded and `extra="forbid"`;
- bump the structured-design template version because generation semantics changed;
- regenerate any prompt hashes or contract snapshots through documented commands only.

Avoid a broad generic schema framework. Implement only the version dispatch required for known versions.

### 9. Preserve exact source selections

The generated DesignSpec must echo validated canonical answers exactly:

- garment type;
- ceremony, including `anand_karaj`;
- regional direction;
- silhouette;
- ordered colour list;
- ordered fabric list, including satin;
- embellishment values;
- coverage preferences;
- dedicated neckline;
- dupatta or saree drape.

No LLM is allowed to rename, omit, reinterpret or normalise these canonical values.

### 10. Update structured-design guidance

Update the trusted structured-output context and system instructions so that:

- Anand Karaj is handled as a Sikh marriage ceremony without conflating it with other religious rites;
- garment choices remain South Asian bridalwear concept directions rather than religious costume claims;
- dedicated neckline and coverage fields are mutually coherent;
- complete coverage is described concretely rather than with vague "modest" wording;
- head covering means fabric over the head, not merely jewellery or an ornament;
- satin is distinguished from silk and raw silk;
- no designer names, logos or imitation requests are introduced.

The structured provider must still return the strict schema only. Do not add hidden prose fields or unbounded cultural metadata.

### 11. Update the deterministic image prompt

Bump `PROMPT_BUILDER_VERSION` because the canonical prompt inputs and visual requirements change.

Requirements:

- preserve composition-first and coverage-second ordering;
- render the canonical neckline early, beside other coverage requirements;
- render satin and all new colours directly from canonical selections;
- retain the closing coverage reinforcement;
- prevent generated narrative from contradicting the canonical neckline;
- do not add a negative prompt;
- do not switch model;
- do not add JSON prompting;
- do not add provider calls;
- do not include raw questionnaire free text;
- regenerate and manually review golden prompt snapshots and the manifest;
- existing persisted prompts and builder versions remain immutable.

### 12. Refinement compatibility

Existing refinements must continue to preserve `source_selections`.

In this phase:

- do not allow a refinement to silently change `neckline_style`;
- ensure coverage refinement prose cannot contradict the canonical neckline;
- remove or adjust any refinement UI copy that falsely suggests the dedicated canonical neckline can be replaced;
- keep the one-refinement limit unchanged;
- do not add image-to-image editing.

## Part C — Deterministic demo support

### 13. Update the local deterministic DesignSpec engine

The demo builder must produce the same DesignSpec schema version and canonical source selections as the live structured provider for the revised questionnaire.

Add deterministic, safe phrasing for:

- satin;
- every new colour;
- every neckline;
- Anand Karaj;
- corrected head-covering and midriff semantics.

Raw optional notes must still never be copied into generated narrative.

### 14. Update demo manifest and selection

Add bounded optional manifest metadata needed to match:

- ceremony;
- neckline;
- fabric;
- colour groups;
- coverage/head-drape requirements.

Requirements:

- exact garment matching remains mandatory;
- selected head covering cannot match an asset with an uncovered head;
- selected full-midriff coverage cannot match an exposed-midriff asset;
- Anand Karaj requires an explicitly reviewed compatible production asset;
- the development synthetic pack may contain clearly synthetic test assets but is never production content;
- missing required production coverage fails closed rather than selecting a misleading image;
- selector behaviour remains deterministic and independent of manifest order;
- manifest and selector versions are bumped;
- existing historical demo selections remain reproducible.

Do not create paid demo assets or call providers in this phase. Prepare an operator checklist for supplying and approving any missing production assets.

## Part D — Visual questionnaire UX

### 15. Add a rights-controlled visual manifest

Create a frontend-owned, source-controlled manifest for questionnaire explanatory visuals.

Suggested location:

```text
apps/web/src/features/questionnaire/visuals/
```

Each visual entry must contain bounded metadata equivalent to:

- `visual_key`;
- local public asset path;
- intrinsic width and height;
- concise alt text;
- asset kind, such as illustration or swatch;
- rights status;
- source/ownership note suitable for repository audit;
- content hash or another deterministic integrity value.

Requirements:

- use local, project-owned, commissioned or explicitly licensed assets only;
- never accept arbitrary remote URLs from questionnaire JSON;
- do not reuse inspiration-catalogue assets automatically;
- questionnaire visuals explain options and must not influence DesignSpec generation;
- missing/unapproved visuals fall back to text;
- tests reject duplicate keys, absent files, invalid dimensions, unsafe paths and unapproved production assets;
- preserve a clean distinction between production visuals and development placeholders.

Mandatory first visual coverage:

- every garment type;
- every silhouette;
- every neckline;
- every dupatta and saree-drape style;
- every colour through accessible swatches.

Fabric and embellishment visuals may use text fallback until an approved asset pack exists. Do not download unlicensed images merely to reach numerical coverage.

### 16. Replace the dense option list with accessible cards

Refactor the schema-driven question renderer into focused components rather than one ever-growing conditional file.

Create components equivalent to:

- `ChoiceOptionCard`;
- `ChoiceOptionGrid`;
- `ColourSwatchGrid`;
- `ExpandableOptionDescription`;
- `NoPreferenceControl`.

Requirements:

- retain real radio and checkbox inputs;
- preserve `<fieldset>` and `<legend>`;
- render a responsive grid when options have visuals;
- keep a text-list fallback;
- selected state must not rely only on colour;
- descriptions are concise by default and expandable through an actual button;
- use `aria-expanded` and `aria-controls`;
- selection remains possible without expanding;
- keyboard, touch and pointer input all work;
- focus rings remain visible;
- images have useful alt text or are correctly decorative when the adjacent text fully communicates the distinction;
- lazy-load later-step assets without layout shift;
- hidden/restricted options do not fetch their images;
- no horizontal overflow at 320px;
- no carousel that hides choices from keyboard or screen-reader users.

### 17. Add reversible no-preference behaviour

For every optional single-choice question:

- show an explicit "No preference — let Sitara decide" control;
- selecting it clears the persisted answer to null/absence;
- it remains visually understandable when the stored answer is empty;
- selecting a real option replaces no preference;
- users can return to no preference after choosing a radio option;
- review screens display "No preference" rather than silently omitting the question;
- required questions never expose this control;
- server answer validation remains authoritative.

Do not add a fake schema option unless repository constraints make null impossible and the alternative is reviewed first.

### 18. Add compact grouped colour selection

Implement a compact labelled swatch grid:

- group colours using the schema's bounded `group` metadata;
- show the selected count, for example "3 of 4 selected";
- pin or summarise selected colours above the full grid;
- provide text labels on the swatch and to assistive technology;
- indicate selection with shape/icon/border as well as colour;
- preserve ordered selection because earlier choices carry more weight;
- allow deselection without reordering the remaining values unexpectedly;
- disable only unselected colours at the maximum;
- provide a clear "Show more colours" or grouped disclosure on small screens without hiding the selected state;
- do not use an unrestricted native colour picker;
- do not make users scroll through one long single-column list.

## API and contract requirements

Regenerate the committed OpenAPI schema and frontend TypeScript types.

Add or update tests proving:

- option presentation metadata is typed;
- no arbitrary URL/path field is accepted;
- DesignSpec v1 and v2 result payloads validate;
- old result fixtures remain readable;
- revised questionnaire answer fields are typed and persisted;
- generated files are deterministic;
- CI detects contract drift.

Do not hand-edit generated files.

## Automated tests

Add focused tests for at least:

### Questionnaire schema and answers

- published questionnaire versions remain immutable;
- draft/new version validates;
- satin is accepted and unknown fabric values are rejected;
- Anand Karaj is distinct from every existing ceremony;
- neckline is optional and single-choice;
- old high-neck coverage answers remain valid historically;
- invalid neckline/coverage combinations are rejected;
- head-covering and dupatta contradictions are rejected;
- option visual metadata accepts bounded machine ids only;
- arbitrary URLs, paths, HTML and unknown keys are rejected;
- colour list count and selection bounds remain valid.

### DesignSpec and generation

- schema v1 still validates historical fixtures;
- schema v2 validates new fixtures;
- unsupported schema versions fail safely;
- canonical `neckline_style`, satin, colours and ceremony are echoed exactly;
- prompt order remains composition first and coverage/neckline second;
- high/covered selections cannot yield open-neckline prompt wording;
- prompt snapshots are deterministic;
- no raw notes, provider metadata, model identifiers or negative prompts appear;
- refinement cannot contradict canonical neckline.

### Demo

- same revised input yields the same spec, prompt and selected asset;
- covered-head selections never match uncovered-head metadata;
- full-midriff selections never match exposed-midriff metadata;
- Anand Karaj fails closed when exact approved production support is absent;
- synthetic development assets are rejected outside development;
- zero provider client construction and zero provider network calls remain proven.

### Frontend

- visual cards retain semantic radio/checkbox inputs;
- unknown visual keys fall back to text;
- no-preference clears optional answers and cannot clear required answers;
- expanded descriptions expose correct ARIA state;
- colour selection order and maximum are preserved;
- selected state is understandable without colour;
- compatibility restrictions update immediately;
- review summary displays neckline, expanded colours, satin, Anand Karaj and no preference correctly;
- axe checks pass for image cards, swatches and disclosure controls;
- keyboard-only interaction covers select, deselect, expand and clear.

## Commands and validation

Use current repository commands. At minimum run:

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
```

Run OpenAPI generation twice and prove the committed schema is deterministic.

Run the documented DesignSpec schema-generation and prompt-snapshot commands twice and prove the committed artifacts do not drift on the second run.

Run the repository's complete Phase 2 experiment unit suite if shared prompt or taxonomy helpers are touched. Do not modify frozen experiment outputs.

## Manual checkpoint

In `DEMO_MODE=true`, with no provider keys configured:

1. Complete one Anand Karaj design.
2. Select satin.
3. Select a dedicated neckline.
4. Select complete midriff/back coverage and a covered-head treatment.
5. Use the compact colour selector and choose four colours in a known order.
6. Clear an optional dupatta or regional preference back to "No preference".
7. Complete generation through the normal async demo pipeline.
8. Confirm the review, DesignSpec, image prompt and result brief agree.
9. Confirm no provider wrapper/client/network call occurs.
10. Repeat keyboard-only at mobile and desktop widths.
11. Inspect visual rights-manifest validation and verify no arbitrary remote asset is loaded.
12. Record the pending operator-only live coverage/cultural visual validation separately; do not perform it.

## Non-goals

Do not implement:

- stylist annotation tools;
- height/body representation;
- user-uploaded questionnaire visuals;
- arbitrary remote image URLs;
- a generic CMS or media-management framework;
- unrestricted custom colours;
- additional Sikh events without reviewed definitions;
- internationalisation;
- sharing or public galleries;
- image-to-image refinement;
- a new FLUX model;
- reference-image conditioning;
- extra refinements;
- a sewing pattern, measurement or fit system;
- paid provider calls during implementation.

## Documentation and decision record

Add the next available ADR documenting:

- why the work is Phase 16B rather than reopening delivered Phase 16;
- questionnaire-version choice;
- option `visual_key`/`group` design;
- rights boundary between explanatory visuals and inspiration assets;
- DesignSpec v2 and historical v1 support;
- dedicated neckline semantics;
- no-preference as null/absence;
- cultural handling of Anand Karaj;
- coverage/dupatta consistency;
- colour grouping;
- demo fail-closed requirements;
- prompt-builder version bump;
- deferred annotations and body representation.

Update:

- `docs/phases/PHASES.md`;
- `README.md` where user-facing capability summaries changed;
- relevant questionnaire, generation, demo and prompt ADRs;
- the manual live-validation plan with new synthetic cases, without performing paid calls.

## Completion report

Report:

- starting and ending commit;
- questionnaire version selected and why;
- DesignSpec and prompt-builder version changes;
- exact taxonomy additions;
- compatibility rules added;
- visual asset coverage and any approved-asset gaps;
- demo-pack coverage and any activation blockers;
- generated artifacts changed;
- tests and commands run;
- manual demo checkpoint results;
- council findings and resolutions;
- explicit confirmation of zero paid calls and zero provider client construction;
- remaining operator-only cultural and live-image validation;
- each commit SHA and draft PR URL.
