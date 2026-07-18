# Sitara Phase 9 — Deterministic image-prompt builder

Starting commit:

6df78d80b679dd0bb70eaa94633977feb342763d

Read before editing:

- @CLAUDE.md
- @docs/phases/PHASES.md
- @docs/PROPOSAL.md
- @docs/decisions/0001-image-model.md
- @docs/decisions/0009-structured-design-spec-generation.md
- @apps/api/sitara/generation/
- @apps/api/sitara/designs/models.py
- @apps/api/sitara/designs/services.py
- @experiments/model-eval/src/model_eval/prompt_formats.py
- @experiments/model-eval/prompts/briefs.yaml
- @experiments/model-eval/configs/screening.yaml
- @experiments/model-eval/configs/model_candidates.yaml

Implement this work as two focused commits:

1. `fix(generation): make final spec persistence atomic`
2. `feat(generation): add deterministic image prompt builder`

Do not combine them.

Do not make Anthropic or Replicate calls. Do not begin Celery generation,
generation API endpoints, image storage, results UI, inspiration influence or
refinement.

---

# Part A — Final Phase 8 atomicity hardening

## 1. Make the final freshness check and persistence atomic

The current service rechecks the Design and its input snapshot, then enters a
separate transaction to create the DesignVersion. Close that check-to-write
window.

After the provider request has completed and produced a valid DesignSpec:

1. enter a new short `transaction.atomic()` block;
2. acquire the Design row with `select_for_update()`;
3. re-run completion and inspiration-eligibility validation on that locked row;
4. recompute the input snapshot;
5. compare it with the pre-provider snapshot;
6. calculate the next version number;
7. create and fully populate the DesignVersion;
8. commit.

The same Design row lock must cover steps 3–7.

Do not hold a transaction or row lock during the Anthropic call.

Refactor `create_next_design_version` narrowly if needed so already-locked
callers can create the version without opening a disconnected check/write
sequence. Preserve its existing public behaviour and concurrency guarantees.

A concurrent draft mutation must either:

- commit before the final lock and cause `DesignChangedDuringGeneration`; or
- wait until after the generated version transaction commits.

It must never commit between the final freshness check and version creation.

## 2. Tighten claim-negation scope

The generated-content scanner must not treat any unrelated earlier negation in
a sentence as negating a later claim.

Use deterministic local/clause-aware handling. A negation should excuse a
claim only when it directly governs that claim, such as within a small
preceding token window and without crossing a clause boundary.

Reject:

- `This is not merely inspiration; it is a sewing pattern.`
- `No embellishment is used, and this is a sewing pattern.`
- `This is not a mood board but is a sewing pattern.`
- `The design is not plain; it can be constructed exactly as shown.`

Continue accepting:

- `This is not a sewing pattern.`
- `This concept does not guarantee constructibility.`
- `It cannot be constructed exactly as shown.`
- `It does not guarantee that the garment can be constructed exactly as shown.`

Never echo rejected text in errors or logs.

## 3. Part A tests

Add tests proving:

- a mutation before the final row lock causes safe rejection;
- the final snapshot check and DesignVersion creation use the same transaction
  and locked Design row;
- no DesignVersion is created after a detected change;
- no provider retry occurs after a freshness failure;
- the new negation bypass cases are rejected;
- legitimate disclaimer wording still passes;
- all existing Phase 8 tests remain green.

Commit Part A as:

