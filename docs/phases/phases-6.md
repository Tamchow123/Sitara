Begin Sitara Phase 6: validated OpenAPI contract and generated TypeScript API client.

Repository:

C:\Users\Tamee\source\repos\Sitara

Starting commit:

f8d5e88de7c85abc46761ec3d427f8c9e8adfad3

Work autonomously unless a genuine blocker prevents safe implementation.

This phase must produce TWO separate focused commits:

1. feat(api): add validated OpenAPI contract
2. feat(web): add generated typed API client

Do not combine them.

Do not implement questionnaire answer submission, questionnaire UI, inspiration selection, design-to-asset linking, provider calls, generation jobs or new product endpoints.

Do not make Anthropic or Replicate calls. Do not delete Docker volumes.

# Current foundation

Sitara now has:

- Django 5.2 and Django REST Framework;
- secure Django session authentication and CSRF;
- private anonymous/authenticated design ownership;
- versioned questionnaire taxonomy;
- staff-managed rights-controlled inspiration catalogue;
- private S3-compatible object storage;
- a Next.js 15 frontend using a same-origin API rewrite;
- existing manually written frontend wire types;
- green backend and frontend CI.

Phase 6 must establish one committed OpenAPI contract as the source for generated frontend API types.

# Initial inspection

Run:

git status --short
git log -7 --oneline
docker compose ps

Read at minimum:

apps/api/config/settings.py
apps/api/config/urls.py
apps/api/sitara/accounts/
apps/api/sitara/health/
apps/api/sitara/designs/
apps/api/sitara/questionnaire/
apps/api/sitara/catalogue/
apps/api/requirements.in
apps/web/package.json
apps/web/src/lib/api.ts
apps/web/src/lib/auth.tsx
.github/workflows/ci.yml
docs/PHASES.md
README.md

Run the existing backend, frontend and Phase 2 suites before editing.

Do not modify or stage:

experiments/model-eval/outputs/

# PART A — Backend OpenAPI contract

## A1. Add drf-spectacular

Add one exact compatible drf-spectacular pin to:

apps/api/requirements.in

Regenerate requirements.txt using the existing pinned toolchain:

Python 3.12.7
pip 26.0.1
pip-tools 7.5.3

Requirements:

- retain --generate-hashes;
- retain hash-verified installation;
- no unrelated dependency upgrades;
- deterministic second regeneration;
- python -m pip check passes.

Configure DRF:

REST_FRAMEWORK["DEFAULT_SCHEMA_CLASS"] =
    "drf_spectacular.openapi.AutoSchema"

Add a focused SPECTACULAR_SETTINGS configuration including:

- TITLE: Sitara API;
- VERSION matching the current API contract, such as 1.0.0;
- DESCRIPTION explaining concept visualisation, private-by-default designs
  and demo/provider restrictions;
- OAS_VERSION: 3.0.3;
- COMPONENT_SPLIT_REQUEST: true;
- SERVE_INCLUDE_SCHEMA: false;
- deterministic operation ordering.

Do not add Swagger UI, Redoc or a public runtime schema endpoint in this phase.

The schema is generated through the management command only.

## A2. Commit a canonical schema

Create:

apps/api/openapi/schema.json

Generate it through:

python manage.py spectacular \
  --format openapi-json \
  --file openapi/schema.json \
  --validate \
  --fail-on-warn

The generated file must be deterministic and committed.

It must contain no:

- timestamps generated at build time;
- machine-specific paths;
- storage endpoints;
- bucket names;
- credentials;
- session keys;
- provider secrets;
- internal staff emails;
- fixture-only poison values.

## A3. Include every existing API operation

The schema must include these canonical operations:

GET  /api/v1/health/live
GET  /api/v1/health/ready
GET  /api/v1/config/public

GET  /api/v1/auth/csrf/
POST /api/v1/auth/register/
POST /api/v1/auth/login/
POST /api/v1/auth/logout/
GET  /api/v1/auth/me/

GET   /api/v1/designs/
POST  /api/v1/designs/
GET   /api/v1/designs/{design_id}/
PATCH /api/v1/designs/{design_id}/

GET /api/v1/questionnaire/active/

