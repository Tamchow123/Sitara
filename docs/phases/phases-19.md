# Sitara Phase 19 — Private stylist annotation workspace

Known repository baseline when this specification was last revised:

```text
7b67ec8b343c6e5902752e5dc0c43c64de9e2ebc
```

Required starting point:

- the latest `main` must be a clean descendant of the baseline;
- **Phase 18 is deliberately skipped.** Phases 1–17 plus Phase 16B are delivered
  and merged; Phase 18 (E2E hardening and deployment) has no specification file
  and is not implemented. Phase 19 therefore extends **Phase 17's** Playwright
  suite (`apps/web/e2e/`), and every "deployment smoke test" obligation below is
  struck rather than assumed;
- retention, private image delivery and the Phase 17 E2E foundation must be green;
- Phase 16B questionnaire-feedback work is merged (confirmed on `main`);
- no later phase may already have introduced annotation or design-sharing semantics.

Phase 19 adds an owner-operated stylist annotation workspace. "Stylist" describes
the workflow, not a new account role. In this phase, only the existing private
design owner may create or edit annotations. External stylist collaboration,
invitation links and sharing are intentionally deferred.

Before changing anything:

1. Run `git status --short`, `git log -20 --oneline`, `git rev-parse HEAD`, and
   `git branch --show-current`.
2. Confirm the working tree is clean.
3. Report any annotation-like models, endpoints, frontend packages, email
   infrastructure or sharing work already present.
4. Do not work directly on `main`; follow the repository's `/run-phase`, branch,
   per-commit council-review, push and draft-PR workflow.
5. Use the current repository structure and existing ownership, CSRF, OpenAPI,
   image-delivery, throttling, logging and retention patterns.

## Main objective

Allow the owner of a generated `DesignVersion` to mark up the private concept
image without modifying the original image, and to have the flattened result
**emailed to their own account address**.

The first supported annotation workspace must provide:

- pins;
- arrows;
- rectangles;
- bounded freehand strokes;
- a short text note for each annotation;
- an allowlisted mark palette;
- zoom and pan;
- keyboard-accessible selection and adjustment;
- autosave with visible saved/saving/error states;
- multi-tab conflict protection;
- a structured annotation list as a non-canvas alternative;
- hide/show overlays;
- clear-all with confirmation;
- a private, on-demand annotated PNG **sent to the owner's account email**;
- immutable original generated imagery;
- the same anonymous-session and authenticated-user ownership guarantees used by
  the design API.

An annotation is editorial feedback attached to one immutable generated version.
It is not a refinement, a new generated version, an AI prompt, a public comment
or a modification to the canonical stored image.

## Design source — the handoff is authoritative for Part C

The annotation workspace has a supplied UX design. Read it before writing any
frontend code:

| File | What it gives you |
| --- | --- |
| `design_handoff_sitara_flow/Sitara Annotation.dc.html` | The annotation workspace: layout, chrome, marks, list rows, dialogs, export artifact |
| `design_handoff_sitara_flow/README.md` §7 | The written intent behind that screen, plus the prototype props |
| `design_handoff_sitara_flow/Sitara Concept.dc.html` | The Concept screen's revised under-render actions (**Annotate**, **Send to account**) |
| `apps/web/src/app/styles/tokens.css` | The transcribed "Organic" tokens every measurement below refers to |
| `apps/web/src/app/styles/base.css` | The existing `.btn*`, `.tag*`, `.field*`, `.visually-hidden` primitives to reuse |

Handoff rules that bind this phase:

- The bundle is **reference material only** — never imported, bundled or served.
  Its prototype runtimes (`support.js`, `image-slot.js`) must not be ported.
- The `⋮` overflow menu in the header is a **prototype-only** state switcher
  (simulated autosave failure, simulated conflict, unsaved-leave, keyboard-focus
  pass). **Do not ship it.**
- "Restore sample marks" in the empty list panel is **prototype-only**. Do not
  ship it.
- The prototype's `panelSide` and `exportLegend` props are prototype tweaks. Ship
  the defaults only: panel on the **right**, export legend **below the image**.
- `CLAUDE.md` §5 names the handoff `design/sitara-handoff/`; the bundle actually
  lives at repository root as `design_handoff_sitara_flow/`. Correct the
  documentation in this phase's docs commit rather than moving the bundle.
- **`docs/phases/PHASES.md` is deliberately left stale until this phase's own
  docs commit.** Its Phase 19 row still describes a downloadable PNG export, four
  commits, and "Phases 1–18 delivered". This file supersedes all three; the
  roadmap is corrected in the docs commit, not before. Do not read the
  disagreement as an open question.

Three places where the prototype must **not** be copied literally, because doing
so would break correctness. Each is called out again where it applies:

1. The prototype renders the concept with `object-fit: cover` inside a fixed
   `aspect-ratio: 3/4` box. **Cover crops.** A cropped image destroys the
   coordinate fidelity this whole feature rests on. Production sizes the image
   box from the version's own intrinsic dimensions and never crops.
2. The prototype's SVG overlay uses a hard-coded `viewBox="0 0 600 800"`.
   Production derives the overlay's coordinate space from the version's real
   canonical dimensions (§9).
3. The prototype's export filename is derived from the design title
   (`sitara-walima-lehenga—annotations.png`). Production uses a **fixed**
   server-owned filename, never a user-controlled value (§7).

## Safety mode

This phase requires no AI providers.

Keep:

```text
DEMO_MODE=true
ALLOW_PAID_AI_CALLS=false
LIVE_GENERATION_ENABLED=false
```

Use no provider credentials and make no Anthropic or Replicate calls.

Email delivery is **not** an AI provider, but it is a new outbound network
capability and gets the same fail-closed discipline (§8). Automated tests and CI
make **zero SMTP connections**: the locmem email backend is used throughout, and
a test asserts no real backend is ever constructed.

Never run:

```text
docker compose down --volumes
```

Never log annotation note text, recipient email addresses, signed URLs, storage
keys, image hashes or private design identifiers beyond the repository's existing
safe correlation patterns.

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
- `docs/phases/phases-4.md`, `phases-6.md`, `phase-7.md`, `phases-11.md`,
  `phases-12.md`, `phases-14.md`, `phases-16.md`, `phases-16b.md`, `phases-17.md`
  (there is no `phases-18.md`)
- `docs/decisions/0004-private-design-ownership.md`
- `docs/decisions/0012-private-design-image-storage.md`
- `docs/decisions/0013-generation-progress-and-results.md`
- `docs/decisions/0015-single-round-refinement.md`
- `docs/decisions/0017-live-generation-security-and-cost-controls.md`
- `docs/decisions/0018-questionnaire-feedback-and-visual-choice-ux.md`
- `apps/api/sitara/designs/models.py`, `ownership.py`, `views.py`, `urls.py`,
  `serializers.py`, `openapi.py`, `result.py`, `upload_service.py`
- `apps/api/sitara/accounts/models.py`, `rate_limits.py`
- `apps/api/sitara/media/delivery.py`, `ingest.py`, `image_processing.py`
- `apps/api/sitara/image_sanitize.py`
- `apps/api/sitara/generation/maintenance.py`, `tasks.py`, `admission.py`
- `apps/api/config/settings.py` (gates, production validation, Sentry)
- `apps/api/openapi/schema.json`
- `apps/web/src/api/schema.d.ts`, `apps/web/src/lib/api.ts`,
  `apps/web/src/lib/transport.ts`, `apps/web/src/lib/sentry-scrub.ts`
- `apps/web/src/features/results/` (result page, image loading, refresh) and
  `apps/web/src/features/refinement/VersionComparison.tsx`
- `apps/web/src/features/questionnaire/InfoDrawer.tsx` (the existing accessible
  modal precedent)
- `apps/web/src/app/styles/tokens.css` and `base.css`
- `apps/web/e2e/` and `apps/web/e2e/README.md`
- the design handoff files listed above.

### Findings already established — do not re-derive, verify and extend