```text
fix(generation): make final spec persistence atomic
Part B — Deterministic image-prompt builder
4. Follow the evaluated FLUX prompt approach

Phase 2 selected the environment-configured
black-forest-labs/flux-1.1-pro as the MVP model.

The screening evidence used an editorial text prompt. The selected model does
not expose a genuine negative-prompt input or documented JSON prompting.

Therefore Phase 9 must produce:

one deterministic natural-language image prompt string;
no separate negative prompt;
no JSON prompt;
no hard-coded Replicate model identifier;
no provider call.

Use the positive presentation conventions established by Phase 2:

full-length studio fashion photograph;
entire garment visible from head to hem;
clean, uncluttered studio background;
original, non-branded textile and embroidery design;
natural anatomy and coherent visible hands;
soft, even lighting showing true fabric colour and embroidery detail.

Do not add a universal modesty, sleeve or neckline suffix. Coverage must come
only from the DesignSpec because a generic suffix could contradict the user's
validated choices.

5. Add the pure prompt builder

Create:

apps/api/sitara/generation/prompt_builder.py

Define:

PROMPT_BUILDER_VERSION = "1.0.0"
IMAGE_PROMPT_MAX_CHARS = 6000

def build_image_prompt(spec: DesignSpec) -> str:
    ...

The function must be pure and deterministic:

no database access;
no environment reads;
no random values;
no timestamps;
no network access;
no provider SDK imports;
identical validated input always produces identical UTF-8 output.

Accept either a validated DesignSpec or revalidate defensively at the
boundary. Run the existing generated-content safety scan before interpolation.

6. Fixed prompt ordering

Render the visual information in this exact conceptual order:

garment and ceremony;
silhouette and garment components;
drape, layering and visual proportions;
colour palette and placement;
fabrics, texture, finish and movement;
embellishment techniques, density, placement and motifs;
coverage, neckline, sleeves, back, midriff and head covering;
dupatta or saree drape;
broad cultural direction and styling cues;
fixed presentation instructions.

The prose may use sentences and short paragraphs, but ordering must remain
stable and snapshot-tested.

Do not include:

construction_caveats;
image_alt_text;
provider metadata;
token usage;
database identifiers;
questionnaire labels or the complete questionnaire schema;
inspiration metadata or image references;
raw questionnaire free text;
Anthropic prompts;
system instructions.
7. Garment-integrity cues

Add a very small source-controlled set of integrity cues for categories that
had meaningful confusion risks in Phase 2:

gharara: fitted through the upper leg and knee, with the flare beginning
below the knee;
sharara: trousers flaring from the waist or upper leg, without a gharara
knee joint;
saree: visibly draped fabric with a pallu over a blouse, not converted into
a stitched gown.

These cues derive only from source_selections.garment_type.

Do not create a broad cultural rules engine or duplicate the whole
questionnaire taxonomy.

8. Controlled narrative slots

Every DesignSpec narrative string is generated text and must enter the prompt
through named, bounded slots.

Create a deterministic helper that:

applies Unicode NFKC normalisation;
converts CRLF/CR to LF;
collapses internal whitespace;
removes leading/trailing whitespace;
preserves meaningful words;
enforces a documented per-slot character cap;
truncates only at a word boundary;
never inserts raw HTML, Markdown or control characters.

Critical machine selections and coverage choices must not be silently removed.

The combination of slot caps must keep the prompt below
IMAGE_PROMPT_MAX_CHARS. Treat an unexpected overrun as a controlled
ImagePromptBuildError; do not slice the completed prompt in a way that might
remove its coverage or presentation sections.

After building, perform a final safety check proving the prompt contains no:

blocked designer or brand;
imitation phrase;
URL;
prompt leakage;
untrusted-section delimiter;
control character.
9. Positive-only presentation

Do not append a section labelled Negative prompt.

Do not append the Phase 2 controlled negative list because FLUX 1.1 Pro does
not expose a genuine negative-prompt parameter.

Express the safeguards positively through the fixed presentation language,
including original/non-branded design, clean composition and natural anatomy.

Do not promise:

photorealistic identity;
exact constructibility;
preservation between refinements;
designer imitation;
historical authenticity beyond the validated concept.
10. Prompt version and snapshot guard

Create versioned golden fixtures and expected prompt snapshots, for example:

apps/api/sitara/generation/tests/fixtures/prompt_builder/
apps/api/sitara/generation/tests/snapshots/image_prompt/v1/

Use original synthetic DesignSpecs. Do not copy external product descriptions.

Cover at least:

all six garments:
lehenga;
saree;
gharara;
sharara;
anarkali;
shalwar kameez;
all six ceremonies:
nikah;
mehndi;
baraat;
walima;
pheras;
reception;
minimal, balanced and heavy embellishment;
none embellishment;
full-sleeve/high-neck/full-coverage preferences;
head drape;
double dupatta;
saree drape;
no regional direction;
a real broad regional direction.

A fixture may cover more than one category. Avoid creating a full combinatorial
matrix.

Commit exact .txt snapshots.

Add a deterministic combined snapshot hash or version manifest tied to
PROMPT_BUILDER_VERSION.

Tests must fail when prompt output changes until:

snapshots are deliberately reviewed;
the builder version is deliberately updated;
the versioned manifest/hash is deliberately updated.

Do not silently overwrite snapshots during normal tests.

An explicit developer command may regenerate snapshots, but tests and CI must
run in comparison-only mode.

11. Persist prompt provenance

Extend DesignVersion with:

image_prompt
prompt_builder_version

Suggested fields:

image_prompt: TextField(blank=True);
prompt_builder_version: CharField(max_length=32, blank=True).

Database constraints:

both fields are empty or both are populated;
an image prompt cannot exist without design_spec;
existing Phase 8 DesignVersions with only a DesignSpec remain valid and can
be backfilled;
no prompt is required for legacy rows without a DesignSpec.

Do not add:

image model;
seed;
image storage changes;
Replicate prediction IDs;
negative prompt;
reference-image fields.

Those belong to later phases.

Make both fields read-only in Django admin.

12. Add an atomic persistence service

Create a service such as:

build_and_store_image_prompt(design_version) -> DesignVersion

Requirements:

use transaction.atomic();
lock the DesignVersion row;
require a persisted DesignSpec;
require the supported DesignSpec schema version;
revalidate the stored JSON through DesignSpec;
run generated-content safety validation;
build the deterministic prompt;
persist the exact prompt and PROMPT_BUILDER_VERSION;
save both fields together;
return the updated version.

Immutability:

first build populates the missing fields;
rerunning with the same version and exact prompt is idempotent;
an existing different prompt or builder version is never overwritten;
rebuilding with a future builder must create a new DesignVersion rather than
rewriting historical audit data.

Use a safe domain exception that never includes the prompt contents.

13. Add an offline management command

Add:

python manage.py build_image_prompt --design-version <uuid>

Optional:

--show-prompt

The command must:

perform zero provider calls;
report the DesignVersion UUID;
report the prompt-builder version;
report prompt character count;
optionally print only the persisted prompt when explicitly requested;
never print user answers, Anthropic context, API keys or internal storage
metadata;
refuse safely when the version has no DesignSpec;
remain idempotent for an already-matching prompt.

Do not modify generate_spec to call Replicate or perform image generation.

14. Tests

Add tests proving:

Pure builder
deterministic repeated output;
exact golden snapshots;
fixed section/field order;
global character cap;
deterministic word-boundary slot truncation;
coverage details survive;
ordered colours/fabrics/embellishments remain ordered;
no raw untrusted questionnaire note appears;
no inspiration metadata or IDs appear;
construction caveats and alt text are excluded;
fixed positive presentation text is present;
no Negative prompt section exists;
no configured model ID is embedded;
no designer/brand can enter;
prompt leakage and URLs are rejected.
Cultural and garment integrity
gharara contains the knee-fitted/below-knee-flare cue;
sharara contains the waist/upper-leg flare cue;
saree remains a draped garment with pallu;
minimal/no embellishment does not gain heavy embellishment language;
head-covering and full-coverage selections remain visible;
no regional direction does not invent one;
a supplied broad regional direction is framed as influence, not a universal
rule.
Persistence
prompt and version save together;
database all-or-none constraints;
prompt requires a DesignSpec;
existing Phase 8 rows remain valid;
same build is idempotent;
differing historical prompt is immutable;
concurrent builds create one identical stored prompt;
admin fields are read-only.
Integrity
no Anthropic client constructed;
no Replicate client constructed;
socket/network guard remains active;
no OpenAPI operation changes;
no frontend generated-type changes.
15. Documentation

Create:

docs/decisions/0010-deterministic-image-prompt-builder.md

Record:

DesignSpec remains the only model-authored generation contract;
the image prompt is produced by deterministic application code;
editorial text format follows the Phase 2 evaluated path;
why no JSON or negative prompt is used for the current default model;
positive-only presentation wording;
fixed prompt ordering;
garment-integrity cues;
narrative slot limits;
prompt/version immutability;
prompt persistence for reproducibility and audit;
inspiration influence remains deferred to Phase 13;
no provider calls or image generation in Phase 9.

Update:

README.md;
docs/phases/PHASES.md;
docs/PROPOSAL.md;
CLAUDE.md only where a genuinely permanent rule is introduced.

Do not mark the Phase 8 paid live checkpoint as complete.

16. Validation

Backend:

docker compose exec api python manage.py check
docker compose exec api python manage.py makemigrations --check --dry-run
docker compose exec api python manage.py migrate
docker compose exec api python -m pip check
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .
docker compose exec api python manage.py export_design_spec_schema
git diff --exit-code -- apps/api/sitara/generation/schemas/design_spec_v1.json

OpenAPI:

docker compose exec api python manage.py spectacular `
  --format openapi-json `
  --file openapi/schema.json `
  --validate `
  --fail-on-warn
git diff --exit-code -- apps/api/openapi/schema.json

Frontend regression:

docker compose exec web npm run generate:api
git diff --exit-code -- apps/web/src/api/schema.d.ts
docker compose exec web npm run lint
docker compose exec web npm run typecheck
docker compose exec web npm test -- --run
docker compose exec web npm run build

Celery:

docker compose exec api python -c "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"

Phase 2:

Push-Location experiments/model-eval
.venv/Scripts/python -m pytest tests/test_model_decision.py -q
Pop-Location

Do not run:

docker compose down --volumes
17. Manual checkpoint

Using at least five synthetic fixture DesignVersions:

build each prompt through the management command;
inspect a lehenga, saree, gharara, sharara and full-coverage concept;
confirm the garment identity remains correct;
confirm minimal embellishment remains visually restrained;
confirm coverage and draping details are present;
confirm the full garment/head-to-hem presentation wording is present;
confirm there are no designer names, logos, typography requests, raw user
notes or inspiration metadata;
rerun the command and confirm idempotency;
confirm zero provider calls occurred.

No real Replicate generation is part of this checkpoint.

18. Integrity

Confirm:

zero Anthropic calls;
zero Replicate calls;
no provider key required;
no new dependency;
no raw questionnaire free text in image prompts;
no inspiration metadata in image prompts;
no model ID hard-coded in builder logic;
no Docker volumes deleted;
no Phase 2 evidence changed;
DesignSpec schema has no drift;
OpenAPI has no drift;
generated TypeScript has no drift;
hosted CI is green after push.
Part B commit

Commit Part B as:

feat(generation): add deterministic image prompt builder

Do not amend or rewrite earlier commits.

Do not push unless explicitly requested.

Return

Return only:

Part A full SHA;
Part B full SHA;
final persistence atomicity approach;
prompt-builder structure and version;
fixed field ordering;
slot limits and safety behaviour;
garment-integrity cues;
DesignVersion migration and constraints;
snapshot/version guard;
management-command behaviour;
backend results;
frontend regression results;
schema/OpenAPI/type drift;
Celery and Phase 2 results;
manual checkpoint result;
zero-provider-call confirmation;
unresolved issues;
hosted CI status.