GET /api/v1/inspiration-assets/
GET /api/v1/inspiration-assets/{asset_id}/image/
GET /api/v1/inspiration-assets/{asset_id}/thumbnail/

Do not document Django admin.

Do not add new product endpoints merely to improve the schema.

## A4. Make auth endpoints visible without weakening CSRF

The auth endpoints are currently plain Django JSON views and may not be
discoverable by DRF schema generation.

Make the smallest safe adjustment needed to expose them to
drf-spectacular.

Preferred approach:

- retain the existing function bodies and response helpers;
- wrap them as DRF function-based views using @api_view;
- explicitly apply AllowAny;
- use SessionAuthentication where authenticated request.user state is needed;
- preserve explicit Django @csrf_protect on register, login and logout;
- preserve @ensure_csrf_cookie and database-session materialisation on CSRF
  bootstrap;
- preserve generic credential errors, session rotation, rate limits and
  no-store responses.

Follow the supported decorator order for DRF policy decorators and
@extend_schema.

Do not use csrf_exempt.

Do not convert authentication to JWT or token authentication.

All existing authentication tests must remain unchanged or become stricter.

## A5. Define explicit schema serializers

Create small schema serializers where runtime serializers do not already
describe the response accurately.

Use a sensible location such as:

sitara/<app>/openapi.py
or existing serializers.py modules

Avoid one giant cross-application schema module.

Represent at minimum:

- stable error envelope;
- field-validation errors;
- CSRF response;
- auth requests and responses;
- authenticated/anonymous me response;
- health and readiness responses;
- public configuration;
- design list wrapper;
- design create/update request;
- design response;
- questionnaire version and schema structures;
- public inspiration asset;
- catalogue list wrapper.

Password fields must be write-only.

Do not expose internal model serializers wholesale.

## A6. Questionnaire schema typing

The active questionnaire response must not be typed merely as arbitrary
`object`.

Define nested output serializers for the stable schema structure:

- questionnaire schema;
- steps;
- questions;
- options;
- constraints;
- compatibility rules;
- rule conditions;
- rule actions.

It is acceptable for the question-type-specific `constraints` field to
remain a bounded JSON mapping when representing a perfect discriminated
union would create excessive complexity.

The generated TypeScript must still know:

- supported question types;
- question identifiers;
- labels/help text;
- required flag;
- option shape;
- rule operator;
- rule action;
- condition and target values.

Do not duplicate the actual questionnaire options from the fixture in
schema code.

## A7. Explicit operation annotations

Give every operation:

- a stable explicit operation_id;
- a relevant tag;
- request type;
- successful response type;
- documented error status codes;
- concise description.

Use stable tags such as:

Health
Configuration
Authentication
Designs
Questionnaire
Inspiration catalogue

Document relevant responses:

- 400 validation failure;
- 401 invalid credentials;
- 403 CSRF failure;
- 404 ownership/ineligible-resource response;
- 429 authentication rate limit;
- 503 dependency, questionnaire, workspace or catalogue unavailable.

Do not claim an endpoint returns a status that runtime tests disprove.

## A8. CSRF and session documentation

For unsafe browser operations, document the X-CSRFToken header.

Document that:

1. the client first calls GET /api/v1/auth/csrf/;
2. Django sets sitara_csrftoken and sitara_sessionid;
3. the returned token is sent through X-CSRFToken;
4. authentication uses an HttpOnly session cookie;
5. the browser must use same-origin credentials.

Do not model a bearer token or API key.

Public identity-free GET endpoints should not claim authentication is
required.

Design endpoints must document that they support either:

- anonymous Django-session ownership; or
- authenticated account ownership.

Frontend middleware must not be represented as an authorization layer.

## A9. Binary image endpoints

Document the two catalogue image endpoints as:

Content-Type: image/webp
binary response body

Document:

- 200 WebP response;
- 404 for missing or ineligible assets;
- 503 for eligible assets whose private storage object is unavailable.

Do not expose storage keys or storage URLs in the schema.

## A10. Canonical paths

Existing slash-optional regex routes must appear only once in the schema,
using the documented canonical paths with trailing slashes.

Do not change runtime support for both slash spellings.