These were confirmed against the current `main` while this specification was
revised. Re-check them, then move on:

- **Result route and owning component.** `/design/[designId]/result/[versionId]`
  → `apps/web/src/app/design/[designId]/result/[versionId]/page.tsx` →
  `features/results/DesignResult.tsx`. The image is owned by
  `features/results/ResultImage.tsx`. A **refined** version renders through
  `features/refinement/VersionComparison.tsx` instead.
- **Image delivery.** `GET /designs/{id}/versions/{vid}/images/` →
  `DesignVersionImagesView` → `media.delivery.issue_design_image_urls()`, which
  returns short-lived presigned S3/MinIO GET URLs for the original and thumbnail
  plus one shared `expires_at`. The filesystem backend fails closed. Responses
  carry `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.
- **Canvas source safety.** The signed URL renders fine in an `<img>`, and the
  overlay is a **separate SVG element** — the browser never needs to read the
  image's pixels, so no cross-origin canvas taint arises and no `crossOrigin`
  attribute is required. Do not introduce a `<canvas>` that reads the private
  original; the client never touches its bytes (§9).
- **Dimensions available to the client.** `images.original.width` /
  `.height` already come back from the images endpoint. Those are the canonical
  `DesignVersion.image_width` / `image_height`.
- **Retention cascade.** `generation.maintenance.purge_expired_designs` locks the
  `Design` row, deletes permanent (`design_images` alias), staging and upload
  objects **first**, then deletes the row and lets the normal Django cascade
  remove children. A new annotation table hangs off `DesignVersion` and therefore
  cascades automatically — but it must be covered by a test (§7).
- **Ownership helper.** `designs.ownership.accessible_designs(request)`, applied
  **before** any UUID lookup, exactly as `DesignVersionImagesView` and
  `DesignVersionResultView` already do.
- **Where the code goes.** A **focused module set inside the existing `designs`
  app**, not a new Django app. Annotations have no independent lifecycle, no
  admin surface of their own and no separate migration story; they are one more
  private child of `DesignVersion`, like `DesignInspirationUpload` is of
  `Design`. The server-side PNG composition belongs in the existing
  `sitara.media` support package, which already owns Pillow policy.
- **Where the email capability goes.** One named boundary module,
  `sitara/media/account_delivery.py`, is the *only* place that constructs a
  Django email message or touches the mail backend — the same
  single-choke-point discipline `ai_gateway` gives paid providers and
  `media/delivery.py` gives signed image URLs. The capability gate, recipient
  derivation, attachment bounds and message construction all live there; views
  and the Celery task call it and never assemble mail themselves. Do not scatter
  `send_mail`/`EmailMessage` across views, serializers or tasks.
- **The module takes the user ROW, never an address string.** Its signature
  accepts the `User` (or its id) and derives `.email` itself as its own step.
  This is deliberately narrower than "the caller resolves the address and hands
  it in", and it is the load-bearing detail: §8.1 calls the recipient rule the
  single most important rule in this phase, and a choke point that accepts an
  arbitrary string cannot enforce it — every present and future call site would
  have to re-implement the invariant correctly, which is exactly what a choke
  point exists to prevent. Passing a row makes a caller-chosen address
  *unexpressible* rather than merely discouraged. Equally, the module never
  touches `request`: it is handed a row by the view or task, so `sitara.media`
  stays an image/storage support package and does not grow request handling.
  A test must call the module directly, bypassing the view, and confirm there is
  no parameter through which an arbitrary recipient can be supplied.
- **Frontend dependencies.** **None.** The workspace is plain React plus an
  inline SVG overlay, `ResizeObserver` for the rendered-bounds transform, and the
  existing TanStack Query client. No whiteboard, canvas, drag-and-drop or
  collaboration library. Adding one requires proving the native approach failed.
- **Pillow text rendering.** Pillow 11.3.0 is pinned and
  `ImageFont.load_default(size=…)` returns a scalable embedded FreeType face — no
  font file ships and nothing is downloaded at runtime. `raqm` is **not** built
  in the image, so complex-script shaping (Arabic, Indic conjuncts) is
  unavailable; text still renders as Unicode but will not shape. Document this
  limitation honestly rather than claiming full script support (§7).
- **Email infrastructure.** There is **none** today — no `EMAIL_BACKEND`, no
  `DEFAULT_FROM_EMAIL`, no send call anywhere. Phase 19 introduces it (§8).
- **Account email.** `accounts.User.email` is the canonical unique login
  identifier and is exposed to the authenticated caller as `AuthUser.email`.
  Registration does **not** verify it (§8 records the consequence).

Prefer browser-native SVG primitives and small focused utilities. Do not add a
large whiteboard/collaboration framework without proving it is necessary.

## Required commit boundaries

Implement as six independently reviewed commits:

1. `feat(annotations): add version-bound annotation model and validation`
2. `feat(api): add private annotation persistence endpoints`
3. `feat(media): add deterministic annotated PNG composition`
4. `feat(email): add fail-closed account render delivery`
5. `feat(frontend): add accessible stylist annotation workspace`
6. `docs(phase-19): record private annotation architecture and limits`

Do not combine the commits. Each must pass focused tests and the per-commit
council before continuing.

---

## Part A — Annotation data contract

### 1. Store one versioned annotation document per DesignVersion

Add a focused model equivalent to `DesignAnnotationDocument`.

Required fields:

- UUID primary key;
- one-to-one or unique foreign key to `DesignVersion` (`on_delete=CASCADE`);
- positive annotation schema version;
- bounded JSON document;
- positive integer revision;
- created and updated timestamps.

Ownership is derived only through:

```text
annotation document -> DesignVersion -> Design -> DesignSession
```

Do not duplicate user ids, session ids, raw Django session keys, public tokens,
image storage keys or signed URLs onto the annotation model.

Deleting a design/version through existing retention behaviour must delete its
annotation document. Do not weaken the `DesignVersion.parent_version` protection
or permanent-image immutability.

### 2. Define a strict annotation schema

Use a pure-Python, dependency-light validator and a matching generated/typed API
contract.

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
      "geometry": { "point": { "x": 0.5, "y": 0.175 } },
      "note": "Raise the neckline ~2 cm — it sits lower than the brief.",
      "palette": "terracotta",
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
- reject zero-area rectangles and zero-length arrows after a small documented
  tolerance;
- freehand must contain a minimum useful number of points and a strict maximum.

Bounds:

- maximum 100 annotation items per document;
- minimum 2 and maximum 500 points per freehand item;
- **maximum 140 characters per note** — the handoff's editor limit, adopted as
  the single product limit so the wire contract, the editor counter and the
  export legend layout can never drift. (An earlier draft of this spec said 500;
  140 supersedes it.) The 256 KiB and 100-item ceilings below remain the outer
  bounds;
- maximum 256 KiB canonical serialized document;
- fixed allowlisted palette ids only — exactly `terracotta`, `sage`, `ink`
  (§10);
- bounded item ids (a client-generated UUID string; bound the length and reject
  anything that is not a plain UUID);
- duplicate item ids rejected;
- created order unique and positive;
- unknown fields rejected;
- no HTML, Markdown rendering, URLs, file paths or executable data.

Normalise note whitespace safely (collapse runs of whitespace, strip control
characters, trim ends), but preserve the user's ordinary text. Render notes as
escaped text only — React's default escaping in the workspace, and plain glyph
drawing in the PNG export.

"Control characters" here means **both** the ASCII C0/C1 ranges (Unicode general
category `Cc`) **and** the invisible formatting characters in category `Cf` —
notably the bidirectional overrides and isolates `U+202A`–`U+202E` and
`U+2066`–`U+2069`, plus `U+200E`/`U+200F`. Stripping only `Cc` is the obvious
reading and the wrong one. Escaping stops markup injection but does **not** stop a
Trojan-Source-style presentation mismatch, where the stored bytes render or are
announced in a different order than they were typed — and this phase renders the
same note into three separately-trusted surfaces: an `aria-label` read aloud by
assistive technology (§13), the on-canvas chip and list row, and permanent pixel
text in the PNG legend that then **leaves the system by email** to an address the
spec itself records as unverified (§8.5). A test must assert a note containing
`U+202E` is normalised before persistence. Do not solve this with an allow-list of
scripts: this product's cultural-accuracy principle means Arabic, Urdu and Indic
notes must keep working.

An **empty note is allowed** for a purely visual mark. The decision is
deliberate: a rectangle around a hem sometimes says everything. The consequences
are handled explicitly — the annotation list announces such a row as
`Rectangle 3, no note`, and the export legend prints `3 — (no note)` so the
numbering stays continuous and understandable.

### 3. Bind annotations to immutable image identity

When first creating the document, persist the canonical image width and height
from the `DesignVersion`.

Requirements:

- annotation creation is allowed only after permanent image ingest is complete
  (`DesignVersion.has_permanent_image`); before that, the endpoints return the
  same controlled `409 design_image_not_ready` the images endpoint already uses;
- stored dimensions must exactly match the version's canonical image dimensions;
- clients cannot choose or change the bound image identity — a client-supplied
  `image_width`/`image_height` is validated against the server's values and
  rejected on mismatch, never trusted;
- a later request with mismatched dimensions is rejected;
- annotations never modify `image_storage_key`, image bytes, hashes, processor
  version or DesignSpec;
- a refined design version has its own separate annotation document;
- annotations are never copied automatically from parent to refined version.

---

## Part B — Private API, concurrency and rendering

### 4. Add ownership-first endpoints

Add canonical endpoints equivalent to:

```text
GET    /api/v1/designs/{design_id}/versions/{version_id}/annotations/
PUT    /api/v1/designs/{design_id}/versions/{version_id}/annotations/
DELETE /api/v1/designs/{design_id}/versions/{version_id}/annotations/
```

Requirements:

- use existing anonymous/authenticated ownership helpers
  (`accessible_designs(request)` **before** the UUID lookup);
- inaccessible design or mismatched version returns indistinguishable 404;
- unsafe methods require Django session CSRF (`@method_decorator(csrf_protect,
  name="dispatch")`, never `csrf_exempt`) — DRF's `SessionAuthentication` alone
  does not cover anonymous unsafe requests;
- all responses use `Cache-Control: no-store`;
- never expose storage keys, hashes, user ids, DesignSession ids or signed URLs;
- **GET before first save returns `200` with an empty document** — `items: []`,
  `revision: 0`, and the server's own bound `image_width`/`image_height`. A
  stored document always has `revision >= 1`, so `revision: 0` is the unambiguous
  "never saved" signal and the client needs no separate 404 branch;
- PUT replaces the complete bounded document atomically;
- DELETE clears only the annotation document, never the generated image (204);
- no public list/search endpoint;
- no cross-design lookup by annotation UUID.

Follow the existing slash-optional `re_path` convention in
`apps/api/sitara/designs/urls.py` for runtime routes, while the OpenAPI contract
documents exactly one canonical trailing-slash path.

### 5. Add optimistic concurrency

Protect multi-tab and stale autosave writes.

Use the **`expected_revision` request field** consistently (not `If-Match`/ETag) —
it keeps the contract inside the JSON body the generated TypeScript client
already models, and needs no header plumbing through the Next.js rewrite.

Requirements:

- create begins at revision 1 (a PUT with `expected_revision: 0` creates);
- every successful replacement increments exactly once;
- a successful PUT returns **`200`** with the stored document and its incremented
  `revision` — never `201`, even on the create path, because the resource is the
  version's single annotation document and the URL never changes;
- stale revision returns `409 annotation_conflict`;
- the stored document remains unchanged on conflict;
- **lock the always-existing parent, not the not-yet-existing child.**
  `select_for_update()` only locks rows that already exist, so two concurrent
  first-saves — both carrying `expected_revision: 0`, with no document row yet —
  would each find nothing to lock and both proceed to insert. Take the row lock on
  the **`DesignVersion`** inside `transaction.atomic()` *before* looking for the
  document, exactly as `CLAUDE.md` §11 already does for concurrent first creates
  sharing one session ("serialise on the database session row"). The second
  transaction then re-reads the just-created document and correctly returns `409`.
  Additionally catch a unique-constraint `IntegrityError` on create and map it to
  the same controlled `409` — never let it escape as an unhandled 500;
- the two-tab **first**-save race (both `expected_revision: 0`, no prior row) is
  its own required test, distinct from the same-revision-on-an-existing-row case,
  and must use a real two-connection harness (`TransactionTestCase` or
  equivalent) — the default transaction-wrapped `TestCase` cannot exercise
  genuine row-lock contention between independent database sessions;
- **idempotent replay is documented and tested**: re-sending an already-applied
  payload with a now-stale `expected_revision` is a `409`, not a silent success.
  The client resolves it by reloading (§12). Replay with the *current* revision
  and identical content still increments — the revision counts writes, not
  content changes, and that is the documented behaviour;
- error responses never echo the full private annotation document — the 409 body
  carries only the code, a safe message and the server's current `revision`;
- frontend offers reload/keep-local-copy behaviour rather than silently
  overwriting.

Do not add WebSockets, CRDTs, presence, live cursors or a collaborative document
framework.

### 6. Add deterministic annotated PNG composition

A pure server-side composition function in the `sitara.media` package, built from:

- the canonical private original image read through the existing storage boundary
  (`design_image_storage()` resolved at call time via
  `django.core.files.storage.storages`, never a module-level instance);
- the persisted validated annotation document;
- deterministic server-side rendering with the pinned Pillow.

Requirements:

- no signed storage URL is accepted from the client, ever;
- output is a PNG with **stripped metadata** (no EXIF, no text chunks, no
  timestamp chunk) — identical input bytes and document must produce identical
  output bytes under the pinned Pillow;
- the original stored image is read-only and remains byte-identical; the
  composite is never written back to storage;
- enforce maximum output dimensions and a memory bound; reject and fail closed
  rather than allocating an unbounded canvas. Make the ceiling a **named,
  testable settings constant** — `ANNOTATION_RENDER_MAX_PIXELS` (and a byte
  ceiling) — not prose. Pillow compositing is a genuine memory-amplification
  step: a decoded RGBA buffer is many times the compressed file size, so "enforce
  a memory bound" without a named number does not reliably become working code.
  A test must assert the documented controlled exception is raised for a synthetic
  image above the ceiling, never an unhandled `MemoryError`. Bound the storage
  read in **time** as well as size, the way `media/delivery.py` bounds its own
  storage phase with `EXISTENCE_DEADLINE_SECONDS`;
- use fixed allowlisted line widths, marker sizes, fonts and palette colours —
  nothing derived from the request;
- **every mark is drawn twice**: a white halo stroke underneath, then the palette
  stroke on top. This is not decoration. `--color-accent` measures 3.03:1 on
  cream and far less on a maroon render; the halo is what makes a mark legible on
  arbitrary photography, and the handoff calls it out explicitly. It applies to
  strokes, selection rings and number badges alike, in the export and in the
  browser;
- render **numbered marks plus a readable note legend below the image**, in the
  handoff's export layout (§7 of the handoff README): a header line, the
  annotated image, then an `ANNOTATIONS` legend as a two-column grid of numbered
  badge + note;
- wrap and truncate notes deterministically within documented limits; a note that
  cannot fit its legend cell is truncated with an ellipsis, never overflowed;
- an empty note prints `(no note)` so numbering stays continuous;
- safely handle Unicode text. **State the limitation honestly**: the runtime has
  no `raqm`, so complex-script shaping is unavailable — Arabic/Indic text renders
  as unshaped glyphs. Record this in the ADR and the export documentation rather
  than implying full script support;
- no external font download at runtime; no network request during rendering;
- composition failures raise a narrow, typed exception the caller maps to a
  controlled code without leaking storage details;
- log exception type and correlation id only.

Reuse the pinned image-processing dependency and the shared primitives in
`sitara.image_sanitize` where possible. Do not introduce a general report/PDF
service.

A `DESIGN_ANNOTATION_RENDERER_VERSION` constant names the exact composition
behaviour, mirroring `DESIGN_IMAGE_PROCESSOR_VERSION`. Because nothing is
persisted, a bump needs no migration — but it does need a reviewed golden-bytes
test update.

### 7. Fixed output identity

- the attachment filename is **fixed and server-owned**:
  `sitara-concept-annotations.png` for the annotated composite and
  `sitara-concept.png` for the plain render. Never the design title, never a
  client value. (The prototype's title-derived filename is not portable.)
- output is `image/png`;
- the composite is never persisted to object storage in this phase.

### 8. Send the render to the owner's account email

**This replaces download entirely.** The handoff's product decision — recorded in
its README §4 — is *"There is no local download button — delivery is by email
only."* Two surfaces get it:

| Surface | Endpoint | Sends |
| --- | --- | --- |
| Concept screen (`DesignResult` / `VersionComparison`) | `POST /api/v1/designs/{design_id}/versions/{version_id}/send/` | the plain canonical render |
| Annotation workspace | `POST /api/v1/designs/{design_id}/versions/{version_id}/annotations/send/` | the annotated composite + legend |

On the Concept screen this **replaces the existing "Download image" link** in
`ResultImage.tsx`. State plainly in the ADR that removing the button is a
product/UX decision, **not** a privacy control: the image is rendered in the
browser and a user can always save it. The Phase 11/12 signed-image API is
otherwise untouched — `original.download_url` stays in the contract (unused by
the UI) rather than churning the generated client; a later phase may remove it.

Explicitly **out of scope** on the Concept screen: the handoff's "Copy brief" and
"Design history" actions. Neither is Phase 19 work.

#### 8.1 Delivery behaviour

- ownership-first 404, exactly as every other design endpoint;
- CSRF required on these POSTs;
- **the recipient is always the authenticated account's own address, read
  server-side from `request.user.email`.** A client-supplied address is never
  accepted, in any field, ever. This is the single most important rule in this
  section: an endpoint that mails an attachment to a caller-chosen address is an
  open relay;
- **an anonymous session owner has no account email.** The endpoint returns
  `409 email_recipient_unavailable` with a safe message, and the UI renders the
  action disabled with honest copy plus a sign-in link (§13). Do not invent a
  fallback, do not prompt for an address, do not silently succeed;
- rendering and sending happen in a **Celery task**, not the request. The browser
  transport aborts at 5 s (`apps/web/src/lib/transport.ts`); reading the original
  from object storage, composing a PNG and completing an SMTP round trip does not
  fit that budget reliably. The endpoint returns `202` with
  `{"send": {"status": "queued"}}`;
- the response carries no email address — the client already knows the account's
  own address from `/auth/me` and uses it for the confirmation copy;
- the task re-checks ownership and permanent-image readiness from database state
  alone; it is handed row UUIDs, never a rendered payload, address or URL;
- **the send task must be idempotent under Celery redelivery.**
  `CELERY_TASK_ACKS_LATE` and `CELERY_TASK_REJECT_ON_WORKER_LOST` are both true
  project-wide, so a worker killed *after* the SMTP hand-off but *before* its ack
  reaches the broker gets the task redelivered — and SMTP send, unlike a resumable
  render stage, is a non-idempotent external side effect. The ownership/readiness
  recheck above guards a different failure class and does nothing here. Persist a
  send-state marker and transition it under `select_for_update()` inside
  `transaction.atomic()` *before* the mail is handed over, so a redelivered task
  observes "already sent" and no-ops. A test must invoke the task body twice
  against the same version and assert exactly **one** message in the locmem outbox.
  Mirror the durable-marker discipline `generation/tasks.py` already documents;
- **a stuck claim is resolved by an explicit, bounded state machine.**
  Exactly-once delivery is not achievable across a non-transactional external
  side effect, so state what is chosen instead of implying it is solved. Three
  states, with `attempt_count`:

  | State | Meaning | On redelivery |
  | --- | --- | --- |
  | `claimed` | a worker holds this send; carries the claim timestamp | within `ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS` → no-op, another worker has it. Beyond it → the claim is stale, so re-claim and retry, incrementing `attempt_count` |
  | `sent` | terminal; the mail was handed to the backend | no-op, always |
  | `retry_exhausted` | terminal; `attempt_count` reached 2 | no-op, never sends again |

  Capping `attempt_count` at 2 is what makes "retry once" *enforced* rather than
  merely expected — two successive worker deaths cannot produce a third attempt.
  A test must drive two successive beyond-TTL redeliveries and assert the second
  does not send.
  The stale-claim retry exists because a worker that died between claiming and
  sending would otherwise leave the send permanently stuck with no path back.
  That branch deliberately prefers **at most one duplicate** over **silent
  loss** for a copy the user explicitly asked for; §8.5 records that choice as
  accepted. This is a bounded TTL check on the row, not a new Beat reconciler —
  `reconcile_stuck_generations` is the precedent for the shape only;
- **the marker stores state, counters and timestamps only.** The state, the
  `attempt_count`, the `DesignVersion` it belongs to, and timestamps — never the
  recipient address, never note text, never the rendered bytes. A durable row is
  a *worse* place to leak an address than a cache key: it survives into backups,
  dumps and any admin view. The same rule §1 applies to the annotation model
  applies here, and a test must assert the marker carries no address- or
  note-shaped field;
- the task declares an explicit `soft_time_limit`/`time_limit`, sized from a
  bounded storage read plus a bounded render budget plus `EMAIL_TIMEOUT`, the way
  `generate_design_attempt` derives its limits — a stalled storage read or slow
  SMTP handshake must not hold a worker slot indefinitely;
- the frontend **Send to account** button disables itself between click and
  response, not only after the outcome — a rapid double-click is a second,
  independent route to two real sends that an hourly throttle cannot catch.

#### 8.2 Fail-closed configuration

New settings, following the existing strict-boolean and
production-validation patterns in `apps/api/config/settings.py`:

```text
ACCOUNT_EMAIL_DELIVERY_ENABLED   default false   the capability gate
EMAIL_BACKEND                    locmem in tests, console in dev, SMTP in prod
EMAIL_HOST / EMAIL_PORT / EMAIL_HOST_USER / EMAIL_HOST_PASSWORD
EMAIL_USE_TLS / EMAIL_TIMEOUT
DEFAULT_FROM_EMAIL
ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES
ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR / _PER_DAY
ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS   the §8.1 stuck-claim retry window
ANNOTATION_RENDER_MAX_PIXELS           the §6 composition memory ceiling
```

Rules:

- a present SMTP credential must never enable sending by itself —
  `ACCOUNT_EMAIL_DELIVERY_ENABLED` is a separate, explicit operator decision,
  exactly like `LIVE_GENERATION_ENABLED`;
- when the gate is closed the endpoints return `503 email_delivery_disabled` and
  the UI hides the action behind honest copy;
- production startup fails closed on a missing, placeholder or dev-only
  `EMAIL_HOST` / `DEFAULT_FROM_EMAIL` when the gate is open; the failure message
  names the setting and a safe reason, never the rejected value;
- demo mode does not change email behaviour in either direction, and neither
  `DEMO_MODE` nor `ALLOW_PAID_AI_CALLS` can enable or disable it;
- tests and CI make **zero SMTP connections**; the locmem backend is asserted;
- a rendered attachment over `ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES` is refused with
  a controlled code rather than sent.

#### 8.3 Abuse bounds

- per-user and per-hashed-IP throttles over Redis, reusing the hashed-identifier
  pattern in `designs.upload_service.enforce_upload_throttle` /
  `generation.admission._throttle`; fail closed on a cache outage;
- exceeded → `429` with `Retry-After`;
- **a third throttle dimension keyed on a hash of the resolved recipient
  address**, independent of which account or IP is sending. Per-actor throttles
  alone do not bound what a *recipient* experiences: the account address is
  unverified (§8.5), and account creation plus demo generation are free, so
  rotating accounts or source IPs would otherwise let one person deliver
  attachment-bearing mail repeatedly to an address they merely typed at
  registration and do not control. Size the ceiling generously — it is an abuse
  backstop, not a normal-usage limit — and hash the address exactly as the other
  identifiers are hashed, never storing it in a cache key in the clear. A test
  must drive sends from two distinct accounts resolving to the same recipient and
  assert a recipient-scoped `429` while each account still has its own quota left;
- **a cache outage is not the caller's abuse.** The Redis-unavailable path returns
  a distinct `503 email_send_unavailable`, never the `429`/`Retry-After` used for a
  genuine over-limit, following `accounts.rate_limits.RateLimitUnavailable` and
  `generation.admission.AdmissionControlUnavailable`, which exist precisely so an
  infrastructure fault is never reported as the caller's own fault. Omit
  `Retry-After` there — no recovery window is known. Record as an accepted
  trade-off that a Redis outage makes account-render email unavailable until it
  recovers, while annotation editing and autosave keep working (they have no such
  dependency);
- the throttle runs **after** CSRF and ownership, so a cross-origin page cannot
  burn a victim's quota and a throttled caller still cannot distinguish an owned
  design from one that does not exist.

#### 8.4 Message content and privacy

- subject and body are **fixed server strings**. The body carries no note text,
  no design title, no brief content, no signed URL and no tracking pixel — one
  short sentence saying a private copy is attached;
- the attachment carries the content; the message does not;
- HTML mail is not required. Send plain text;
- **logs and Sentry never receive** the recipient address, the note text, the
  attachment bytes or the message body. Log only the safe operation name, the
  `DesignVersion` UUID and an exception type, matching
  `DesignVersionImagesView`'s convention;
- extend `apps/web/src/lib/sentry-scrub.ts` coverage if any new client-side error
  shape could carry an address or note text.

#### 8.5 Recorded, accepted exposures

State these plainly in the ADR — as accepted and recorded, never as removed:

- **The account address is unverified.** Registration does not verify email, so a
  user may register with an address they do not control and have their own
  renders delivered there. The throttles bound the volume; email verification is
  deferred to a later phase. This is a deliberate, recorded acceptance.
- **A private concept image leaves the system.** It reaches the configured SMTP
  provider and the recipient's mail host, both of which may retain it
  indefinitely and outside Sitara's control. Say so in the UI before the first
  send, in one plain sentence — not buried in a policy page.
- **A rare duplicate send is preferred over a silent loss.** Exactly-once
  delivery is unachievable across SMTP, a non-transactional external side effect.
  If a worker dies between claiming a send and completing it, the claim expires
  after `ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS` and the send is retried once — so
  in that narrow window the owner may receive two copies of their own render
  rather than none (§8.1). The alternative would silently drop a copy the user
  explicitly asked for. This is a deliberate, recorded acceptance, and the
  duplicate only ever goes to the account's own address.

### 9. Update retention and observability

- annotation documents cascade with retained/deleted designs;
- retention purge tests prove no orphan annotation rows remain;
- annotation note text is never logged or sent to Sentry;
- request bodies are not captured;
- metrics may count save/send success or failure but must not contain design ids,
  email addresses or note content;
- keep designs private by default.

---

## Part C — Frontend annotation workspace

Build this screen to the handoff. Every measurement, colour and string below is
taken from `Sitara Annotation.dc.html` unless marked as an addition.

### 10. Route and entry point

Add an **Annotate** action under the render on the private result view,
alongside the new **Send to account** button, replacing the current "Download
image" link:

```text
apps/web/src/app/design/[designId]/result/[versionId]/annotate/page.tsx
```

The route is private and ownership-backed by the API; the Next.js middleware is a
navigation optimisation only, never the authorization boundary.

The workspace is a `"use client"` feature under
`apps/web/src/features/annotations/` with its own stylesheet partial
(`apps/web/src/app/styles/annotations.css`), imported the way
`questionnaire.css` and `generation.css` already are.

**Header** — one row, `padding: var(--space-3) var(--space-4)`, wrapping:

| Element | Detail |
| --- | --- |
| Back link | `‹ Concept` — chevron + label, pill hover `--color-accent-100`, links to the result route |
| Divider | 1px × 24px `--color-neutral-300` |
| Title | `<h1>` "Annotate this concept", `--font-display`, 600, 23px, line-height 1.1 |
| Privacy tag | `.tag.tag-accent-2` — "Private — only you" |
| Save pill | `role="status"`, see §14 |
| Overlay toggle | `.btn.btn-ghost.btn-icon` 38×38, eye icon, `aria-pressed`, title "Hide annotations (H)" / "Show annotations (H)" |
| Send action | `.btn.btn-secondary` — paper-plane icon + "Send to account" |

Also display — these are additions this phase requires, which the handoff does not
draw — the same version identity (Original / Refined), demo-or-live historical
label and immutable-image explanation that
`apps/web/src/features/results/DesignResult.tsx` already renders on the result
page, carried through so the workspace states plainly which version is being
marked up and that its image cannot change. Plus the annotation count, in the
panel heading (§12).

Do not imply that annotations modify the AI-generated design or feed
automatically into refinement. Do not ship the `⋮` prototype menu.

### 11. Canvas and responsive image overlay

**Canvas region** — `flex: 1`, `background: var(--color-neutral-200)`,
`border-radius: var(--radius-lg)`, `overflow: hidden`, contents centred,
`padding: var(--space-4)`, `min-height: 600px`. The image sits on a
`--shadow-lg` card at `border-radius: var(--radius-md)`.

Implement the visual layer as an **inline SVG overlay** positioned over the
rendered image.

Requirements:

- **the image box is sized from the version's own intrinsic dimensions.** Set
  `aspect-ratio: {image_width} / {image_height}` and cap the height
  (`height: min(64vh, 620px)` as drawn). Never `object-fit: cover` — the
  prototype's fixed 3:4 cover box crops, and a cropped image silently
  invalidates every stored coordinate;
- the overlay `<svg>` uses `viewBox="0 0 {image_width} {image_height}"`,
  `preserveAspectRatio="xMidYMid meet"` and `overflow: visible`, absolutely
  positioned over the image at `inset: 0`. Normalised geometry maps to
  `x * image_width`, `y * image_height`;
- stroke widths, badge radii and handle sizes are constants in that space, scaled
  from the handoff's 600×800 reference by `min(W, H) / 600`, so a mark reads at
  the same apparent size on any image;
- coordinate transforms for **pointer input** are derived from the rendered
  bounds (`getBoundingClientRect()`, tracked with `ResizeObserver`) — never from
  a hard-coded size;
- resizing never changes stored normalised geometry;
- zoom and pan apply `transform: scale()` / translation to the **image and
  overlay together**, so marks stay pinned to the garment and geometry is never
  mutated;
- pointer and touch creation work (`PointerEvent`, with `touch-action` set so a
  drawing gesture is not stolen by scrolling);
- selection handles remain usable at zoom;
- marks remain aligned when the signed image URL refreshes — the overlay is
  independent of the `<img>` element's `src`;
- image-load failure does not destroy local unsaved notes; the overlay and the
  list stay live over the failed-image placeholder;
- the original image cannot be dragged or selected accidentally while
  annotating (`draggable={false}`, `user-select: none`, `-webkit-user-drag: none`);
- no client-side mutation of the source image blob; no `<canvas>` reads the
  private original;
- no data URL containing the private original is placed in localStorage or logs.

**Mark rendering** — as drawn in the handoff, in this order per mark:

1. white halo stroke (`--color-on-accent`, width ≈ 7 for lines, 5–6 for rings and
   rectangles);
2. palette stroke on top (width ≈ 2.75);
3. selection ring when selected: outer circle r ≈ 20–21, white 5 + palette 2.5.
   A selected rectangle additionally thickens its own stroke to 4 and shows four
   10×10 white/palette corner handles;
4. number badge: white circle r ≈ 16, palette circle r ≈ 13.5, then the numeral
   in `--color-on-accent`, bold, 13.

Per type: **pin** = badge circle with a downward triangular tail; **arrow** =
line plus a filled triangular head outlined in white (`paint-order: stroke`);
**rectangle** = rounded rect (`rx` ≈ 10); **freehand** = a rounded-cap polyline
through the stored points.

**Note chip** — when a mark is selected, overlays are visible and the row is not
being edited, float a chip beside the mark: `--color-bg`, `radius 10px`,
`--shadow-md`, `padding: 7px 12px`, 12.5px, `max-width: 220px`, content
`**{n} · {Type}**` then the note.

**Empty state** — a centred card on the canvas: `width: 280px`, `--color-bg`,
`--radius-lg`, `--shadow-md`, pin glyph in an `--color-accent-100` circle, then
"Nothing marked yet" (`--font-display`, 600, 20px) and "Pick a tool on the left,
then click anywhere on the render. Every mark takes a short note."

**Overlays-hidden banner** — while hidden, a pill at top-centre of the canvas:
`--color-neutral-900` ground, `--color-neutral-100` text, "Overlays hidden — the
eye brings them back". Marks are hidden, never deleted.

**Zoom cluster** — floating bottom-right of the canvas, `--color-bg` pill with
`--shadow-md`: `−` / percentage label / `+` / hairline / fit-to-view. Titles
"Zoom out (−)", "Zoom in (+)", "Fit to view (0)". Zoom in ×1.25 capped at 300%;
zoom out ×0.8 floored at 60%; label `Math.round(zoom * 100)%`. While zoom ≠ 100%,
a hint at bottom-left: "Drag with Select / Pan to move around · marks stay pinned
to the garment".

### 12. Tools, editing and the annotation list

**Tool rail** — a vertical `role="toolbar"` pill card on the **left**,
vertically centred: `--color-bg`, `--shadow-md`, `border-radius: 999px`,
`padding: 8px 6px`, 4px gaps, 40×40 circular buttons. Active tool =
`--color-accent` ground with `--color-on-accent` glyph; inactive = transparent
with `--color-neutral-800`; hover `--color-accent-100`. Each button carries
`aria-label` and `aria-pressed`.

Tools, in order, with the handoff's shortcuts:

| Tool | Key | Behaviour |
| --- | --- | --- |
| Select / Pan | `V` | select, move, drag the canvas |
| Pin | `P` | one point |
| Arrow | `A` | drag start → end |
| Rectangle | `R` | drag corner → corner |
| Freehand | `F` | bounded stroke |
| Note | `N` | focuses the note editor of the selected (or most recent) mark |

`Note` is deliberately **not** a fifth geometry type — the schema has exactly
four, and a "note" without a mark has nowhere to point. It is a shortcut to the
editor, which is what the handoff's rail implies and what keeps the schema
closed.

**Additions the handoff does not draw** (required by this phase, drawn in the
rail's own style below a hairline divider): **Undo** and **Redo** for the current
unsaved editing session, labelled with their shortcuts
(`Ctrl`/`⌘`+`Z`, `Ctrl`/`⌘`+`Shift`+`Z`), disabled when the stack is empty.

**Annotation list panel** — right side, `width: 300px`, `--color-bg`,
`--radius-lg`, `--shadow-sm`, `padding: var(--space-3)`, full height.

- heading: `<h2>` "ANNOTATIONS · {count}" — 12px, `letter-spacing: .09em`,
  uppercase, 700, `--color-accent-700`;
- **Clear all** text button beside it, shown only when rows exist;
- empty state: `--color-neutral-100` block, "Marks you place will list here,
  numbered to match the canvas." (no "Restore sample marks" — prototype only);
- rows, ordered by `created_order`: number badge (22px, `--color-accent` ground,
  `--color-on-accent` numeral), type label (11px, uppercase, `.07em`,
  `--color-accent-2-800`), pencil (edit) and trash (delete) 26×26 icon buttons,
  then the note text (13.5px, `--color-neutral-900`);
- selected row: `--color-accent-100` ground plus `--shadow-sm`;
- inline editing: a labelled `<textarea>` (3 rows, white ground,
  `--color-accent-300` border, radius 10), a live `{n}/140` counter, then
  **Cancel** (`.btn.btn-ghost`) and **Save** (`.btn.btn-primary`);
- **palette control** (required by this phase; the handoff draws no picker): a
  labelled radio group of three swatches inside the editor — `terracotta`
  (`--color-accent`), `sage` (`--color-accent-2`), `ink`
  (`--color-neutral-900`). Each swatch has a visible text label, never colour
  alone, and every colour keeps the mandatory white halo so contrast holds on any
  render;
- footer note, `--color-accent-2-100` ground / `--color-accent-2-900` text:
  "These marks are your private worktable. They never appear on shared concept
  views — only in the PNG sent to your account." Extend it with the §8.5
  sentence about the image leaving the system by email.

Selecting a row selects the canvas mark and vice versa; the two stay
synchronised in both directions.

**Clear all** opens a confirm dialog: title "Clear all {count} annotations?",
body "Every pin, arrow, shape and note on this concept will be removed. This
can't be undone.", actions **Keep them** (`.btn.btn-ghost`) and **Clear all**
(`.btn.btn-primary` on `--color-accent-800`). `role="alertdialog"`, focus
trapped, Escape closes, focus returns to the trigger — follow
`features/questionnaire/InfoDrawer.tsx`. Only the conflict and clear-all
dialogs are modals; the unsaved-leave warning is an anchored popover (§14).

### 13. Accessible non-canvas representation

The visual overlay cannot be the only way to understand or edit annotations. The
list panel above is that alternative, and it must independently support:

- ordering by `created_order`;
- type and number announced (`aria-label` of the form
  `Annotation 3, rectangle: Champagne hem border is too narrow here` — or
  `…, rectangle, no note`);
- note editable through a labelled text field;
- palette selectable through labelled controls;
- delete action;
- focus/select of the corresponding visual mark;
- keyboard nudge controls for position;
- a numeric or descriptive geometry summary (e.g. "centred at 50%, 18% from the
  top");
- validation errors associated with the correct item, using the existing
  `.field-error` / `aria-describedby` pattern.

Keyboard requirements — the handoff's own focus pass is
**toolbar tools → canvas marks → note rows**:

- toolbar reachable in logical order (roving tabindex within the `role="toolbar"`);
- tool state announced via `aria-pressed`;
- `Enter` on a focused mark or row edits its note;
- `Escape` returns to select mode, cancels an in-progress mark, or closes the
  editor — in that order of specificity;
- `Delete`/`Backspace` removes a selected item **only** when focus is not in a
  text field;
- arrow-key nudge of 0.5% of the image's smaller edge; `Shift`+arrow nudges 2%;
  both documented in the on-screen instructions;
- no keyboard trap anywhere, including inside the dialogs;
- visible `:focus-visible` at all zoom levels — a 2px `--color-accent-700` ring,
  with a white underlay for on-canvas focus so it survives dark photography.

Add concise on-screen instructions, not a gesture-only tutorial.

### 14. Autosave and conflict UX

Use the existing typed API and TanStack Query patterns (Phase 12's
generation-progress flow is the precedent). Do not expand TanStack Query into a
general data layer.

**Save pill** — one `role="status"` pill in the header, covering all five
required states in the handoff's single control:

| State | Ground / text | Content |
| --- | --- | --- |
| Saved | `--color-accent-2-100` / `--color-accent-2-900` | tick + "Saved" |
| Saving | `--color-neutral-100` / `--color-neutral-800` | spinner + "Saving…" |
| Unsaved | `--color-neutral-100` / `--color-neutral-800` | "Unsaved changes" |
| Save failed | `--color-accent-100` / `--color-accent-800` | warning + "Couldn't save" + a **Retry** button |
| Conflict | `--color-accent-100` / `--color-accent-800` | "Conflict" + a **Reload** button, and the modal below |

The system has no red, and the handoff is explicit that errors stay in the
terracotta ramp on this screen. `--color-bad` remains in use for form validation
elsewhere; do not introduce it here.

Behaviour:

- local edits update immediately;
- debounced autosave after a reasonable idle period (~800 ms–1 s), plus an
  immediate save on blur of the note editor;
- only one save in flight per document; later edits queue behind the current save
  and coalesce into one follow-up write;
- retry is explicit after network failure — never an automatic retry loop;
- navigation with unsaved changes produces a controlled warning: an **anchored
  popover under the back link** (`role="alertdialog"`, aria-label "Unsaved
  changes"), body "Your last edits haven't saved yet. Leaving now will lose
  them.", actions **Stay** (`.btn.btn-secondary`) and **Leave anyway**
  (`.btn.btn-ghost`). Back a hard browser navigation with `beforeunload`;
- a successful server response replaces the local revision;
- a `409` never silently discards either copy. Open the conflict modal:
  title "Newer notes elsewhere", body "This concept was annotated somewhere else
  since your last save. Reload to see the latest marks, or discard this tab's
  unsaved changes — nothing is overwritten silently.", actions **Discard my
  changes** (`.btn.btn-ghost`) and **Reload latest** (`.btn.btn-primary`). Do not
  copy the prototype's hard-coded "two minutes ago" — either compute it from the
  server's `updated_at` or omit it.
  The copy deliberately does **not** claim "another tab", which the prototype
  asserts and the client cannot actually know: by §5's own replay rule, an
  ordinary single-tab lost-response retry (the write landed, the response did not,
  so the client never advanced its revision) produces the identical `409`. Say
  what is true — the stored document moved on — not a cause you cannot
  distinguish;
- **entering the conflict state suspends autosave until the user resolves it.** No
  further automatic PUT fires while the modal is open, and any coalesced
  follow-up write pending when the `409` arrives is folded into the
  conflict-resolution flow rather than independently re-submitted. Without this, a
  flaky connection re-fires a doomed write on every debounce tick and keeps
  re-opening the modal mid-edit — the wedged-conflict-loop this section exists to
  prevent. Local edits keep accumulating unsaved meanwhile, which is safe because
  they are never silently discarded;
- signed image URLs and annotation documents remain memory-only;
- **no annotation data in localStorage, sessionStorage or IndexedDB**;
- page refresh loads the persisted server document.

**Send to account** — the header button posts to the annotated-send endpoint and
flashes its own state, matching the Concept screen's pattern:

- disabled with honest copy when the owner is anonymous
  (`409 email_recipient_unavailable` — "Sign in to send this to your email",
  with a sign-in link) or when the capability gate is closed
  (`503 email_delivery_disabled`);
- on `202`: label flashes "Sent to your email ✓" for ~2.2 s, announced through a
  polite live region, then returns to "Send to account";
- on `429`: a controlled message with the retry hint, never a silent failure;
- the confirmation may name the account's own address (the client already holds
  it from `/auth/me`); nothing about the send is written to storage or logs.

### 15. Mobile and responsive behaviour

Support desktop and tablet fully. Below the layout's breakpoint:

- the tool rail becomes a horizontal toolbar above the canvas; advanced drawing
  tools (arrow, rectangle, freehand) may collapse into an accessible disclosure,
  keeping pin, select, undo and the note editor always visible;
- keep basic pin, selection, note editing, hide/show and zoom usable;
- do not horizontally overflow at any supported width;
- the `−` / `+` / fit buttons remain the primary zoom method; pinch zoom is
  additive, never the only way;
- retain the annotation list below the image;
- document any deliberate mobile limitation honestly — in particular, freehand
  drawing on a small touch screen is coarse, and that should be said rather than
  papered over.

---

## OpenAPI and generated client

Add explicit request/response serializers for:

- annotation point/geometry variants (one per item type — do not model geometry
  as an untyped `JSONField` in the contract);
- annotation item;
- annotation document (with `revision`, `image_width`, `image_height`);
- save request with `expected_revision`;
- controlled error responses (`annotation_conflict`, `annotation_invalid`,
  `design_image_not_ready`, `email_recipient_unavailable`,
  `email_delivery_disabled`, `email_send_unavailable`);
- the send-queued `202` response.

Requirements:

- stable operation ids (`designs_version_annotations_retrieve` / `_replace` /
  `_delete`, `designs_version_send_render`, `designs_version_send_annotated`);
- canonical trailing-slash paths;
- no bearer/JWT scheme;
- CSRF header documented for PUT/DELETE/POST;
- regenerate schema and TypeScript deterministically;
- do not hand-edit generated files.

The PNG is delivered by email, not over HTTP, so there is **no** binary
`image/png` response to document in this phase.

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
- item, point, note (140) and payload (256 KiB) limits;
- duplicate item ids/order rejection;
- unknown fields rejected;
- palette allowlist enforced;
- dimension mismatch rejected;
- note HTML remains inert text;
- **a refined version's annotation document is independent and never inherited**:
  annotate version 1, refine the design, and assert version 2's document comes
  back empty (`revision: 0`, `items: []`) — annotations are never copied from
  parent to child, and clearing one version's document never touches the other's.

### API and ownership

- anonymous owner can read/write their own annotation;
- authenticated owner can read/write their own annotation;
- anonymous-to-authenticated design promotion preserves annotation access;
- second browser/account receives indistinguishable 404;
- mismatched design/version receives 404;
- a version with no permanent image returns `409 design_image_not_ready`;
- CSRF enforced on PUT/DELETE/POST;
- `no-store` headers on every response;
- private fields absent from every payload;
- stale revision returns controlled 409 and leaves the stored document unchanged;
- **the 409 conflict body carries only the code, a safe message and the server's
  current `revision`** — assert it contains no `items`, no `schema_version` and
  no note text, so a conflict can never echo the stored private document back;
- concurrency test admits only one of two same-revision writes **on an existing
  document**;
- **a separate two-tab first-save race test**: both PUTs carry
  `expected_revision: 0` with no prior document row, driven from two real
  independent database connections under `TransactionTestCase` (or equivalent) —
  the default transaction-wrapped `TestCase` cannot exercise genuine row-lock
  contention. Exactly one succeeds with `revision: 1`; the other gets
  `409 annotation_conflict`, never a 500;
- delete clears annotations only, never the generated image;
- no endpoint exposes a public annotation id lookup.

### Composition

- deterministic PNG bytes for a fixed synthetic image/document (golden bytes);
- marks align to normalised coordinates;
- note legend rendered, including `(no note)` for an empty note;
- long notes wrap and truncate deterministically;
- metadata absent from the output;
- the composite is `image/png` and carries the fixed server-owned filename —
  assert the filename is exactly `sitara-concept-annotations.png` (and
  `sitara-concept.png` for the plain render) and is never derived from the design
  title or any client value;
- dimension/memory limits enforced;
- storage failure is controlled, not an unhandled exception;
- the original stored object is byte-identical afterwards;
- no external network call during rendering.

### Email delivery

- gate closed → `503 email_delivery_disabled`, nothing queued;
- anonymous owner → `409 email_recipient_unavailable`, nothing queued;
- authenticated owner → `202` with exactly the documented body
  `{"send": {"status": "queued"}}` (assert the shape, not just the status code),
  and exactly one message in the locmem outbox;
- the recipient equals `request.user.email` and **no** client-supplied address is
  ever honoured. Assert this across the plausible attack surface, not one field:
  `recipient`, `to`, `email`, `cc` and `bcc` in the JSON body **and** the same
  names as query parameters. Every one of them is ignored or rejected, and the
  message still goes only to the account's own address;
- fixed filename and `image/png` attachment;
- subject/body contain no note text, design title or URL;
- oversize attachment refused;
- throttle returns `429` with `Retry-After`;
- **the cache-outage path is distinguishable from an over-limit**: a raised
  cache fault yields `503 email_send_unavailable` with no `Retry-After`, never
  the `429`;
- **a recipient-scoped throttle test**: two distinct accounts resolving to the
  same recipient address hit a recipient-scoped `429` while each account still
  has its own per-user quota remaining;
- **the send task is idempotent under redelivery**: invoking the task body twice
  for the same version leaves exactly **one** message in the outbox;
- **a stuck `claimed` marker beyond its TTL retries exactly once**, and a
  `claimed` marker inside its TTL no-ops. Prove the cap with the negative case,
  not only the positive one: drive **two successive** beyond-TTL redeliveries for
  the same version and assert the **second does not send** (`attempt_count`
  capped at 2, marker terminal at `retry_exhausted`). Asserting only "one retry
  sends" would pass against an implementation that increments `attempt_count`
  without ever gating on it — which is the unbounded-duplicate hazard this rule
  exists to close;
- foreign design → 404 before any send;
- no SMTP backend is constructed anywhere in the suite, asserted two ways: the
  resolved `EMAIL_BACKEND` setting **is** the locmem backend under test, and no
  test constructs `django.core.mail.backends.smtp.EmailBackend`.

### Retention

- purging an expired design removes its annotation documents with no orphan rows.

### Frontend

- tool selection and mark creation;
- coordinate transform under responsive resize;
- note edit/delete;
- palette selection;
- undo/redo;
- autosave sequencing (debounce, single in-flight, queued follow-up);
- network failure and explicit retry;
- 409 conflict handling — neither copy discarded;
- unsaved-navigation warning;
- annotation list and visual selection stay synchronised;
- keyboard nudge, Enter-to-edit and Escape behaviour;
- signed-image refresh does not move marks;
- send action: disabled states, 202 flash, 429 message;
- nothing is written to `localStorage`, `sessionStorage` **or IndexedDB** — all
  three, since the requirement is that state stays memory-only;
- axe checks for the toolbar, canvas region, note list, both dialogs, the
  unsaved-leave popover and the send action.

### E2E

Extend the **Phase 17** zero-cost demo suite (`apps/web/e2e/`):

1. create/generate a synthetic demo design;
2. open the annotation workspace;
3. add a pin and a rectangle;
4. enter notes;
5. reload and confirm persistence;
6. queue a send as an authenticated owner and confirm the flash (with the locmem
   backend, assert the queued outcome — never a real SMTP connection);
7. confirm an anonymous owner sees the disabled send action with its explanation;
8. confirm another browser cannot access the workspace;
9. confirm the original result image remains unchanged.

Record the additions in `apps/web/e2e/README.md`.

## Commands and validation

Run the repository's own commands (`CLAUDE.md` §20 and
`.claude/phase-council.json`), on the host for the frontend:

```bash
docker compose build api
docker compose up -d
docker compose ps
docker compose config

