# Sitara Phase 19 — Private stylist annotation workspace

Known repository baseline when this specification was written:

```text
d7cd168091ea901863e03782f45ae8ab263399a9
```

Required starting point:

- the latest `main` must be a clean descendant of the baseline;
- Phases 1–18 must be delivered;
- deployment, retention, private image delivery and E2E foundations must be green;
- Phase 16B questionnaire-feedback work must be merged if it was accepted into the roadmap;
- no later phase may already have introduced annotation or design-sharing semantics.

Phase 19 adds an owner-operated stylist annotation workspace. "Stylist" describes the workflow, not a new account role. In this phase, only the existing private design owner may create or edit annotations. External stylist collaboration, invitation links and sharing are intentionally deferred.

Before changing anything:

1. Run `git status --short`, `git log -20 --oneline`, `git rev-parse HEAD`, and `git branch --show-current`.
2. Confirm the working tree is clean and Phase 18 is merged.
3. Report any annotation-like models, endpoints, frontend packages or sharing work already present.
4. Do not work directly on `main`; follow the repository's `/run-phase`, branch, per-commit council-review, push and draft-PR workflow.
5. Use the current repository structure and existing ownership, CSRF, OpenAPI, image-delivery, logging and retention patterns.

## Main objective

Allow the owner of a generated `DesignVersion` to mark up the private concept image without modifying the original image.

The first supported annotation workspace must provide:

- pins;
- arrows;
- rectangles;
- bounded freehand strokes;
- a short text note for each annotation;
- zoom and pan;
- keyboard-accessible selection and adjustment;
- autosave with visible saved/saving/error states;
- multi-tab conflict protection;
- a structured annotation list as a non-canvas alternative;
- hide/show overlays;
- clear-all with confirmation;
- a private, on-demand annotated PNG export;
- immutable original generated imagery;
- the same anonymous-session and authenticated-user ownership guarantees used by the design API.

An annotation is editorial feedback attached to one immutable generated version. It is not a refinement, a new generated version, an AI prompt, a public comment or a modification to the canonical stored image.

## Safety mode

This phase requires no AI providers.

Keep:

```text
DEMO_MODE=true
ALLOW_PAID_AI_CALLS=false
LIVE_GENERATION_ENABLED=false
```

Use no provider credentials and make no Anthropic or Replicate calls.

Never run:

```text
docker compose down --volumes
```

Never log annotation note text, signed URLs, storage keys, image hashes or private design identifiers beyond the repository's existing safe correlation patterns.

Tests and fixtures must use synthetic designs and synthetic local images only.

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
- `docs/phases/phases-4.md`
- `docs/phases/phases-6.md`
- `docs/phases/phases-7.md`
- `docs/phases/phases-11.md`
- `docs/phases/phases-12.md`
- `docs/phases/phases-14.md`
- `docs/phases/phases-16.md`
- `docs/phases/phases-17.md` if present
- `docs/phases/phases-18.md` if present
- `docs/decisions/0004-private-design-ownership.md`
- `docs/decisions/0012-private-design-image-storage.md`
- `docs/decisions/0013-generation-progress-and-results.md`
- `docs/decisions/0015-single-round-refinement.md`
- the Phase 16 retention/security ADR
- `apps/api/sitara/designs/models.py`
- design ownership, serializers, views, result and OpenAPI modules
- private image signing and delivery modules
- canonical image-processing modules
- retention purge and stuck-job maintenance modules
- correlation/logging and Sentry configuration
- `apps/api/openapi/schema.json`
- `apps/web/src/api/schema.d.ts`
- result page, version comparison and image-loading components
- existing TanStack Query client and mutation patterns
- global accessible button, dialog, toast and form components
- current E2E infrastructure and demo fixtures.

Before implementation, report:

- the exact current result route and component that owns the generated image;
- how the original image and thumbnail are delivered;
- whether the signed image response can safely be used as a canvas source;
- the existing image dimensions available to the client;
- the current retention cascade behaviour;
- the correct ownership helper to reuse;
- whether a focused module under `designs` or a small new Django app best matches repository conventions;
- the exact frontend library plan, including whether a dependency is genuinely necessary.

Prefer browser-native SVG/canvas primitives and small focused utilities. Do not add a large whiteboard/collaboration framework without proving it is necessary.

## Required commit boundaries

Implement as four independently reviewed commits:

1. `feat(annotations): add version-bound annotation model and validation`
2. `feat(api): add private annotation persistence and export endpoints`
3. `feat(frontend): add accessible stylist annotation workspace`
4. `docs(phase-19): record private annotation architecture and limits`

Do not combine the commits. Each must pass focused tests and the per-commit council before continuing.

## Part A — Annotation data contract

### 1. Store one versioned annotation document per DesignVersion