When optional-regex syntax produces duplicate or malformed schema paths,
add a narrowly scoped drf-spectacular preprocessing hook that:

- normalises only the terminal optional slash;
- preserves path parameters;
- does not affect runtime routing;
- does not silently remove unrelated endpoints.

Add tests for the exact canonical path set.

## A11. Backend contract tests

Add tests proving:

1. schema generation completes with no warnings;
2. OpenAPI validation succeeds;
3. operation IDs are unique;
4. every expected endpoint and method exists;
5. no unexpected admin endpoint appears;
6. canonical paths contain no regex fragments or optional-slash syntax;
7. CSRF header is documented on unsafe browser endpoints;
8. password fields are write-only;
9. image endpoints expose image/webp binary responses;
10. questionnaire schema is structurally typed;
11. public endpoints are not documented as requiring a bearer token;
12. no JWT security scheme exists;
13. private fields are absent, including:
    - password hashes;
    - staff/superuser flags;
    - session keys;
    - DesignSession IDs;
    - storage keys;
    - image SHA;
    - rights evidence;
    - internal rights notes;
    - provider credentials;
14. the committed schema is byte-deterministic;
15. all existing application tests still pass.

## A12. Backend CI drift check

Update the backend CI job to run:

python manage.py spectacular \
  --format openapi-json \
  --file openapi/schema.json \
  --validate \
  --fail-on-warn

git diff --exit-code -- openapi/schema.json

Place this after dependency installation and before or alongside the test
suite.

A stale schema must fail CI.

## A13. Validate and commit Part A

Run:

docker compose build api
docker compose up -d
docker compose ps

docker compose exec api python manage.py check
docker compose exec api python manage.py makemigrations --check --dry-run
docker compose exec api python -m pip check
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .

Generate the host-side canonical schema using either the local Python
environment or a documented Docker copy/redirection workflow.

Run generation twice and prove:

git diff --exit-code -- apps/api/openapi/schema.json

Commit Part A as:

feat(api): add validated OpenAPI contract

Do not begin Part B until Part A is clean.

# PART B — Generated TypeScript client

## B1. Add frontend dependencies

Add exact versions of:

Runtime dependency:
- openapi-fetch

Development dependency:
- openapi-typescript

Update package-lock.json through npm.

Do not add Axios, React Query, Redux or another state library.

## B2. Generate TypeScript types

Create:

apps/web/src/api/schema.d.ts

Add:

npm run generate:api

The command must generate from:

../api/openapi/schema.json

Use openapi-typescript directly.

Do not post-process the generated file manually.

The generated file must carry a clear generated-file header and must not
be formatted by hand.

## B3. Create the typed client wrapper

Create:

apps/web/src/api/client.ts

Use:

createClient<paths>()

from openapi-fetch.

Requirements:

- base URL remains same-origin/relative;
- no NEXT_PUBLIC backend URL;
- credentials remain "same-origin";
- cache remains "no-store";
- requests retain the five-second timeout;
- malformed or unavailable responses remain controlled;
- the wrapper must not store credentials, cookies or CSRF tokens.

Extract the existing timeout/same-origin fetch transport into a small
shared module where useful so api.ts and the generated client do not
implement competing request policies.

Do not remove the existing tested CSRF-aware unsafe-request flow.

The typed client may initially be used for safe GET operations while
registration, login, logout and design mutations continue through the
existing CSRF-aware wrapper.

Do not create an unsafe typed client that silently omits CSRF.

## B4. Remove duplicated wire interfaces

Replace manually written API wire-response interfaces with aliases derived
from the generated schema wherever practical.

Examples include:

- ReadyResponse;
- ReadyChecks;
- PublicConfig;
- AuthUser;
- MeResponse.

Client-only result types such as:

{ ok: true } | { ok: false; ... }

may remain handwritten because they represent frontend behaviour rather
than server wire contracts.

Do not rewrite the entire frontend unnecessarily.

## B5. Compile-time and runtime tests

Add tests proving:

1. the generated `paths` type imports successfully;
2. known paths compile;
3. a nonexistent path fails with @ts-expect-error;
4. questionnaire response types expose steps/questions/rules;
5. catalogue response exposes only public fields;
6. storage keys and rights evidence are not available in generated public
   response types;
