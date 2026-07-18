# Sitara Phase 10 — Durable Celery generation pipeline and Replicate rendering

Starting commit:

3c5bc642a23129555b07f8b04fc0bfd0793d8eeb

Read before editing:

- @CLAUDE.md
- @docs/PROPOSAL.md
- @docs/phases/PHASES.md
- @docs/phases/phases-8.md
- @docs/phases/phases-9.md
- @docs/decisions/0001-image-model.md
- @docs/decisions/0004-private-design-ownership.md
- @docs/decisions/0005-versioned-questionnaire-schema.md
- @docs/decisions/0009-structured-design-spec-generation.md
- @docs/decisions/0010-deterministic-image-prompt-builder.md
- @apps/api/config/celery.py
- @apps/api/config/settings.py
- @apps/api/sitara/ai_gateway/
- @apps/api/sitara/generation/
- @apps/api/sitara/designs/
- @compose.yaml
- @experiments/model-eval/configs/screening.yaml
- @experiments/model-eval/configs/model_candidates.yaml

Implement this phase as two focused commits:

1. `feat(generation): add durable asynchronous generation jobs`
2. `feat(generation): add gated Replicate image rendering`

Do not combine them.

Part A must pass before beginning Part B.

Make zero live provider calls while implementing or running automated tests.
The paid checkpoint remains separately authorised and must not be executed
automatically.

Do not begin:

- final image transcoding or thumbnail creation;
- signed design-image URLs;
- permanent final image layout;
- frontend progress or results pages;
- inspiration metadata influence;
- reference-image conditioning;
- refinement;
- demo-mode fixture matching;
- public deployment;
- Redis rate limits;
- per-user generation limits;
- daily spend ceilings;
- retention purge jobs.

Those belong to later phases.

---

# Baseline

Run:

```powershell
git status --short
git log -12 --oneline
docker compose config
docker compose up -d
docker compose ps

Run the full existing backend, frontend, OpenAPI, prompt-snapshot, Celery and
Phase 2 integrity checks before editing.

Confirm:

questionnaire v1 fingerprint remains unchanged;
questionnaire v2 remains draft;
prompt-builder version remains 3.0.0;
no Phase 2 evidence is modified.

Do not run:

docker compose down --volumes
Part A — Durable asynchronous generation jobs
1. Evolve Design lifecycle status

Extend Design.Status to:

draft
generating
generated
generation_failed

Add a database constraint restricting the field to those values.

State rules:

a new design starts as draft;
successful enqueue changes it to generating;
successful raw-image staging changes it to generated;
terminal pipeline failure changes it to generation_failed;
a failed design with no DesignVersion may be edited again;
the first successful edit of such a design moves it back to draft;
a design with an existing DesignVersion is no longer draft-editable;
a generated design is never editable through the Phase 7 draft endpoint.

Update update_design_draft under its existing Design row lock:

allow ordinary edits only in draft;
also allow recovery edits in generation_failed only when the design has no
DesignVersion;
after a successful recovery edit, set status to draft;
reject all other edits with a safe design_not_editable domain error;
never modify or remove an existing DesignVersion.

Update the PATCH API and OpenAPI contract with the controlled 409 response.

2. Reshape GenerationAttempt

The existing attempt currently requires a DesignVersion, but the asynchronous
job must exist before the DesignSpec and DesignVersion exist.

Change it to include:

design                     required FK, CASCADE
design_version             nullable FK, SET_NULL
idempotency_key            UUID, unique per Design rather than globally
celery_task_id              bounded blank string

status
error_code
started_at
completed_at

image_provider
image_model
image_prediction_id
image_seed
image_parameters

staged_image_storage_key
staged_image_sha256
staged_image_size_bytes
staged_image_width
staged_image_height

created_at
updated_at

Suggested bounds:

task ID: 255;
provider: 32;
model: 100;
prediction ID: 128;
storage key: 255;
SHA-256: exactly 64 when supplied.

image_parameters is server-authored JSON containing only safe reproducibility
values such as:

{
  "aspect_ratio": "3:4",
  "output_format": "webp",
  "output_quality": 80,
  "safety_tolerance": 2,
  "prompt_upsampling": false
}

It must never contain:

the prompt;
an API token;
output URL;
provider error body;
questionnaire answers;
inspiration metadata;
raw image data.

Migration requirements:

add the required Design FK initially nullable;
backfill it from the old design_version.design_id;
make it non-null after backfill;
make design_version nullable;
remove global uniqueness from idempotency_key;
add unique (design, idempotency_key);
preserve any existing rows;
no destructive migration.

Add a PostgreSQL partial unique constraint enforcing at most one in-progress
attempt per Design, where status is one of:

queued
running_text
running_image

Add database checks for:

valid status;
non-negative seed when present;
positive staged size/dimensions when present;
staged key/hash/size/width/height are all populated together or all absent;
succeeded requires:
DesignVersion;
staged image metadata;
blank error code;
completed timestamp;
failed requires:
non-empty stable error code;
completed timestamp.

Do not require image provenance during text-stage or queued states.

3. Attempt ownership and privacy

Every attempt belongs to its Design and therefore inherits the Design’s private
ownership.

Never expose:

provider name;
model identifier;
prediction ID;
seed;
parameters;
storage key;
image hash;
image size;
provider errors;
Celery task ID.

Inaccessible jobs return the same 404 as nonexistent jobs.

An anonymous workspace promoted after registration/login must retain access to
its attempts through the existing ownership model.

4. Public job payload

Define one stable public job shape:

{
  "job": {
    "id": "uuid",
    "design_id": "uuid",
    "design_version_id": "uuid-or-null",
    "status": "queued|running_text|running_image|succeeded|failed",
    "error_code": "string-or-null",
    "created_at": "ISO-8601",
    "updated_at": "ISO-8601",
    "started_at": "ISO-8601-or-null",
    "completed_at": "ISO-8601-or-null"
  }
}

No prompt, DesignSpec, image URL or internal provenance appears in Phase 10.

Every response must use:

Cache-Control: no-store
5. Enqueue service

Create a focused service such as:

enqueue_design_generation(
    design,
    *,
    idempotency_key,
    enqueue_task=None,
) -> tuple[GenerationAttempt, bool]

The boolean means whether a new attempt was created.

The service must use one short transaction and lock the Design row.

Order:

Find an attempt for the same Design and same idempotency key.
If found, return it unchanged, regardless of current provider gates.
For a new key, enforce generation availability.
Re-run authoritative complete questionnaire and inspiration validation.
Reject if another attempt is queued/running.
Determine whether the job starts from:
no version: full text → prompt → image pipeline;
one incomplete existing version after an image-stage failure: reuse that
exact version and resume at prompt/image;
a completed version/raw image: reject as already generated.
Create the queued attempt.
Set Design status to generating.
Commit.
Enqueue with transaction.on_commit.

Use the attempt UUID as the deterministic Celery task ID:

task_id=str(attempt.id)

Route it explicitly to:

generation

Duplicate idempotency requests must:

return the same attempt;
not enqueue another Celery task;
not re-run validation;
not create another DesignVersion;
not make provider calls.

A different key while a job is in progress returns:

409 generation_in_progress

An already completed design returns:

409 design_already_generated
6. Broker enqueue failure

Handle broker failure after database commit safely.

Requirements:

mark the attempt failed;
set error_code=queue_unavailable;
set completed_at;
set the Design to generation_failed;
return a controlled 503;
never leave a queued job that was not actually submitted;
log only attempt UUID, Design UUID and exception class;
never log broker URLs or credentials.

A later request with a new idempotency key may retry.

7. Generation API

Add:

POST /api/v1/designs/<uuid>/generate/
GET  /api/v1/jobs/<uuid>/
POST generate

Requirements:

AllowAny because anonymous private workspaces are supported;
mandatory ownership filtering before UUID lookup;
mandatory CSRF;
JSON only;
accept either no body or exactly {};
reject unknown body fields;
require Idempotency-Key header;
require it to be a valid UUID;
return 202 for a newly queued attempt;
return the same 202 payload for an existing identical idempotency request;
include a same-origin Location header pointing to the job endpoint.

Responses:

202 queued or idempotent replay
400 incomplete/invalid questionnaire
400 invalid idempotency key
403 csrf_failed
404 not_found
409 generation_in_progress
409 design_already_generated
409 design_not_generatable
503 generation_unavailable
503 queue_unavailable

Do not reveal whether an inaccessible UUID exists.

GET job

Requirements:

ownership-first 404;
no session/workspace creation for unknown anonymous callers;
return the public job payload;
no provider or storage details;
available even when live generation gates are currently disabled.
8. OpenAPI and frontend transport

Document both operations accurately in OpenAPI.

Regenerate:

apps/api/openapi/schema.json
apps/web/src/api/schema.d.ts

The general generated runtime client remains GET-only.

Add narrow explicit frontend wrappers without building any UI:

startDesignGeneration(
  designId: string,
  idempotencyKey: string,
): Promise<GenerationResult>

fetchGenerationJob(jobId: string): Promise<GenerationJob>

The unsafe wrapper must use the existing:

same-origin transport;
in-memory CSRF token;
retry-CSRF-once behaviour;
no-store policy;
timeout handling.

It must send Idempotency-Key without exposing a generic arbitrary-header API.

Do not add polling UI, TanStack Query or a results route.

9. Celery routing

Add an explicit task route:

sitara.generation.tasks.generate_design_attempt -> generation

Update Compose so the worker listens to both:

generation,celery

The default queue must remain available for the existing health ping.

Use:

acks_late=True;
reject_on_worker_lost=True;
bounded task soft/hard limits;
no infinite retry policy;
no global automatic retry of the whole pipeline.

Never hold a Django transaction or row lock while waiting for a provider.

10. Attempt-level execution lock

Use a non-blocking PostgreSQL advisory lock keyed by the GenerationAttempt UUID
for the whole task execution.

Purpose:

duplicate broker delivery cannot execute the same attempt concurrently;
only one worker polls or stages output for an attempt;
duplicate delivery exits safely;
lock is released in finally.

This lock is separate from the existing Design-level spec-generation lock.

11. Resumable pipeline service

Create a testable service behind the Celery task:

run_generation_attempt(
    attempt_id,
    *,
    structured_provider=None,
    image_provider=None,
    image_downloader=None,
    storage=None,
)

The Celery task calls it with live factories. Tests and the offline command
inject fakes.

The state machine must inspect persisted state and resume safely.

Stage A — claim and pre-check
lock the attempt briefly;
return immediately for succeeded/failed attempts;
set started_at once;
re-check Design ownership-independent domain readiness;
do not reset an existing DesignVersion link;
do not clear image prediction provenance.
Stage B — DesignSpec

When design_version_id is absent:

set attempt status to running_text;
call the existing generate_design_spec_for_design;
add an optional GenerationAttempt integration argument so that the newly
created DesignVersion is linked to the attempt in the same transaction as
DesignVersion creation;
verify the attempt belongs to the same locked Design;
persist no disconnected crash window between creating the version and linking
the attempt.

When design_version_id is already present:

never call Anthropic again;
verify the version belongs to the same Design;
continue from the prompt/image stage.
Stage C — prompt

Call:

build_and_store_image_prompt(design_version)

It is already idempotent.

If the exact prompt already exists, continue without rebuilding history.

Never overwrite another builder version.

Stage D — image

Set status to running_image.

Part A uses an injected fake image provider only. Part B adds Replicate.

Stage E — success

Only after the raw image has been safely staged:

mark attempt succeeded;
set completed timestamp;
clear error code;
set Design status to generated.

All final state changes happen transactionally.

12. Retry/resume guarantees

Persisted stage markers are authoritative.

On task redelivery:

linked DesignVersion means skip Anthropic;
existing image prompt means skip prompt persistence;
existing prediction ID means never submit another prediction;
existing staged object means verify it and finalise rather than regenerate.

Do not use autoretry_for over the entire task.

Only retry explicitly classified safe transient failures.

A terminal failure after a DesignVersion exists leaves that version immutable.
A new API request with a new idempotency key may link the same incomplete
version and retry only the image stage.

13. Stable error codes

Create a source-controlled allowlist, including at least:

queue_unavailable
generation_unavailable
design_incomplete
design_changed
structured_generation_failed
structured_provider_refused
prompt_build_failed
image_provider_unavailable
image_submission_ambiguous
image_prediction_failed
image_prediction_canceled
image_prediction_aborted
image_poll_timeout
image_download_failed
image_output_invalid
image_staging_failed
internal_generation_error

Requirements:

store only a stable code;
never store provider text;
never expose exception messages through /jobs/;
unexpected exceptions become internal_generation_error;
no raw prompt, answers, output URL, key or provider body enters logs.
14. Part A fixture pipeline

Add deterministic test fixtures:

a valid fixture StructuredDesign provider;
a synthetic 3:4 WebP image created locally with Pillow;
an image provider fake returning deterministic prediction states;
transient poll failure then success;
terminal image failure;
ambiguous submission failure;
invalid output bytes.

No fixture may use a network URL or provider SDK.

15. Part A tests

Test at least:

Models and migrations
old attempt rows backfill Design correctly;
Design status constraint;
attempt status constraint;
unique (design, idempotency_key);
same key may exist for different designs;
one in-progress attempt per design;
succeeded/failed database constraints;
staged metadata all-or-none.
Enqueue/API
incomplete design creates no attempt and enqueues nothing;
first request returns 202;
same key returns the same job and queues once;
two concurrent requests with different keys admit exactly one;
second in-progress request gets 409;
inaccessible design/job gets 404;
anonymous ownership works;
login promotion retains access;
CSRF enforced;
malformed UUID header controlled;
non-empty body rejected;
no-store headers;
response leaks no provider/storage values;
broker failure becomes failed attempt + 503;
GET job does not create an anonymous workspace.
Task
queued → running_text → running_image → succeeded;
Design becomes generating then generated;
DesignVersion is linked atomically during spec creation;
task redelivery after linked version does not call text provider;
existing prompt is reused;
transient image-stage failure retries without another text call;
terminal failure stores one safe error code;
a new attempt after image failure reuses the existing DesignVersion;
duplicate task delivery is serialised by the advisory lock;
terminal task invocation is idempotent;
no provider/network module is invoked in Part A tests.

Commit Part A as:

feat(generation): add durable asynchronous generation jobs
Part B — Gated Replicate image rendering
16. Dependency

Add exactly:

replicate==1.0.7

to apps/api/requirements.in.

Regenerate the hashed lock using the existing pinned toolchain:

Python 3.12.7;
pip 26.0.1;
pip-tools 7.5.3;
--generate-hashes.

Requirements:

no prerelease Replicate package;
deterministic second regeneration;
no unrelated upgrades;
pip check clean.

Use only public SDK interfaces.

Do not use experimental replicate.use() or private SDK modules.

17. Settings

Add strict settings:

LIVE_GENERATION_ENABLED=false
REPLICATE_TIMEOUT_SECONDS=30
REPLICATE_POLL_INTERVAL_SECONDS=2
REPLICATE_POLL_TIMEOUT_SECONDS=180
GENERATION_RAW_MAX_BYTES=20000000
GENERATION_RAW_MAX_PIXELS=40000000

Continue using:

DEFAULT_IMAGE_MODEL
REPLICATE_API_TOKEN
DEMO_MODE
ALLOW_PAID_AI_CALLS

Do not introduce a second conflicting image-model environment variable.

Validate:

model is non-empty and at most 100 characters;
timeout/poll/size values are strict positive integers;
poll interval is less than poll timeout;
malformed configuration refuses startup without echoing values.

LIVE_GENERATION_ENABLED gates the public end-to-end API. It does not weaken
the existing standalone Anthropic management-command gates.

Default values must make accidental paid generation impossible.

Update Compose and .env.example.

18. Capability gates

Set explicit code-level flags:

STRUCTURED_DESIGN_PROVIDER_IMPLEMENTED = True
IMAGE_PROVIDER_IMPLEMENTED = True
FULL_GENERATION_PIPELINE_IMPLEMENTED = True

Add:

image_generation_is_available()

It requires:

DEMO_MODE false;
ALLOW_PAID_AI_CALLS true;
image provider capability implemented;
non-empty stripped Replicate token;
valid configured image model.

Update public:

generation_is_available()

It requires:

LIVE_GENERATION_ENABLED true;
structured generation available;
image generation available;
full pipeline implemented.

Behaviour:

demo mode remains unavailable until Phase 15;
token presence alone enables nothing;
API disabled means no task enqueue;
worker re-checks the gates before every new paid provider submission;
a previously accepted prediction may still be polled/staged when the public
API flag is subsequently disabled, avoiding loss of already-paid output;
public config never exposes keys or model IDs.
19. Image provider contract

Create a narrow contract under sitara.ai_gateway, for example:

@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    model: str
    seed: int
    aspect_ratio: str
    output_format: str
    output_quality: int
    safety_tolerance: int
    prompt_upsampling: bool
    reference_image_urls: tuple[str, ...] = ()

Separate provider operations:

create_prediction(request) -> ImagePrediction
get_prediction(prediction_id) -> ImagePrediction
cancel_prediction(prediction_id) -> None

ImagePrediction carries only safe structured metadata:

prediction_id
provider
model
status
output_url

Do not carry:

API tokens;
prompt in returned results;
raw provider error body;
logs;
request headers;
dashboard URL.

Reference-image arguments are reserved for Phase 13.

For now:

the signature accepts the empty tuple;
any non-empty reference collection is rejected before a provider call with
reference_images_not_enabled;
never silently ignore a supplied reference.
20. Replicate provider

Create:

apps/api/sitara/ai_gateway/replicate_provider.py

Requirements:

lazy client creation after all gates pass;
one cached client per provider instance;
explicit API token from settings;
configured timeout;
use the official-model asynchronous prediction-create endpoint;
use the public polling/get operation;
do not use blocking replicate.run();
no streaming;
no webhooks;
no model version/digest hard-coded;
no reference images;
no negative prompt;
no prompt upsampling chosen implicitly.

Inspect the pinned SDK’s public method signatures and use the exact supported
equivalent of the official-model endpoint. Add a contract test against the
pinned SDK interface.

Input must use the reviewed Phase 2 profile:

prompt             exact persisted DesignVersion.image_prompt
seed               generated once and persisted before submission
aspect_ratio       3:4
output_format      webp
output_quality     explicit reviewed value
safety_tolerance   explicit reviewed value
prompt_upsampling  explicit boolean

Use the Phase 2 evidence rather than inventing incompatible settings. Record the
exact parameter dictionary on the attempt.

The configured model remains environment-driven.

21. Seed

Generate one seed with a cryptographically safe local source.

Requirements:

generate and persist it before provider submission;
reuse it for every retry of that attempt;
never generate a second seed after a task restart;
allow zero if supported;
include it in private provenance;
never expose it through the Phase 10 job API.

Tests inject a deterministic seed factory.

22. Prediction submission boundary

Before creating a prediction:

verify all gates;
verify the persisted image prompt;
persist seed and safe parameters;
set attempt status to running_image;
commit;
call Replicate.

After Replicate returns a prediction object:

immediately persist the prediction ID;
never clear or replace it for that attempt;
all retries poll the same prediction.

Replicate does not provide an exactly-once prediction-creation guarantee.

Classify create failures as:

definitely_pre_acceptance
ambiguous_acceptance

Only a definitely pre-acceptance transient failure may retry submission.

An ambiguous timeout/connection loss:

must not automatically submit again;
marks the attempt failed with image_submission_ambiguous;
preserves conservative spend semantics;
never includes provider text.
23. Polling

Poll the persisted prediction ID until a terminal state or configured timeout.

Provider states:

starting
processing
succeeded
failed
canceled
aborted

Rules:

temporary GET/poll transport failures may use bounded Celery retry;
retries use the same prediction ID;
terminal failed/canceled/aborted states do not create a replacement prediction
inside the same attempt;
timeout attempts cancellation through the public SDK method;
timeout ends as image_poll_timeout;
a new user request with a new idempotency key may create a new image-only
attempt against the existing DesignVersion.

Do not sleep while holding a database lock or transaction.

24. Output download boundary

A succeeded prediction must contain exactly one HTTPS output URL.

Allow only:

replicate.delivery
*.replicate.delivery

Requirements:

reject every other hostname;
reject embedded credentials;
reject non-HTTPS URLs;
validate every redirect destination;
bounded timeout;
stream in chunks;
stop immediately over GENERATION_RAW_MAX_BYTES;
never log the URL or query string;
no arbitrary URL supplied by users reaches this downloader.

After download:

verify with Pillow;
allow expected PNG/JPEG/WebP provider image formats;
reject decompression bombs;
enforce GENERATION_RAW_MAX_PIXELS;
record dimensions;
calculate SHA-256;
do not trust extension or Content-Type alone;
do not transcode or create thumbnails in Phase 10.
25. Raw private staging

Immediately copy successful provider output to the existing private default
storage because provider output is temporary.

Use a deterministic staging key such as:

generation-staging/<attempt-uuid>/raw.<verified-extension>

Store the staging key and verified metadata on GenerationAttempt.

Requirements:

do not populate DesignVersion.image_storage_key;
that field remains reserved for Phase 11 final ingest;
no signed URL;
no public URL;
no catalogue path;
no overwrite of a different object;
if the deterministic object exists after task restart, read and verify it;
matching existing object resumes finalisation without another provider call;
conflicting existing content fails safely;
storage remains private.
26. Task failure behaviour

Map provider/domain exceptions to stable codes.

On terminal failure:

mark attempt failed transactionally;
set Design to generation_failed;
preserve linked DesignVersion and prompt;
preserve prediction ID where accepted;
preserve staged data where already safely written;
never delete or rewrite newer design work;
no raw provider message in DB or logs.

On success:

attempt succeeded;
Design generated;
linked version retained;
private staged metadata retained;
no final design image URL exists yet.
27. Offline fixture command

Add:

python manage.py run_generation_fixture --design <uuid>

Optional:

--idempotency-key <uuid>

The command must:

use the real enqueue/state-machine services;
inject fixture structured and image providers;
generate a synthetic local WebP;
stage it through the same validation/storage path;
make zero network calls;
report:
attempt UUID;
DesignVersion UUID;
final status;
staged byte count;
SHA-256;
print no prompt, answers, storage key or private provider metadata;
be idempotent for the supplied key.
28. Provider tests

Test every gate combination:

demo true with both keys present;
paid flag false;
API live flag false;
missing Anthropic key;
missing Replicate token;
blank/oversized model;
all gates open;
fixture injection.

Prove:

client is never constructed before gates pass;
API token alone enables nothing;
demo mode cannot instantiate clients;
public availability remains false unless the full pipeline is available;
accepted prediction ID is persisted before polling;
task restart reuses the prediction;
transient polling error does not repeat Anthropic or prediction creation;
pre-acceptance transient create failure may retry safely;
ambiguous create failure never retries;
failed/canceled/aborted mapping;
timeout cancellation;
one output URL required;
URL host and redirect validation;
maximum-byte enforcement;
invalid image bytes rejected;
pixel cap enforced;
raw object staged once;
existing matching staged object resumes;
conflicting object fails;
DesignVersion final image key remains blank;
no prompt or provider URL appears in logs.

Use socket denial in the automated suite. CI must be incapable of reaching
Anthropic or Replicate.

29. End-to-end fake tests

Using real PostgreSQL, Redis/Celery eager mode and private test storage:

Complete a synthetic questionnaire design.
POST generate with one UUID idempotency key.
Observe queued.
Run the task.
Observe running_text.
Persist one DesignVersion.
Persist one deterministic prompt.
Observe running_image.
Stage one verified synthetic image.
Observe succeeded.
Confirm the Design is generated.
Confirm the public job payload contains no private provenance.

Add failure-path equivalents.

Add a worker-restart simulation:

persist prediction ID;
interrupt before polling completes;
invoke the task again;
confirm no second text or create call;
finish the original prediction.
30. OpenAPI and frontend regression

Regenerate and commit the changed API contracts.

Tests must prove:

generated runtime client remains GET-only;
generation POST uses the explicit CSRF wrapper;
idempotency header is present;
no UI route was added;
no browser storage contains job IDs, prompts or credentials.
31. Documentation

Create:

docs/decisions/0011-asynchronous-generation-pipeline.md

Record:

GenerationAttempt now begins before DesignVersion;
per-Design idempotency;
one in-progress job database constraint;
Design lifecycle states;
Celery generation queue;
deterministic task IDs;
attempt-level advisory locking;
resumable stage markers;
atomic attempt↔DesignVersion linkage;
Replicate async prediction creation and polling;
best-effort prediction-creation boundary;
no whole-pipeline automatic retries;
seed reuse;
raw private staging;
provider output expiry;
Phase 11 owns final image ingest;
Phase 12 owns progress/results UI;
Phase 15 owns demo generation;
Phase 16 owns rate limits and cost ceilings;
live generation must not be publicly enabled before Phase 16 safeguards.

Update:

README.md;
docs/phases/PHASES.md;
docs/PROPOSAL.md;
.env.example;
compose.yaml;
CLAUDE.md only for permanent operational rules.

Correct obsolete ALLOW_PROVIDER_CALLS wording where encountered in current
generation documentation, without rewriting historical experiment evidence.

Do not activate questionnaire v2 as part of documentation or setup.

32. Validation

Dependency:

docker compose build api
docker compose up -d
docker compose exec api python -m pip check

Database/backend:

docker compose exec api python manage.py check
docker compose exec api python manage.py makemigrations --check --dry-run
docker compose exec api python manage.py migrate
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .

DesignSpec and prompt snapshots:

docker compose exec api python manage.py export_design_spec_schema
git diff --exit-code -- apps/api/sitara/generation/schemas/design_spec_v1.json
docker compose exec api pytest \
  sitara/generation/tests/test_prompt_builder.py \
  sitara/generation/tests/test_prompt_snapshots.py

OpenAPI:

docker compose exec api python manage.py spectacular `
  --format openapi-json `
  --file openapi/schema.json `
  --validate `
  --fail-on-warn
git diff --exit-code -- apps/api/openapi/schema.json

Frontend:

docker compose exec web npm run generate:api
git diff --exit-code -- apps/web/src/api/schema.d.ts
docker compose exec web npm run lint
docker compose exec web npm run typecheck
docker compose exec web npm test -- --run
docker compose exec web npm run build

Celery:

docker compose exec api python -c "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"

Confirm the generation task is registered and routed to generation.

Questionnaire lifecycle:

docker compose exec api pytest \
  sitara/questionnaire/tests/test_fixture_versions.py

Phase 2:

Push-Location experiments/model-eval
.venv/Scripts/python -m pytest tests/test_model_decision.py -q
Pop-Location

Integrity:

git status --short
git diff -- experiments/model-eval/outputs/
33. Offline manual checkpoint

Without provider keys:

Keep DEMO_MODE=true.
Keep ALLOW_PAID_AI_CALLS=false.
Keep LIVE_GENERATION_ENABLED=false.
Run the fixture command on a complete synthetic Design.
Confirm queued → running_text → running_image → succeeded.
Confirm exactly one DesignVersion.
Confirm the prompt remains version 3.0.0.
Confirm one private staged WebP exists.
Confirm DesignVersion.image_storage_key remains blank.
Repeat with the same idempotency key and confirm no duplicate.
Simulate task restart after prediction acceptance.
Confirm zero network calls.
34. Paid live checkpoint — do not execute automatically

Leave pending unless the repository owner separately authorises spend.

Before execution, verify the current official model schema, price, terms and
output-retention policy again.

Use only a private local environment.

Set:

DEMO_MODE=false
LIVE_GENERATION_ENABLED=true
ALLOW_PAID_AI_CALLS=true
ANTHROPIC_API_KEY=<local-untracked-secret>
REPLICATE_API_TOKEN=<local-untracked-secret>
DEFAULT_IMAGE_MODEL=black-forest-labs/flux-1.1-pro

Recreate API and worker containers.

Use one complete synthetic Design and one idempotency key.

Call through the same-origin API:

POST /api/v1/designs/<uuid>/generate/
Idempotency-Key: <uuid>

Poll:

GET /api/v1/jobs/<job-uuid>/

Verify:

no more than two Anthropic requests;
exactly one accepted Replicate prediction;
one DesignVersion;
one image prompt;
one staged image;
seed and model provenance private;
public job payload contains no prompt/provider/storage details;
duplicate POST with the same key creates no work;
image visually follows garment, coverage, drape and embellishment selections.

Worker-restart checkpoint:

Start a second synthetic Design.
Wait until the Replicate prediction ID is persisted.
Stop the worker.
Restart it.
Confirm the same prediction ID is polled.
Confirm Anthropic and Replicate-create call counts do not increase.
Confirm final success or a controlled terminal failure.

Afterwards:

LIVE_GENERATION_ENABLED=false
ALLOW_PAID_AI_CALLS=false
DEMO_MODE=true

Do not commit:

API keys;
real prompts/specifications;
provider prediction IDs;
generated images;
storage keys;
billing data;
user answers.

Record only safe aggregate observations in the ADR.

35. Integrity requirements

Confirm:

zero live calls during implementation and CI;
no provider key committed;
no raw provider error persisted;
no prompt exposed through APIs;
no final signed image URL;
no final DesignVersion image key;
no inspiration metadata sent to either provider;
questionnaire v1 fingerprint unchanged;
questionnaire v2 remains draft;
prompt snapshots unchanged unless deliberately reviewed;
no Phase 2 evidence modified;
no Docker volumes deleted;
hosted CI green after push.
Part B commit

Commit Part B as:

feat(generation): add gated Replicate image rendering

Do not amend Part A or rewrite previous history.

Do not push unless explicitly requested.

Return

Return only:

Part A full SHA;
Part B full SHA;
migrations and backfill;
Design status lifecycle;
GenerationAttempt fields and constraints;
idempotency and concurrency behaviour;
API response shapes and stable errors;
Celery routing and task settings;
task resume state machine;
attempt↔DesignVersion atomic linkage;
Replicate dependency and lock changes;
live capability gates;
exact image input profile;
prediction submission/polling behaviour;
seed persistence;
output-download protections;
private staging behaviour;
fake end-to-end tests;
backend results;
frontend results;
schema/OpenAPI/type drift;
Celery and questionnaire lifecycle results;
Phase 2 integrity result;
zero-live-call confirmation;
offline checkpoint;
paid checkpoint status;
unresolved issues;
hosted CI status.