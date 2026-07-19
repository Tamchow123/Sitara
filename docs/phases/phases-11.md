# Sitara Phase 11 — Permanent private image storage and signed delivery

Starting commit:

2dced04fdfb65459405efa7d75c2c8fe71c69191

Read before editing:

- @CLAUDE.md
- @docs/PROPOSAL.md
- @docs/phases/PHASES.md
- @docs/phases/phases-10.md
- @docs/decisions/0004-private-design-ownership.md
- @docs/decisions/0006-rights-controlled-inspiration-catalogue.md
- @docs/decisions/0009-structured-design-spec-generation.md
- @docs/decisions/0010-deterministic-image-prompt-builder.md
- @docs/decisions/0011-asynchronous-generation-pipeline.md
- @apps/api/config/settings.py
- @apps/api/sitara/designs/models.py
- @apps/api/sitara/designs/views.py
- @apps/api/sitara/designs/ownership.py
- @apps/api/sitara/generation/pipeline.py
- @apps/api/sitara/generation/image_download.py
- @apps/api/sitara/catalogue/
- @compose.yaml

Implement as two focused commits:

1. `feat(storage): add canonical private design image ingest`
2. `feat(storage): add signed design image delivery`

Do not combine them.

Part A must pass before beginning Part B.

Make zero live Anthropic and Replicate calls during implementation, automated
tests and manual fixture checks.

Do not implement:

- frontend progress or results pages;
- a CDN;
- a backend image proxy;
- public generated-image URLs;
- image sharing;
- retention or deletion jobs;
- showcase/gallery behaviour;
- inspiration influence;
- reference-image conditioning;
- refinement;
- demo fixture matching;
- rate limits or cost ceilings;
- deployment configuration beyond local storage settings.

Phase 12 owns the results UI. Phase 16 owns retention and cleanup.

---

# Baseline

Run:

```powershell
git status --short
git log -12 --oneline
docker compose config
docker compose up -d
docker compose ps

Run all existing backend, frontend, OpenAPI, prompt-snapshot, Celery,
questionnaire-lifecycle and Phase 2 integrity checks before editing.

Confirm:

questionnaire v1 fingerprint is unchanged;
questionnaire v2 remains draft;
prompt-builder version remains 3.0.0;
Phase 10 fixture generation succeeds;
DesignVersion.image_storage_key remains blank before Phase 11 ingest;
no live provider request is made.

Do not run:

docker compose down --volumes
Part A — Canonical private image ingest
1. Final-storage architecture

Keep Phase 10 raw provider output in its existing private staging location:

generation-staging/<attempt-uuid>/raw.<verified-extension>

Add a distinct Django storage alias:

design_images

All permanent generated-image operations must use:

from django.core.files.storage import storages

store = storages["design_images"]

Do not use a module-level storage instance, because tests and environment
overrides must take effect correctly.

Support these configured final-storage backends:

s3
filesystem

Purpose:

s3: production and local MinIO-compatible storage;
filesystem: offline development and deterministic ingest testing only.

The filesystem backend must not expose a public MEDIA_URL or bare file path.

Add a strict setting:

DESIGN_IMAGE_STORAGE_BACKEND=s3

Accepted values are exactly:

s3
filesystem

Unknown, blank or differently-cased values must refuse startup without echoing
the supplied value.

Suggested modules:

apps/api/sitara/media/
  __init__.py
  backends.py
  image_processing.py
  ingest.py
  delivery.py
  exceptions.py

A Django app is unnecessary unless genuinely needed.

Do not create a generic storage framework or repository abstraction.

2. Private backend configuration
S3-compatible backend

Use the existing S3-compatible configuration:

S3_ENDPOINT_URL
S3_ACCESS_KEY_ID
S3_SECRET_ACCESS_KEY
S3_BUCKET_NAME
S3_REGION_NAME

Requirements:

private bucket;
default_acl=None;
query-string authentication enabled;
file_overwrite=False;
SigV4;
deterministic key names;
no public-read ACL;
no credentials or endpoints logged.
Filesystem backend

Add:

DESIGN_IMAGE_FILESYSTEM_ROOT

Use a private directory outside static files and outside any publicly served
media root.

Requirements:

no public base URL;
deterministic keys;
no overwrite;
directory/file permission settings where the platform supports them;
no file:// URL returned through the API.

The filesystem backend is valid for ingest and verification. Because Phase 11
does not implement a backend image proxy, signed browser delivery must fail
closed for this backend.

3. Deterministic permanent layout

Use only server-owned UUIDs in paths:

design-images/<design-uuid>/<design-version-uuid>/original.webp
design-images/<design-uuid>/<design-version-uuid>/thumbnail.webp

Requirements:

no user ID;
no email address;
no session identifier;
no title;
no questionnaire answer;
no prompt fragment;
no provider prediction ID;
no client-controlled filename;
no path traversal;
fixed lowercase .webp extension.

Provide one pure key-building function and test its exact output.

4. Processing settings

Add strictly validated settings:

DESIGN_IMAGE_MAX_EDGE=2048
DESIGN_IMAGE_THUMBNAIL_EDGE=512
DESIGN_IMAGE_WEBP_QUALITY=90
DESIGN_IMAGE_THUMBNAIL_QUALITY=82
DESIGN_IMAGE_SIGNED_URL_TTL_SECONDS=300

Validation:

all are strict positive integers;
WebP qualities must be between 1 and 100;
thumbnail edge must not exceed full-image max edge;
signed URL TTL must be between 30 and 3600 seconds;
errors identify only the setting, never its value.

Do not add another image-processing dependency. Use the existing pinned Pillow.

5. Extend DesignVersion image provenance

Retain:

image_storage_key

Add:

image_sha256
image_size_bytes
image_width
image_height

thumbnail_storage_key
thumbnail_sha256
thumbnail_size_bytes
thumbnail_width
thumbnail_height

image_ingested_at

Suggested field types:

keys: CharField(max_length=255, blank=True);
hashes: CharField(max_length=64, blank=True);
byte sizes: nullable positive big integers;
dimensions: nullable positive integers;
timestamp: nullable datetime.

Do not store:

signed URLs;
S3 credentials;
source staging bytes;
provider output URL;
MIME headers;
EXIF;
prompts;
user answers.

Database constraints:

Every permanent-image field is absent, or every permanent-image field is
present.
Original and thumbnail hashes are blank or exactly 64 lowercase hexadecimal
characters.
Byte sizes and dimensions are positive when present.
Original and thumbnail keys must differ when populated.
Permanent image metadata requires:
a DesignSpec;
an image prompt;
a prompt-builder version.
Existing Phase 10 rows with all permanent-image fields absent remain valid.

Do not require old Phase 10 succeeded attempts to have final metadata through
a migration. Storage I/O must never occur inside a schema migration.

Make every final-image provenance field read-only in Django admin.

6. Canonical image processing

Create a pure function such as:

process_design_image(
    raw_bytes: bytes,
    *,
    max_edge: int,
    thumbnail_edge: int,
    full_quality: int,
    thumbnail_quality: int,
) -> ProcessedDesignImage

Return structured data containing:

original_bytes
original_sha256
original_width
original_height

thumbnail_bytes
thumbnail_sha256
thumbnail_width
thumbnail_height

Requirements:

Enforce Phase 10 byte and pixel bounds again.
Decode with Pillow and force a complete load.
Reject:
truncated files;
unidentified formats;
zero-sized images;
decompression bombs;
animated or multi-frame input;
unsupported colour modes that cannot be safely converted.
Apply EXIF orientation before rendering.
Strip:
EXIF;
GPS;
comments;
XMP;
ICC profile;
provider metadata;
animation.
Produce a single-frame RGB image.
For alpha-bearing input, composite predictably onto a documented neutral
background before RGB conversion.
Preserve aspect ratio.
Downsize only when the image exceeds DESIGN_IMAGE_MAX_EDGE; never upscale.
Create a thumbnail constrained within a square of
DESIGN_IMAGE_THUMBNAIL_EDGE; never distort or crop.
Use a high-quality deterministic resampling filter.
Encode both outputs as WebP using explicit quality/method parameters.
Reopen and verify the encoded WebP outputs.
Confirm the stored dimensions match the encoded files.
Calculate hashes from the final encoded bytes.
Identical input under the pinned Pillow version must produce identical
output bytes.

Do not retain the provider’s original metadata or original container format.

7. Ingest service

Create a focused service:

ingest_staged_design_image(
    attempt,
    *,
    staging_storage=None,
    final_storage=None,
) -> DesignVersion

Preconditions:

attempt belongs to a Design;
attempt has a linked DesignVersion;
attempt has all five Phase 10 staged-image fields;
staged object exists;
staged bytes match the attempt’s recorded SHA-256;
staged dimensions and size match verified bytes;
version belongs to the same Design;
version has a valid DesignSpec and immutable image prompt.

The service must:

Read the staged object with a strict byte limit.
Revalidate its real format, dimensions and hash.
Process the canonical original and thumbnail.
Write both to deterministic final keys.
Verify stored objects after writing.
Persist the full permanent-image provenance atomically on the locked
DesignVersion.
Return the refreshed DesignVersion.

Do not hold a database transaction while:

reading staging storage;
processing image bytes;
writing final storage;
verifying final objects.

Use a short final transaction.atomic() block and lock:

the GenerationAttempt;
the DesignVersion.

Under the lock, re-check:

the attempt/version relationship;
staged metadata;
existing permanent metadata.
8. Crash-safe object/DB boundary

Object storage and PostgreSQL cannot commit atomically. Handle this through
deterministic keys and verification.

Rules:

if neither final object exists, write both;
if a matching object already exists, reuse it;
if an existing object’s bytes differ, fail safely;
if storage renames the requested key, delete the unexpected object
best-effort and fail;
if one object exists and the other does not, verify the existing object and
write only the missing one;
if both objects exist but DB metadata was not committed, verify and recover
the metadata;
if DB metadata exists, verify both objects still exist and match before
treating ingest as complete;
never overwrite conflicting content;
never create suffix-renamed duplicates;
never regenerate or call a provider during recovery.

Use generic storage exceptions such as:

DesignImageIngestRetry
DesignImageIngestFailed
DesignImageImmutable

No exception may include:

storage keys;
image hashes;
image bytes;
prompt text;
answers;
provider URLs;
credentials.
9. Immutability

Once permanent image provenance exists:

an identical rerun is idempotent;
a different original, thumbnail, hash, key, processor setting or encoded
output must not overwrite it;
a future image-processor version must create a new DesignVersion rather than
rewriting an existing generated version.

Add:

DESIGN_IMAGE_PROCESSOR_VERSION = "1.0.0"

Persist:

image_processor_version

Include it in the permanent-image all-or-none constraint.

Add a deterministic processor fixture manifest or golden hashes tied to the
processor version.

A changed processed output with an unchanged processor version must fail the
regeneration guard. A deliberate version bump permits reviewed golden updates.

10. Integrate with the Phase 10 pipeline

Change the state machine to:

A  claim/pre-check
B  DesignSpec
C  prompt
D  Replicate submission/poll/download/raw staging
E  canonical permanent ingest
F  success

Requirements:

the attempt is not marked succeeded until permanent original and thumbnail
have been stored and verified;
the Design is not marked generated until permanent ingest succeeds;
a redelivery with complete permanent metadata verifies final objects and
finalises without:
Anthropic;
prompt rebuilding;
Replicate prediction creation;
prediction polling;
output download;
image reprocessing;
a redelivery after objects were written but before DB commit recovers metadata;
a storage interruption after raw staging must never create another provider
request;
no transaction or row lock is held during image processing/storage I/O.

Preserve:

text submission markers;
image submission markers;
prediction ID reuse;
seed reuse;
raw staging recovery;
attempt advisory locking;
bounded task retries.
11. Storage failure classification

Add stable error codes:

image_ingest_unverified
image_ingest_failed

Use:

image_ingest_unverified for transient or ambiguous storage availability;
image_ingest_failed for confirmed corrupt/conflicting/invalid permanent
content.

These failures occur after paid output may already exist. Therefore:

they must never cause automatic image resubmission;
they must not be treated as permission to start another paid prediction;
task retry may rerun only storage verification/ingest;
retry exhaustion preserves:
DesignVersion;
image prompt;
prediction ID;
seed;
staged image metadata;
any matching permanent object already written.

Update enqueue safeguards so a new idempotency key cannot create another paid
image while recoverable staged/final output exists.

Provide an operator-safe method to retry only permanent ingest, either:

python manage.py ingest_design_image --attempt <uuid>

or a similarly narrow command.

The command must:

use the same ingest service;
make zero provider calls;
refuse attempts without staged data;
refuse a mismatched DesignVersion;
report UUIDs, processor version and dimensions;
print no keys, hashes, prompts or answers;
be idempotent.
12. Staging retention

Do not delete Phase 10 staging objects in Phase 11.

Reason:

staged metadata is part of Phase 10 crash recovery;
permanent-storage and database commits are not atomic;
retention cleanup belongs to Phase 16.

Document clearly that staging objects remain private and are later purge
candidates.

Do not clear staged metadata after ingest.

13. Part A tests

Test at least:

Processing
PNG, JPEG and WebP inputs all become WebP;
orientation is applied;
EXIF/GPS/comments/ICC/XMP are absent;
animated WebP/GIF is rejected;
truncated and invalid bytes are rejected;
pixel cap and byte cap;
no upscaling;
aspect ratio preserved;
alpha handling is deterministic;
original max edge;
thumbnail max edge;
deterministic repeated bytes and hashes;
processor-version golden guard.
Models and migration
legacy rows with blank image metadata remain valid;
complete metadata succeeds;
every partial combination fails;
hash format constraints;
positive dimensions and sizes;
original/thumbnail keys differ;
image requires spec and prompt;
admin fields are read-only.
Ingest and recovery
normal ingest writes two objects;
stored objects are WebP;
final metadata matches encoded bytes;
second ingest is idempotent;
existing matching original is reused;
existing matching thumbnail is reused;
one missing object is created;
conflicting object fails without overwrite;
backend key-renaming fails;
object-written/DB-not-committed recovery;
DB-metadata/object-missing fails;
staging hash mismatch fails;
staged dimension mismatch fails;
attempt/version ownership mismatch fails;
transient storage failure is retryable;
no key/hash/prompt leaks through logs or exceptions.
Pipeline
success occurs only after final ingest;
final ingest failure leaves Design generation_failed;
retry after storage interruption performs no provider calls;
permanent metadata present skips every provider stage;
exactly one DesignVersion remains;
DesignVersion.image_storage_key is populated only by ingest;
original staging metadata remains present.

Commit Part A as:

feat(storage): add canonical private design image ingest
Part B — Signed private image delivery
14. S3 signing endpoint configuration

The API container uses an internal S3/MinIO endpoint for storage operations.
A browser may require a different externally reachable host.

Add:

S3_SIGNED_URL_ENDPOINT_URL

Development Compose default:

http://localhost:9000

Production requirements:

blank means use the normal regional S3 endpoint;
a configured endpoint must be an absolute HTTP/HTTPS origin;
production requires HTTPS;
no username/password component;
no query string;
no fragment;
no path other than /;
rejected values are not echoed.

Do not expose this setting through /api/v1/config/public.

The signing endpoint must not be used for ordinary object upload/read calls.

15. Signed URL service

Create:

issue_design_image_urls(
    design_version,
    *,
    ttl_seconds=None,
    signer=None,
    storage=None,
    now=None,
) -> DesignImageUrls

Return:

original_url
thumbnail_url
expires_at

Requirements:

Require complete permanent-image provenance.
Confirm both private objects exist before signing.
Use a dedicated S3-compatible signing adapter.
Use SigV4.
Sign GET only.
Use the configured TTL.
Set response content type to image/webp.
Use an inline safe filename that contains no user input.
Do not persist URLs.
Do not cache URLs.
Do not log URLs or query strings.
Do not return keys or hashes.
The two URLs must expire at the same declared timestamp.

Use public boto3/django-storages interfaces only. Inspect the pinned versions’
actual method signatures before implementing.

For DESIGN_IMAGE_STORAGE_BACKEND=filesystem:

never return a filesystem path;
never return a permanent public media URL;
raise a safe DesignImageDeliveryUnavailable;
do not implement a Django streaming/proxy endpoint in this phase.
16. Direct-URL privacy model

Document the important limitation:

ownership is checked before the URL is issued;
after issuance, the signed URL is a temporary bearer URL;
anyone possessing it may use it until expiry;
logout, session rotation or account switching does not revoke an already
issued S3 URL;
URLs must therefore be short-lived and never logged or stored;
a future authenticated backend proxy is the upgrade path when immediate
revocation or stricter delivery controls are required.

Do not describe a signed URL as permanently private or non-shareable.

17. Image delivery API

Add:

GET /api/v1/designs/<design-uuid>/versions/<version-uuid>/images/

Response:

{
  "images": {
    "original": {
      "url": "short-lived-signed-url",
      "width": 1536,
      "height": 2048
    },
    "thumbnail": {
      "url": "short-lived-signed-url",
      "width": 384,
      "height": 512
    },
    "expires_at": "ISO-8601"
  }
}

Requirements:

AllowAny, preserving anonymous private workspaces;
ownership filtering before design UUID lookup;
version must belong to the owned Design;
inaccessible/nonexistent design or version returns indistinguishable 404;
a caller knowing only the DesignVersion UUID gains nothing;
no anonymous workspace/session is created on a failed GET;
Cache-Control: no-store;
Referrer-Policy: no-referrer;
no prompt;
no DesignSpec;
no storage key;
no hash;
no provider/model/prediction ID;
no seed;
no staging metadata;
no user/session identifier.

Responses:

200 signed image URLs
404 not_found
409 design_image_not_ready
503 design_image_delivery_unavailable

A generated Design with only Phase 10 staging and no permanent ingest returns
the controlled 409, not an unhandled error.

Do not add signed URLs to:

design list;
design detail;
public job payload;
public config;
catalogue endpoints.
18. OpenAPI and frontend transport

Document the endpoint accurately in OpenAPI.

Regenerate and commit:

apps/api/openapi/schema.json
apps/web/src/api/schema.d.ts

Add a narrow frontend data wrapper only:

fetchDesignImageUrls(
  designId: string,
  designVersionId: string,
): Promise<DesignImageUrlResult>

Requirements:

same-origin request;
generated OpenAPI types;
timeout handling;
malformed response rejection;
no localStorage, sessionStorage or IndexedDB;
do not cache the signed URLs in module state;
no React page or component;
no polling;
no automatic URL refresh yet.

Phase 12 will own URL refresh while the results page remains open.

19. Signed URL tests

Test:

authorised anonymous owner receives URLs;
authenticated owner receives URLs;
anonymous-to-authenticated workspace promotion retains access;
other browser/session gets 404;
other account gets 404;
nonexistent design/version gets identical 404;
version belonging to another owned design cannot be mixed into the path;
failed GET creates no workspace;
not-ingested version gets 409;
filesystem backend gets controlled 503;
storage outage gets controlled 503;
no-store and no-referrer headers;
response contains no internal provenance;
URL is never persisted;
URL is never logged;
signer receives the exact TTL;
original and thumbnail use image/webp;
signing uses safe fixed filenames;
generated URL contains a bounded expiry;
mocked signing time proves the declared expires_at;
an expired presigned request is rejected in the MinIO integration/manual
checkpoint.

Do not make live cloud requests in tests.

20. MinIO integration

Use the existing Compose MinIO service.

Confirm:

bucket remains private;
original and thumbnail are not retrievable without a signature;
an authorised API request issues browser-reachable MinIO URLs;
both URLs work before expiry;
the same URLs fail after expiry;
changing the session does not revoke an already-issued bearer URL;
after expiry, a newly authorised request issues fresh URLs.

Do not modify catalogue rights or catalogue visibility.

Confirm existing publicly eligible catalogue image behaviour still works.

21. Offline fixture flow

Update the existing Phase 10 fixture command so it exercises the whole
zero-network path:

structured fixture;
prompt builder;
synthetic raw WebP;
Phase 10 private staging;
Phase 11 canonical full WebP;
Phase 11 thumbnail;
successful finalisation.

It should report only:

attempt UUID
DesignVersion UUID
status
processor version
original dimensions
thumbnail dimensions

Do not print:

prompts;
answers;
image keys;
hashes;
signed URLs;
provider metadata.

Repeated execution with the same idempotency key must remain idempotent.

22. Documentation

Create:

docs/decisions/0012-private-design-image-storage.md

Record:

raw staging versus permanent image storage;
deterministic final keys;
original and thumbnail WebP processing;
metadata stripping;
processor versioning and immutability;
crash recovery across object storage and PostgreSQL;
why staging remains until Phase 16;
S3-compatible storage and local filesystem ingest support;
filesystem delivery is deliberately unavailable;
ownership before signing;
signed URLs as temporary bearer URLs;
lack of immediate revocation;
no backend image proxy in Phase 11;
Phase 12 owns results presentation and URL refresh;
Phase 16 owns retention and purge;
generated designs remain private by default.

Update:

README.md;
docs/phases/PHASES.md;
docs/PROPOSAL.md;
.env.example;
compose.yaml;
CLAUDE.md only for genuinely permanent rules.

Correct obsolete storage documentation where necessary without rewriting
historical experiment evidence.

Do not mark the Phase 10 paid checkpoint complete.

23. Part B validation

Configuration and dependencies:

docker compose config
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

Image-processing and storage-focused tests:

docker compose exec api pytest \
  sitara/media/ \
  sitara/generation/tests/ \
  sitara/designs/tests/

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

Confirm:

generation task remains registered;
worker still listens to generation,celery;
fixture pipeline completes through permanent ingest;
no provider client is instantiated.

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
24. Manual checkpoint
Filesystem ingest

Without provider keys:

Set DESIGN_IMAGE_STORAGE_BACKEND=filesystem.
Keep:
DEMO_MODE=true;
LIVE_GENERATION_ENABLED=false;
ALLOW_PAID_AI_CALLS=false.
Run the fixture pipeline.
Confirm:
original WebP exists privately;
thumbnail WebP exists privately;
dimensions are correct;
metadata is stripped;
no public filesystem URL is issued;
image endpoint returns controlled 503;
rerunning ingest is idempotent.
MinIO ingest and delivery
Set DESIGN_IMAGE_STORAGE_BACKEND=s3.
Start MinIO.
Run the fixture pipeline on a new synthetic design.
Confirm the bucket objects are private.
Retrieve signed URLs through the authorised image endpoint.
Open original and thumbnail before expiry.
Confirm unsigned object access fails.
Use a short local TTL and confirm both URLs fail after expiry.
Request fresh URLs and confirm they work.
Test another browser/session receives 404 from the issuing endpoint.
Confirm the already-issued bearer URL remains usable until expiry.
Confirm existing public catalogue behaviour is unchanged.

Record no URLs, keys, hashes or generated image bytes in Git.

25. Paid checkpoint

No paid checkpoint is required for Phase 11.

Do not call Anthropic or Replicate.

The existing Phase 10 paid end-to-end checkpoint remains pending and must not be
run without separate explicit authorisation.

26. Integrity requirements

Confirm:

zero Anthropic calls;
zero Replicate calls;
no provider client construction in tests;
no new image-processing dependency;
no credentials committed;
generated images private by default;
no permanent public image URL;
no filesystem path in an API response;
no signed URL stored or logged;
no final image URL in job/design-list/design-detail payloads;
no backend image proxy;
no CDN;
no staging deletion;
no questionnaire v1 change;
questionnaire v2 remains draft;
prompt snapshots unchanged;
no Phase 2 evidence changes;
no Docker volumes deleted;
hosted CI green after push.
Part B commit

Commit Part B as:

feat(storage): add signed design image delivery

Do not amend Part A or rewrite earlier history.

Do not push unless explicitly requested.

Return

Return only:

Part A full SHA;
Part B full SHA;
storage aliases and backend selection;
migration and DesignVersion provenance fields;
database constraints;
permanent key layout;
image-processing pipeline;
processor version and golden guard;
crash recovery and idempotency;
pipeline integration;
storage failure/retry behaviour;
staging-retention behaviour;
signed URL architecture;
ownership and API response shape;
filesystem delivery behaviour;
MinIO signing endpoint configuration;
backend test results;
frontend test results;
OpenAPI/generated-type drift;
Celery and fixture-pipeline results;
questionnaire lifecycle result;
Phase 2 integrity result;
zero-provider-call confirmation;
filesystem manual checkpoint;
MinIO manual checkpoint;
unresolved issues;
hosted CI status.