docker compose exec api python manage.py check
docker compose exec api python manage.py makemigrations --check --dry-run
docker compose exec api python -m pip check
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .

docker compose exec api python manage.py spectacular \
  --format openapi-json --file openapi/schema.json --validate --fail-on-warn
npm --prefix apps/web run generate:api
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web test -- --run
npm --prefix apps/web run build

docker compose exec api python manage.py install_demo_asset_pack --dev-synthetic
npm --prefix apps/web run e2e
npm --prefix apps/web run e2e:visual
```

Run schema/client generation twice and prove no second-run diff.

Deployment smoke tests are **not available** — Phase 18 is skipped. Say so rather
than claiming they passed.

## Manual checkpoint

With `DEMO_MODE=true` and no provider credentials:

1. Generate a synthetic demo design.
2. Add each supported annotation type.
3. Edit notes and change palettes through the annotation list.
4. Resize the browser and zoom/pan; verify alignment at several sizes and zooms.
5. Navigate away during an unsaved edit and confirm the anchored warning.
6. Reload and verify persistence.
7. Open the same annotation in a second tab, create a conflict and confirm the
   modal appears and nothing is silently overwritten.
8. With the email gate open and a console/locmem backend, send an annotated PNG
   and verify numbered marks and the note legend in the captured message.
9. Confirm an anonymous session sees the send action disabled with its
   explanation.
10. Compare the original stored design image and confirm it is byte-identical.
11. Repeat the core flow keyboard-only, following the toolbar → marks → rows
    focus order.
12. Verify another browser/account receives 404.
13. Confirm logs and Sentry contain no note text, email address or private image
    data.
14. Run the retention purge against synthetic expired data and confirm
    annotations are removed with their design.

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
- **a local download button** (superseded by email delivery — see §8);
- email address verification, bounce handling or a mail-delivery dashboard;
- HTML/templated marketing email;
- public galleries;
- offline annotation editing;
- unrestricted colours, fonts or uploaded stickers;
- the handoff's `⋮` prototype menu, "Restore sample marks", `panelSide` /
  `exportLegend` props, "Copy brief" or "Design history";
- paid provider calls.

## Documentation and decision record

Add **two** ADRs — the annotation workspace and the email capability are
separable decisions and should be reviewable separately:

**ADR 0020 — private stylist annotation workspace:**

- annotations as a separate overlay document;
- ownership inherited through DesignVersion;
- one document per immutable version;
- normalised coordinates;
- strict bounded JSON schema (140-character notes, allowlisted palette);
- revision-based optimistic concurrency via `expected_revision`;
- no collaboration in v1;
- deterministic server-side PNG composition, and the `raqm`/complex-script
  limitation stated plainly;
- no mutation of the original image;
- no localStorage;
- retention and privacy behaviour;
- accessible annotation-list alternative;
- the mandatory white halo as an accessibility requirement, not styling;
- deferred sharing and AI-assisted interpretation.

**ADR 0021 — account render delivery by email:**

- why delivery replaced download (the handoff's product decision), and that
  removing the button is UX, not a privacy control;
- recipient is always the authenticated account's own address, never client-supplied;
- anonymous owners have no recipient and are told so;
- fail-closed `ACCOUNT_EMAIL_DELIVERY_ENABLED` gate plus production config
  validation;
- asynchronous Celery delivery and why (the 5 s browser transport budget), and
  the send-state marker's `claimed`/`sent`/`retry_exhausted` machine that makes
  it idempotent under Celery redelivery;
- throttles — per-user, per-hashed-IP **and per-hashed-recipient** — and
  attachment bounds;
- the single choke point: `media/account_delivery.py` takes the user row, never
  an address string, so a caller-chosen recipient is unexpressible;
- **recorded, accepted exposures — all three of §8.5**: the unverified account
  address; the private render leaving the system to an SMTP provider and
  recipient mail host that may retain it; and the bounded single duplicate
  preferred over a silent loss when a worker dies mid-send. Recorded as
  accepted, never described as removed;
- deferred: email verification, bounce handling, delivery receipts.

Update:

- `docs/phases/PHASES.md` (including that Phase 18 was skipped);
- `CLAUDE.md` — §3 delivered-phase state, §5's stale `design/sitara-handoff/`
  path, §7's settings list, and a §13/§14 note that a render now leaves the
  system by email;
- `.env.example` with the new email settings as placeholders only;
- privacy documentation;
- runbook/storage notes for the export's resource considerations and the mail
  configuration;
- OpenAPI and generated client documentation;
- `apps/web/e2e/README.md` coverage notes.

## Completion report

Report:

- starting and ending commit;
- model/migration details;
- annotation schema and bounds;
- endpoint list and ownership behaviour;
- concurrency strategy;
- frontend implementation and dependency choices (and confirmation that no new
  frontend dependency was added);
- composition implementation, renderer version and resource limits;
- email configuration, gating, throttles and the recorded exposures;
- accessibility behaviour, including the keyboard focus order actually shipped;
- retention/logging/Sentry verification;
- fidelity notes against the handoff — what was followed, and every deliberate
  divergence with its reason;
- tests and commands run;
- manual checkpoint results;
- council findings and resolutions;
- explicit confirmation that original image bytes remain unchanged;
- explicit confirmation of zero AI/provider calls and zero SMTP connections in
  tests and CI;
- each commit SHA and draft PR URL.