Add a focused model equivalent to `DesignAnnotationDocument`.

Required fields:

- UUID primary key;
- one-to-one or unique foreign key to `DesignVersion`;
- positive annotation schema version;
- bounded JSON document;
- positive integer revision;
- created and updated timestamps.

Ownership is derived only through:

```text
annotation document -> DesignVersion -> Design -> DesignSession
```

Do not duplicate user ids, session ids, raw Django session keys, public tokens, image storage keys or signed URLs onto the annotation model.

Deleting a design/version through existing retention behaviour must delete its annotation document. Do not weaken the `DesignVersion.parent_version` protection or permanent-image immutability.

### 2. Define a strict annotation schema

Use a pure-Python, dependency-light validator and a matching generated/typed API contract.

The document should be equivalent to:

```json
{
  "schema_version": 1,
  "image_width": 1024,
  "image_height": 1365,
  "items": [
    {
      "id": "client-generated-uuid",
      "type": "pin",
      "geometry": {},
      "note": "Short bounded note",
      "palette": "rose",
      "created_order": 1
    }
  ]
}
```

Exact shape may be refined, but all requirements below are binding.

Supported item types:

- `pin`;
- `arrow`;
- `rectangle`;
- `freehand`.

Geometry requirements:

- store normalised coordinates in the closed range `[0, 1]`;
- never store viewport pixels as the authoritative geometry;
- pin: one point;
- arrow: start and end points;
- rectangle: two corners or x/y/width/height;
- freehand: bounded ordered point list;
- all coordinates finite numbers;
- reject NaN, infinity, negative values and values above one;
- reject zero-area rectangles and zero-length arrows after a small documented tolerance;
- freehand must contain a minimum useful number of points and a strict maximum.

Bounds:

- maximum 100 annotation items per document;
- maximum 500 points per freehand item;
- maximum 500 characters per note;
- maximum 256 KiB canonical serialized document;
- fixed allowlisted palette ids only;
- bounded item ids;
- duplicate item ids rejected;
- created order unique and positive;
- unknown fields rejected;
- no HTML, Markdown rendering, URLs, file paths or executable data.

Normalise note whitespace safely, but preserve the user's ordinary text. Render notes as escaped text only.

### 3. Bind annotations to immutable image identity

When first creating the document, persist the canonical image width and height from the `DesignVersion`.

Requirements:

- annotation creation is allowed only after permanent image ingest is complete;
- stored dimensions must exactly match the version's canonical image dimensions;
- clients cannot choose or change the bound image identity;
- a later request with mismatched dimensions is rejected;
- annotations never modify `image_storage_key`, image bytes, hashes, processor version or DesignSpec;
- a refined design version has its own separate annotation document;
- annotations are never copied automatically from parent to refined version.

## Part B — Private API and concurrency

### 4. Add ownership-first endpoints

Add canonical endpoints equivalent to:

```text
GET    /api/v1/designs/{design_id}/versions/{version_id}/annotations/
PUT    /api/v1/designs/{design_id}/versions/{version_id}/annotations/
DELETE /api/v1/designs/{design_id}/versions/{version_id}/annotations/
GET    /api/v1/designs/{design_id}/versions/{version_id}/annotations/export/
```

Requirements:

- use existing anonymous/authenticated ownership helpers;
- inaccessible design or mismatched version returns indistinguishable 404;
- unsafe methods require Django session CSRF;
- all responses use `Cache-Control: no-store`;
- never expose storage keys, hashes, user ids, DesignSession ids or signed URLs;
- GET before first save returns a clear empty document response or documented 404, consistently typed;
- PUT replaces the complete bounded document atomically;
- DELETE clears only the annotation document, never the generated image;
- no public list/search endpoint;
- no cross-design lookup by annotation UUID.

### 5. Add optimistic concurrency

Protect multi-tab and stale autosave writes.

Use one explicit strategy consistently:

- request field `expected_revision`, or
- a properly implemented `If-Match`/ETag contract.

Requirements:

- create begins at revision 1;
- every successful replacement increments exactly once;
- stale revision returns `409 annotation_conflict`;
- the stored document remains unchanged on conflict;
- row locking/atomic update prevents two matching writes both succeeding;
- idempotent replay of the same already-applied payload is documented and tested;
- error responses never echo the full private annotation document;
- frontend offers reload/keep-local-copy behaviour rather than silently overwriting.

Do not add WebSockets, CRDTs, presence, live cursors or a collaborative document framework.

### 6. Add on-demand annotated PNG export

Export must be generated from:

- the canonical private original image read through the existing storage boundary;
- the persisted validated annotation document;
- deterministic server-side rendering.

Requirements:

- ownership-first 404;
- no signed storage URL is accepted from the client;
- output is a PNG attachment with a fixed safe filename;
- `Cache-Control: no-store`;
- do not persist the exported composite unless a later phase explicitly requires it;
- original stored image remains unchanged;
- strip metadata;
- enforce maximum output dimensions and memory bounds;
- use fixed allowlisted line widths, marker sizes, fonts and palette colours;
- render numbered marks plus a readable note legend so note text is not lost;
- wrap and truncate notes deterministically within documented limits;
- safely handle Unicode text;
- no external font download at runtime;
- no network request during rendering;
- export failures return controlled codes without leaking storage details;
- log exception type and correlation id only.

Reuse the pinned image-processing dependency already in the repository where possible. Do not introduce a general report/PDF service.

### 7. Update retention and observability

- annotation documents cascade with retained/deleted designs;
- retention purge tests prove no orphan annotation rows remain;
- annotation note text is never logged or sent to Sentry;
- request bodies are not captured;
- metrics may count save/export success or failure but must not contain design ids or note content;
- keep designs private by default.

## Part C — Frontend annotation workspace

### 8. Add an owner-only annotation entry point

Add an "Annotate concept" action on the private result/version view.

Route it using the current App Router conventions, for example:

```text
/designs/{designId}/versions/{versionId}/annotate
```

The exact route may follow existing patterns, but it must remain private and ownership-backed by the API.

Display:

- version identity, such as Original or Refined;
- demo/live historical label already used by results;
- immutable-image explanation;
- saving status;
- annotation count;
- export action;
- return-to-result action.

Do not imply that annotations modify the AI-generated design or feed automatically into refinement.

### 9. Use a responsive image overlay

Implement the visual layer with an SVG overlay or another reviewable browser-native approach positioned over the rendered image.

Requirements:

- coordinate transforms are derived from intrinsic image dimensions and current rendered bounds;
- resizing never changes stored normalised geometry;
- zoom and pan do not mutate geometry;
- pointer and touch creation work;
- selection handles remain usable at zoom;
- marks remain aligned when the signed image URL refreshes;
- image-load failure does not destroy local unsaved notes;
- original image cannot be dragged or selected accidentally while annotating;
- no client-side mutation of the source image blob;
- no data URL containing the private original is placed in localStorage or logs.

### 10. Tools and editing

Provide:

- select/move;
- pin;
- arrow;
- rectangle;
- freehand;
- undo and redo for the current unsaved editing session;
- delete selected;
- hide/show all;
- zoom in/out/reset;
- clear all with confirmation.

Use a fixed small accessible palette. Do not expose arbitrary CSS colour input.

Every new annotation opens or focuses a bounded note editor. Empty notes may be allowed for purely visual marks if the decision is documented, but numbered exports and the accessible list must remain understandable.

### 11. Accessible non-canvas representation

The visual overlay cannot be the only way to understand or edit annotations.

Add a structured annotation list:

- ordered by `created_order`;
- type and number announced;
- note editable through a labelled text field;
- palette selectable through labelled controls;
- delete action;
- focus/select corresponding visual mark;
- keyboard nudge controls for position;
- numeric or descriptive geometry summary;
- validation errors associated with the correct item.

Keyboard requirements:

- toolbar reachable in logical order;
- tool state announced;
- Escape returns to select mode or cancels an in-progress mark;
- Delete/Backspace removes a selected item only after focus rules are safe;
- arrow-key nudge with a documented increment;
- Shift + arrow for larger nudge;
- no keyboard trap;
- visible focus at all zoom levels.

Add concise instructions, not a gesture-only tutorial.

### 12. Autosave and conflict UX

Use the existing typed API and TanStack Query patterns.

Requirements:

- local edits update immediately;
- debounced autosave after a reasonable idle period;
- only one save in flight per document;
- later edits queue behind the current save;
- visible `Unsaved`, `Saving`, `Saved`, `Save failed` and `Conflict` states;
- retry is explicit after network failure;
- navigation with unsaved changes produces a controlled warning;
- successful server response replaces the local revision;
- 409 conflict never silently discards either copy;
- signed image URLs and annotation documents remain memory-only;
- do not place annotation data in localStorage;
- page refresh loads the persisted server document.

### 13. Mobile and responsive behaviour

Support desktop and tablet fully.

On small mobile screens:

- keep basic pin, selection, note editing, hide/show and zoom usable;
- allow advanced drawing tools to move into an accessible toolbar disclosure;
- do not horizontally overflow;
- do not make pinch zoom the only zoom method;
- retain the annotation list below the image;
- document any deliberate mobile limitation honestly.

## OpenAPI and generated client

Add explicit request/response serializers for:

- annotation point/geometry variants;
- annotation item;
- annotation document;
- save request with revision;
- controlled error responses;
- export binary PNG response.

Requirements:

- stable operation ids;
- canonical trailing-slash paths;
- no bearer/JWT scheme;
- CSRF header documented for PUT/DELETE;
- export documented as `image/png` binary attachment;
- regenerate schema and TypeScript deterministically;
- do not hand-edit generated files.

## Automated tests

Add focused tests for at least:

### Model and schema

- one document per DesignVersion;
- cascade deletion;
- positive revision;
- every geometry variant;
- coordinate bounds;
- NaN/infinity rejection;
- zero-area geometry rejection;
- item, point, note and payload limits;
- duplicate item ids/order rejection;
- unknown fields rejected;
- dimension mismatch rejected;
- note HTML remains inert text.

### API and ownership

- anonymous owner can read/write their own annotation;
- authenticated owner can read/write their own annotation;
- anonymous-to-authenticated design promotion preserves annotation access;
- second browser/account receives indistinguishable 404;
- mismatched design/version receives 404;
- CSRF enforced;
- no-store headers;
- private fields absent;
- stale revision returns controlled 409;
- concurrency test admits only one of two same-revision writes;
- delete clears annotations only;
- no endpoint exposes a public annotation id lookup.

### Export

- deterministic PNG bytes for a fixed synthetic image/document where practical;
- marks align to normalised coordinates;
- note legend rendered;
- safe filename and attachment disposition;
- metadata absent;
- dimension/memory limits enforced;
- storage failure is controlled;
- original object is unchanged;
- no external network call.

### Frontend

- tool selection and mark creation;
- coordinate transform under responsive resize;
- note edit/delete;
- undo/redo;
- autosave sequencing;
- network failure and retry;
- 409 conflict handling;
- unsaved-navigation warning;
- annotation list and visual selection stay synchronised;
- keyboard nudge and Escape behaviour;
- signed-image refresh does not move marks;
- axe checks for toolbar, canvas region, note list, conflict dialog and export action.

### E2E

Extend the zero-cost demo E2E suite:

1. create/generate a synthetic demo design;
2. open annotation workspace;
3. add a pin and rectangle;
4. enter notes;
5. reload and confirm persistence;
6. export PNG;
7. confirm another browser cannot access it;
8. confirm original result image remains unchanged.

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

Run schema/client generation twice and prove no second-run diff.

Run deployment smoke tests if routes or proxy configuration change.

## Manual checkpoint

With `DEMO_MODE=true` and no provider credentials:

1. Generate a synthetic demo design.
2. Add each supported annotation type.
3. Edit notes through the annotation list.
4. Resize the browser and zoom/pan; verify alignment.
5. Navigate away during an unsaved edit and confirm the warning.
6. Reload and verify persistence.
7. Open the same annotation in a second tab, create a conflict and confirm no silent overwrite.
8. Export an annotated PNG and verify numbered marks and note legend.
9. Compare the original stored/downloaded design image and confirm it is unchanged.
10. Repeat the core flow keyboard-only.
11. Verify another browser/account receives 404.
12. Confirm logs and Sentry contain no note text or private image data.
13. Run the retention purge against synthetic expired data and confirm annotations are removed with their design.

## Non-goals

Do not implement:

- external stylist accounts or a stylist role;
- design sharing or invitation links;
- real-time collaboration;
- comments/replies/mentions;
- WebSockets, CRDTs or presence;
- AI interpretation of annotations;
- automatic conversion of annotations into refinement prompts;
- additional design versions;
- modification or replacement of original image bytes;
- persistent annotated-image storage;
- PDF export;
- public galleries;
- offline annotation editing;
- unrestricted colours, fonts or uploaded stickers;
- paid provider calls.

## Documentation and decision record

Add the next available ADR documenting:

- annotations as a separate overlay document;
- ownership inherited through DesignVersion;
- one document per immutable version;
- normalised coordinates;
- strict bounded JSON schema;
- revision-based optimistic concurrency;
- no collaboration in v1;
- server-side on-demand PNG export;
- no mutation of original image;
- no localStorage;
- retention and privacy behaviour;
- accessible annotation-list alternative;
- deferred sharing and AI-assisted interpretation.

Update:

- `docs/phases/PHASES.md`;
- privacy documentation;
- runbook/storage notes if export changes resource considerations;
- OpenAPI and generated client documentation;
- E2E coverage notes.

## Completion report

Report:

- starting and ending commit;
- model/migration details;
- annotation schema and bounds;
- endpoint list and ownership behaviour;
- concurrency strategy;
- frontend implementation and dependency choices;
- export implementation and resource limits;
- accessibility behaviour;
- retention/logging/Sentry verification;
- tests and commands run;
- manual checkpoint results;
- council findings and resolutions;
- explicit confirmation that original image bytes remain unchanged;
- explicit confirmation of zero AI/provider calls;
- each commit SHA and draft PR URL.