7. the typed client uses relative URLs;
8. credentials are same-origin;
9. cache is no-store;
10. timeout/abort still works;
11. existing CSRF bootstrap and retry-once tests remain green;
12. existing auth and status-page tests remain green.

Do not test generated file contents with huge snapshots.

## B6. Frontend CI drift check

After npm ci, add:

npm run generate:api
git diff --exit-code -- src/api/schema.d.ts

Then run:

npm run lint
npm run typecheck
npm test
npm run build

A stale generated TypeScript contract must fail CI.

## B7. Local generation documentation

Document a reliable Windows/Docker workflow in README.md.

The workflow must show:

1. generating apps/api/openapi/schema.json;
2. validating it with --fail-on-warn;
3. running npm --prefix apps/web run generate:api;
4. reviewing both generated diffs;
5. running backend and frontend checks.

Avoid commands that rely on Unix-only shell syntax without also providing
a PowerShell-compatible form.

## B8. Documentation

Create:

docs/decisions/0007-openapi-generated-client.md

Record:

- backend OpenAPI is authoritative;
- schema is committed;
- TypeScript types are generated, never manually edited;
- CI fails on backend schema drift;
- CI fails on frontend type drift;
- why drf-spectacular was selected;
- why openapi-typescript/openapi-fetch were selected;
- session-cookie and CSRF behaviour;
- same-origin transport;
- binary image typing;
- no Swagger UI/public schema endpoint yet;
- unsafe typed mutations remain deferred until the shared CSRF middleware
  is integrated carefully;
- no endpoint behaviour changed for documentation convenience.

Update:

README.md
docs/PHASES.md
docs/PROPOSAL.md

Mark Phase 6 delivered only after both commits and hosted CI pass.

## B9. Full validation

Backend:

docker compose exec api python manage.py check
docker compose exec api python manage.py makemigrations --check --dry-run
docker compose exec api python -m pip check
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .

Frontend:

docker compose exec web npm run lint
docker compose exec web npm run typecheck
docker compose exec web npm test -- --run
docker compose exec web npm run build

Celery:

docker compose exec api python -c "from sitara.health.tasks import ping; print(ping.delay().get(timeout=10))"

Phase 2:

cd experiments\model-eval
.venv\Scripts\python -m pytest tests\test_model_decision.py -q
cd ..\..

Do not run:

docker compose down --volumes

# Manual contract-drift checkpoint

After committing generated files:

1. Add a temporary harmless response field to one schema serializer.
2. Run backend schema generation.
3. Confirm the committed schema drift check fails.
4. Regenerate and confirm schema.json changes.
5. Run npm run generate:api.
6. Confirm schema.d.ts changes.
7. Revert the temporary field.
8. Regenerate both files.
9. Confirm both drift checks pass.

Do not leave the temporary field in the final commit.

# Integrity requirements

Confirm:

- zero provider calls;
- no real secrets in schema or generated types;
- no private storage fields exposed;
- no rights evidence or internal notes exposed;
- no session keys exposed;
- no Docker volumes deleted;
- no Phase 2 evidence changed;
- nothing under experiments/model-eval/outputs/ staged;
- no manually edited generated code;
- backend and frontend hosted CI green.

# Part B commit

Commit Part B as:

feat(web): add generated typed API client

Do not amend Part A or rewrite earlier history.

# Return

Return:

1. Part A commit SHA;
2. Part B commit SHA;
3. dependency additions and lock changes;
4. auth-view schema integration approach;
5. exact canonical schema paths;
6. operation and component counts;
7. OpenAPI warning/validation result;
8. sensitive-field exclusion proof;
9. CSRF/session representation;
10. questionnaire typing;
11. binary image typing;
12. committed schema location;
13. generated TypeScript location;
14. typed client transport behaviour;
15. manually removed wire-type duplication;
16. backend test/lint results;
17. frontend test/lint/typecheck/build results;
18. schema drift proof;
19. generated-type drift proof;
20. Celery result;
21. zero-provider-call confirmation;
22. Phase 2 integrity confirmation;
23. hosted CI result.