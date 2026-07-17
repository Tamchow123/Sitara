# Sitara Phase 7 — Questionnaire vertical slice

Starting commit:

29ed83edcc9cf941919a5d8b65edd3a8984048ba

Read before editing:

- @CLAUDE.md
- @docs/phases/PHASES.md
- @docs/PROPOSAL.md
- @docs/decisions/0004-private-design-ownership.md
- @docs/decisions/0005-versioned-questionnaire-schema.md
- @docs/decisions/0006-rights-controlled-inspiration-catalogue.md
- @docs/decisions/0007-openapi-generated-client.md
- @apps/api/sitara/designs/
- @apps/api/sitara/questionnaire/
- @apps/api/sitara/catalogue/
- @apps/api/openapi/schema.json
- @apps/web/src/api/
- @apps/web/src/lib/api.ts
- @apps/web/src/lib/transport.ts

Implement Phase 7 as two separate commits:

1. `feat(api): persist validated questionnaire drafts`
2. `feat(web): add questionnaire wizard`

Do not combine them. Do not start Part B until Part A passes all backend
checks and OpenAPI drift checks.

Do not implement provider calls, generation, DesignSpec creation, image
generation, results, refinement, user image uploads or deployment work.

---

# Part A — Authoritative backend draft persistence

## 1. Establish the baseline

Run:

```powershell
git status --short
git log -7 --oneline
docker compose config
docker compose up -d
docker compose ps

Run the current backend, frontend, OpenAPI drift and Phase 2 integrity suites
before editing.

Do not modify or stage anything under:

experiments/model-eval/outputs/
2. Link designs to questionnaire versions

Extend Design with:

questionnaire_version

Requirements:

nullable FK to QuestionnaireVersion;
on_delete=PROTECT;
existing Phase 4 designs migrate safely with null;
a design may be linked only to an active or retired questionnaire;
a draft questionnaire can never receive user answers;
the questionnaire version may be assigned once;
it cannot later be changed to another version;
a design linked to a retired version remains editable and resumable;
existing title-only design creation remains backward compatible.

The design detail response must include the linked questionnaire as:

{
  "id": "<uuid>",
  "version": 1,
  "schema": {}
}

Return null for legacy designs with no questionnaire.

Do not include the full questionnaire schema in design list responses.

3. Persist selected inspirations

Create a through model such as:

DesignInspiration

Fields:

UUID primary key;
design FK with CASCADE;
inspiration asset FK with PROTECT;
positive position;
created timestamp.

Database constraints:

unique (design, inspiration_asset);
unique (design, position);
position between 1 and MAX_INSPIRATION_IMAGES, currently 3.

Selections are ordered by position.

Do not copy or snapshot:

storage keys;
image hashes;
rights evidence;
rights notes;
verifier details;
image bytes;
attribution data.

The linked asset and its current rights record remain authoritative.

4. Define the answer format

Persist answers as a JSON object keyed by stable question ID:

{
  "garment_type": "lehenga",
  "colour_palette": ["maroon", "gold"],
  "final_notes": "Keep the overall look elegant and balanced."
}

Allowed value shapes:

single_choice: string;
multi_choice: ordered list of unique strings;
text: string.

Unanswered optional questions are omitted.

Do not persist:

labels;
step titles;
frontend component state;
validation messages;
hidden question values;
arbitrary unknown question IDs.
5. Implement authoritative answer validation

Create a focused pure-Python service such as:

validate_questionnaire_answers(
    schema,
    answers,
    *,
    require_complete: bool,
) -> dict

The returned mapping is normalised and safe to persist.

The validator must be total over arbitrary JSON-compatible input. Malformed
data must always become a controlled domain validation error, never an
incidental TypeError, KeyError, ValueError or traceback.

Validate:

top-level answers is an object;
every key is a known question ID;
value type matches the question type;
selected options exist;
multi-choice values are unique;
declared maximum item counts;
exclusive values such as none;
maximum text lengths;
active compatibility restrictions;
hidden questions are absent.

For text:

normalise CRLF/CR to LF;
trim outer whitespace;
preserve meaningful internal whitespace;
never interpret HTML or Markdown.
Draft mode

With require_complete=False:

validate all supplied values structurally;
enforce option allowlists;
enforce maximum lengths/counts;
enforce exclusivity and active restrictions;
do not require missing required questions;
do not enforce minimum text/item counts yet.

This allows secure partial autosave.

Complete mode

With require_complete=True, additionally enforce:

every visible required question is answered;
minimum item counts;
minimum text lengths.

Return errors keyed by question ID where practical.

6. Define compatibility-rule semantics once

Implement only the existing allowlisted rule language. Do not add a generic
rules engine.

A condition with no current answer evaluates false.

Treat a scalar answer as one selected value and a multi-choice answer as a set
of selected values:

equals: selected values exactly equal the condition values;
in: at least one selected value occurs in the condition values;
not_in: an answer exists and none of its selected values occurs in the
condition values.

Visibility:

questions targeted by at least one show rule are hidden by default;
other questions are visible by default;
a matching show makes the target visible;
a matching hide hides the target;
hide wins if matching show and hide rules conflict.

Requirements:

base required applies only while the question is visible;
matching require rules make a visible question required.

Restrictions:

matching restrict_options rules intersect their allowed value sets;
with no matching restriction, all declared options remain allowed;
an empty resulting allowed set is a controlled schema/validation failure.

The frontend must implement the same semantics from schema data. Individual
fixture rules must never be hard-coded in either language.

7. Add shared cross-language validation cases

Create a small deterministic JSON contract fixture, for example:

contracts/questionnaire-validation-cases.json

Both Python and TypeScript tests must consume it.

Include cases for:

valid partial answers;
valid complete answers;
missing required answer;
hidden answer supplied;
saree versus non-saree draping;
garment-specific silhouette restrictions;
unknown option;
unknown question;
duplicate multi-choice value;
too many colours;
exclusive none combined with another embellishment;
final notes over the maximum;
changed controlling answer invalidating an old dependent answer.

Do not duplicate the full questionnaire fixture.

8. Implement one atomic draft-update service

Create a service such as:

update_design_draft(
    design,
    *,
    questionnaire_version_id=UNSET,
    answers=UNSET,
    inspiration_asset_ids=UNSET,
)

Requirements:

run in transaction.atomic();
lock the Design row;
enforce ownership before calling the service;
assign the questionnaire version only once;
validate answers against the design's linked version;
validate partial answers before persistence;
replace inspiration selections as one ordered set;
reject duplicate IDs;
reject more than settings.MAX_INSPIRATION_IMAGES;
accept only assets currently returned by
InspirationAsset.objects.publicly_eligible();
reject draft, retired, expired, unverified or incompletely permitted assets;
preserve the submitted selection order;
roll back answers and selections together on any failure.

Never perform a partial update where answers save but inspirations fail.

Concurrent updates must never create duplicate positions or more than three
selection rows.

9. Extend the design API

Preserve:

GET/POST /api/v1/designs/
GET/PATCH /api/v1/designs/<uuid>/

Add:

POST /api/v1/designs/<uuid>/validate/

The validate endpoint:

performs no generation;
checks the persisted draft using require_complete=True;
rechecks that every selected inspiration is still eligible;
returns {"valid": true} on success;
returns controlled 400 validation_failed with question/selection errors;
uses ownership-first lookup;
returns indistinguishable 404 for inaccessible designs;
is explicitly CSRF protected;
uses Cache-Control: no-store;
has no request body.
Create and update request fields

Support only:

{
  "title": "My nikah concept",
  "questionnaire_version_id": "<uuid>",
  "answers": {},
  "inspiration_asset_ids": ["<uuid>"]
}

All fields remain optional for partial draft operations.

Reject every unknown or server-owned field, including:

id;
status;
design_session;
user;
questionnaire schema;
created/updated timestamps;
versions;
generation attempts;
storage fields.

Use JSON-only parser behaviour and document only application/json.

Detail response

Return:

{
  "id": "<uuid>",
  "title": "My nikah concept",
  "status": "draft",
  "questionnaire": {
    "id": "<uuid>",
    "version": 1,
    "schema": {}
  },
  "answers": {},
  "selected_inspirations": [
    {
      "id": "<asset-uuid>",
      "position": 1,
      "available": true,
      "asset": {
        "id": "<asset-uuid>",
        "title": "...",
        "alt_text": "...",
        "garment_type": "...",
        "cultural_context": "...",
        "attribution": "...",
        "image_url": "...",
        "thumbnail_url": "..."
      }
    }
  ],
  "created_at": "...",
  "updated_at": "..."
}

When a previously selected asset becomes retired, expired or otherwise
ineligible:

{
  "id": "<asset-uuid>",
  "position": 1,
  "available": false,
  "asset": null
}

Do not reveal why it became unavailable.

The user must be able to remove or replace an unavailable selection, but
complete validation must fail until no unavailable selections remain.

List response

Keep list rows compact. Do not embed questionnaire schemas or full inspiration
records.

10. Preserve security boundaries

All design operations must retain:

anonymous and authenticated ownership;
ownership filtering before UUID lookup;
404 rather than 403 for inaccessible designs;
explicit Django CSRF protection for anonymous unsafe requests;
concurrency-safe workspace creation;
Cache-Control: no-store;
no raw Django session keys in domain tables or responses;
no public design URLs;
no storage keys or rights evidence;
no provider calls.
11. Backend tests

Add PostgreSQL-backed tests for:

Models and migrations
nullable questionnaire migration;
questionnaire PROTECT;
asset PROTECT;
unique design/asset;
unique design/position;
valid position bounds;
existing designs remain valid.
Answer validation
all shared contract cases;
malformed JSON-compatible shapes;
partial versus complete validation;
every supported question type;
every supported rule operator/action;
hidden answer rejection;
option restrictions;
exclusivity;
max/min constraints;
text normalisation;
no incidental exceptions.
Draft API
title-only backwards compatibility;
assigning a published questionnaire;
rejecting a draft questionnaire;
questionnaire assignment immutability;
resuming against a retired questionnaire;
partial autosave;
complete-validation endpoint;
unknown-field rejection;
malformed JSON;
JSON-only content type;
ownership isolation;
anonymous-to-authenticated promotion;
Cache-Control: no-store;
CSRF enforcement with enforce_csrf_checks=True.
Inspiration selections
zero to three accepted;
fourth rejected;
duplicates rejected;
order preserved;
unverified/expired/retired assets rejected;
concurrent updates never exceed the limit;
an asset becoming unavailable is represented without private data;
complete validation fails for unavailable selections.
12. OpenAPI contract

Annotate all changed and new operations accurately.

The contract must include:

typed create/update request;
compact list response;
detailed design response;
questionnaire payload;
selected inspiration availability shape;
validation success and errors;
JSON-only request bodies;
CSRF header on POST/PATCH/validate;
400/403/404/503 responses where runtime supports them.

Regenerate:

apps/api/openapi/schema.json

Run the OpenAPI validation and drift tests.

Commit Part A as:

feat(api): persist validated questionnaire drafts

Do not begin Part B until Part A is clean.

Part B — Schema-driven accessible frontend wizard
13. Dependencies

Add exact compatible versions through npm of:

react-hook-form;
zod;
@hookform/resolvers.

Do not add:

Redux;
Zustand;
React Query;
a second form library;
a UI component framework;
an analytics SDK.

Do not upgrade unrelated packages.

14. Regenerate frontend API types

Regenerate:

apps/web/src/api/schema.d.ts

Keep it generated-only and ensure the CI drift check passes.

The general exported OpenAPI client must remain GET-only.

For unsafe operations, add explicit endpoint functions using generated
request/response types and the existing CSRF-aware transport, such as:

createDesignDraft
updateDesignDraft
validateDesignDraft

Do not export a generic POST/PATCH client.

Generalise the existing private unsafe request helper only as much as needed to
support POST and PATCH while preserving:

in-memory CSRF token;
one CSRF refresh/retry maximum;
same-origin credentials;
no-store;
five-second timeout;
strict server-confirmed success;
controlled malformed/network failure handling.
15. Frontend feature structure

Use a focused feature structure, for example:

src/features/questionnaire/
  types.ts
  rules.ts
  validation.ts
  answer-utils.ts
  QuestionnaireWizard.tsx
  QuestionField.tsx
  InspirationPicker.tsx
  ReviewSummary.tsx

Do not put the entire phase into one page component.

Suggested routes:

/design/new
/design/<design-id>
/design/<design-id>/review

Add a clear “Start your design” call to action from the home page.

Do not require an account. Anonymous design creation must remain supported.

16. Derive frontend behaviour from the schema

Create pure TypeScript functions that:

evaluate the existing rule language;
determine visible questions;
determine required questions;
determine restricted options;
remove answers that become hidden or invalid;
build a per-step Zod schema from machine-readable constraints;
build a static Zod schema for the stable API envelope.

Do not manually encode:

garment-specific silhouettes;
colour limits;
embellishment exclusivity;
hidden saree/dupatta behaviour;
final-note limits;
individual question IDs as business rules.

Question IDs may be used for generic record keys and test fixtures, but not in
hard-coded conditional UI logic.

Backend validation remains authoritative.

17. Wizard behaviour

The wizard must:

fetch the active questionnaire through the generated GET client;
render steps, labels, help text and options from the schema;
support single-choice, multi-choice and text questions;
apply show/hide/require/restrict rules immediately;
clear stale hidden or disallowed answers;
validate the current visible step before moving forward;
show a textual progress indicator;
support Back and Continue;
preserve answer order for multi-choice selections;
show clear loading, unavailable and retry states.

Create the Design on the first successful persisted step rather than merely
visiting the page, to avoid empty drafts from casual page views.

After creation, replace the URL with the private design URL.

18. Server-backed autosave and resume

Do not store answers, design IDs or questionnaire content in:

localStorage;
sessionStorage;
IndexedDB;
cookies.

Persist progress to the private Design through the API.

Requirements:

choice changes save promptly;
text changes use a short debounce and save on blur;
navigation flushes pending saves;
show Saving…, Saved and controlled failure states through aria-live;
never report saved until the server confirms success;
keep unsaved values visible after a save error;
provide a retry action;
loading /design/<id> reconstructs the wizard from the persisted answers and
the design's linked questionnaire;
determine the resume step from persisted visible answers;
refreshing midway through the wizard restores server-confirmed progress.

Do not silently create a second design during resume.

19. Inspiration selection step

After questionnaire steps, show the approved public catalogue.

Each card must include:

sanitised thumbnail;
title;
alt text;
garment type where present;
cultural context where present;
public attribution;
selected/unselected state.

Use the existing relative image endpoints.

Do not use Next.js image optimisation in a way that caches or proxies
rights-revoked catalogue images. Use an unoptimised image rendering path that
continues to respect the backend's no-store eligibility checks.

Selection rules:

zero to three;
preserve selection order;
client prevents selecting a fourth;
server remains authoritative and also rejects a fourth;
selected state is keyboard accessible;
unavailable previously selected assets display a neutral
“This inspiration is no longer available” placeholder;
unavailable assets cannot remain in a valid completed draft;
no rights evidence, storage path or internal metadata appears.

A catalogue with no assets is a valid empty state.

Do not download or commit third-party images for tests. Generate image fixtures
in memory or use existing synthetic test assets.

20. Review screen

Before showing the final review as valid, call the server-side validation
endpoint.

Display:

garment and ceremony;
each visible answered question;
user-friendly option labels resolved from the linked schema;
final notes;
selected inspiration cards and attribution;
an Edit action for each section;
the concept-visualisation disclaimer.

Do not hard-code option labels.

Include a disabled button:

Generate my concept

Explain that generation is introduced in a later phase.

Do not simulate generation or call provider wrappers.

21. Accessibility and responsive design

Build a polished, mobile-first interface consistent with Sitara's bridalwear
identity without relying on stereotypes or excessive decoration.

Requirements:

semantic fieldset and legend for grouped choices;
real checkbox/radio semantics or equivalent accessible controls;
keyboard-operable inspiration cards;
visible focus indicators;
error summary focused after failed step validation;
errors associated with fields using aria-describedby;
aria-live for saving and server errors;
headings in logical order;
no colour-only state;
adequate contrast;
touch targets suitable for mobile;
respects reduced-motion preferences;
no dangerouslySetInnerHTML;
user-authored text rendered as text only.

User-facing cultural descriptions should come from the backend questionnaire,
not new frontend stereotypes or generic cultural claims.

22. Frontend tests

Test:

shared cross-language contract cases;
changing a schema constraint changes Zod validation without code changes;
question rendering from schema;
current-step validation;
show/hide behaviour;
restricted silhouettes;
hidden answers removed;
exclusivity;
text limits;
partial autosave;
save failure does not show success;
refresh/resume reconstruction;
no browser-storage persistence;
fourth inspiration blocked;
unavailable inspiration display;
attribution display;
server validation failure routes the user back to errors;
review labels derived from schema;
Generate button disabled;
keyboard and accessible-name behaviour;
generated GET client remains GET-only;
explicit unsafe wrappers retain CSRF retry-once behaviour;
existing authentication and status-page tests remain green.

Avoid huge snapshots.

23. Documentation

Create:

docs/decisions/0008-questionnaire-draft-and-wizard.md

Record:

the questionnaire version is pinned to each design;
published historical versions remain usable for resume;
backend validation is authoritative;
frontend Zod rules are derived from schema;
draft and complete validation modes;
server-backed autosave;
no browser storage for answers;
DesignInspiration ordering and three-item limit;
rights eligibility checked on selection and completion;
unavailable inspiration representation;
no provider calls or generation in Phase 7.

Update:

README.md;
docs/phases/PHASES.md;
docs/PROPOSAL.md;
CLAUDE.md only when permanent project rules genuinely need updating.

Mark Phase 7 delivered only after the manual checkpoint and full checks pass.

24. Full validation

Backend:

docker compose exec api python manage.py check
docker compose exec api python manage.py makemigrations --check --dry-run
docker compose exec api python manage.py migrate
docker compose exec api python -m pip check
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .
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

Phase 2:

Push-Location experiments/model-eval
.venv/Scripts/python -m pytest tests/test_model_decision.py -q
Pop-Location

Do not run:

docker compose down --volumes
25. Manual checkpoint

Through http://localhost:3001:

Ensure questionnaire version 1 is loaded.
Use an approved synthetic or genuinely rights-cleared catalogue asset.
Start anonymously.
Complete several questions and refresh midway.
Confirm server-confirmed answers resume.
Change garment type and confirm stale silhouette/draping answers clear.
Select three inspirations.
Confirm the client blocks a fourth.
Confirm a direct fourth-selection API attempt receives controlled 400.
Reach the review screen.
Confirm labels and attribution render from backend data.
Confirm the Generate button is disabled.
Open the design UUID in a second anonymous browser and confirm 404.
Register in the original browser and confirm the design remains available.
Retire or expire one selected synthetic asset and confirm it becomes an
unavailable placeholder and complete validation fails until replaced.
Confirm no provider calls occurred.

Do not use scraped or unverified imagery for this checkpoint.

26. Integrity

Confirm:

zero Anthropic and Replicate calls;
no provider keys required;
no unlicensed images committed;
no browser storage contains answers or credentials;
no design/session/storage/rights internals exposed;
no Docker volumes deleted;
no Phase 2 evidence changed;
no unrelated dependency upgrades;
backend OpenAPI has no drift;
generated TypeScript has no drift;
backend and frontend hosted CI are green after push.
Part B commit

Commit as:

feat(web): add questionnaire wizard

Do not amend Part A or rewrite history.

Do not push unless explicitly requested.

Return

Return only:

Part A full SHA;
Part B full SHA;
migrations and model changes;
validator and rule semantics;
API contract changes;
inspiration eligibility behaviour;
generated-contract drift results;
frontend wizard routes and behaviour;
autosave/resume behaviour;
accessibility coverage;
backend checks;
frontend checks;
manual checkpoint result;
zero-provider-call confirmation;
unresolved issues;
hosted CI status.