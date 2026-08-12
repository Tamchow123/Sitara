# Sitara Phase 22 — The reference step becomes the customer's own photographs, captured in store

**Status:** specification only. Nothing here is implemented. Written to be picked
up on its own branch. Requirements come from the project owner; their words are
recorded inline as **Owner decision** so a later reader can tell a decision from
an inference. All eight open questions were answered on 2026-08-11 — the answers,
and what each changed, are recorded at the end.

> **Amended after delivery (2026-08-12).** This document records what was
> commissioned and is left as written. One part of it has since been narrowed by
> the owner: the reference step's "three ways in" are now one — the QR handoff —
> and the code shows itself rather than waiting behind a button. The iPad's
> camera capture, file picker, rights checkbox and disclosure copy are removed.
> See the Amendment section of
> [ADR 0026](../decisions/0026-in-store-reference-capture.md) for the decision,
> what it deliberately does not change, and what it costs.

## Main objective

Two changes to one screen, plus one thing the owner did not ask for that the
same deployment model forces us to answer.

1. **The curated catalogue disappears from the questionnaire.** The reference
   step stops offering Sitara's own approved inspiration assets. What remains is
   the customer's own photographs, and only those.
2. **Getting those photographs onto the device is redesigned for the real
   setting** — an iPad on a shop floor that a customer is using — rather than the
   current desktop file picker, which assumes the pictures are already on the
   machine doing the browsing. They are not. They are on the customer's phone.
3. **A shop-floor iPad breaks an assumption every ownership rule rests on** —
   that one browser equals one person. The owner has since settled who the
   account belongs to: **the boutique, and the designs are the store's**, which
   the customer never registers for. That settles ownership and leaves a narrower
   question the product still has to answer — a shared screen should not put the
   previous customer's concepts and annotation notes in front of the next one.
   See Part D. This was not requested and is the strongest reason to take this
   phase before the others.

> **Owner decision:** "remove the inspiration images and instead leave that part
> of the questionnaire only for allowing users to upload their own image for
> inspiration" and "how the app will work will be an ipad in a store which
> customers will come in and use so we need a smart and effective way of getting
> photos from the customers onto that ipad or straight to sitara."

## What the catalogue actually is, before removing it

This matters, because the phrase "remove the inspiration images" could mean
anything from hiding a grid to deleting an app, and the two ends of that range
have very different consequences.

`sitara.catalogue` is the ADR 0006 rights-controlled inspiration catalogue: a
`UsageRights` record with a rights basis, holder, evidence reference, expiry and
four separate usage permissions; an `InspirationAsset` with sanitised image
facts under all-or-none database constraints; locking verification, ingest,
approval and retirement services; a staff-only Pillow ingest pipeline; and three
identity-free public endpoints sharing one `publicly_eligible()` queryset.
Fourteen non-test backend modules reference it.

**It has never held a single genuinely rights-cleared image.** PHASES.md Phase 5B
records the manual checkpoint as still pending: *"Pipeline verified end-to-end
with a locally generated synthetic image; the three-real-image checkpoint remains
pending until genuinely rights-cleared photographs are available."* That
checkpoint has been outstanding since Phase 5B and is still outstanding today.
So in production the catalogue grid renders `"No inspiration images are available
yet."` and always has.

That is the honest case for removal: this is not a working feature being taken
away, it is an unfinished one being closed. It is also the reason removal is
cheap — there is no user data to migrate, because there are no approved assets
and therefore no `DesignInspiration` rows pointing at them.

### What must NOT be deleted

- **`DesignVersion.inspiration_context` / `_schema_version` / `_sha256`.**
  CLAUDE.md §13 is explicit: this is *"immutable historical audit data"* and a
  later retirement or revocation *"must never rewrite an existing design's stored
  snapshot or acknowledgement."* Any design that ever generated with a snapshot
  keeps it, and the result API keeps rendering the acknowledgement from it. This
  phase adds no migration that touches those columns.
- **`DesignInspiration` rows**, if any exist in any environment. The FK to
  `InspirationAsset` is `PROTECT`; deleting assets under existing selections
  would raise, and forcing it would corrupt audit history.
- **The `catalogue` app itself.** Removing it means a destructive migration
  against `PROTECT` relations for no benefit. **Decided: retire** the user-facing
  path and leave the app dormant and staff-only, not delete it.

### What is removed

| Surface | Action |
| --- | --- |
| `InspirationPicker.tsx`'s catalogue grid, unavailable-selection placeholders and shared-budget arithmetic | Deleted. The screen becomes the upload component alone. |
| `GET /api/v1/inspiration-assets/`, `/<uuid>/image/`, `/<uuid>/thumbnail/` | Retired. Nothing reaches them once the picker is gone, and leaving a public endpoint alive with no caller is a surface with no owner. Contract change — regenerate the schema. |
| `Design.inspiration_asset_ids` as a **writable** draft field | Removed from the write path. A request naming it is a controlled 400 unknown-field rejection (§15), not a silent ignore. |
| `selected_inspirations` in the design detail payload | Kept **read-only** for historical designs; empty for every new one. Do not remove it — the frontend's runtime shape validator and the gallery both read design detail. |
| Curated presets inside `generation/reference_images.py` | **Decided: kept** as dead-but-correct code guarded by "no selections exist", so the live `publicly_eligible()` re-check still protects any historical design that holds a selection. |
| `generation/inspiration_context.py` snapshot **building** | Effectively never fires (no selections). The module stays, because `result.py` still reads persisted snapshots. |

**The catalogue's rights model does not transfer to uploads, and this phase must
not let anyone think it does.** CLAUDE.md §2: a catalogue asset needs
staff-verified, unexpired, evidenced rights; a user's own upload rests on *"a
bounded per-upload self-affirmation, which is weaker and is never to be presented
— in code, copy or documentation — as verified rights."* Removing the stronger
model from the product does not upgrade the weaker one. Every piece of copy this
phase writes must keep that distinction visible.

---

## Part A — The reference step, rebuilt

### 1. What the screen becomes

One heading, one disclosure, one affirmation, three ways to add a photograph,
and a list of what has been added. The cap stays at
`settings.MAX_INSPIRATION_IMAGES` (3), enforced server-side under a row lock in
`upload_service` exactly as now — it simply stops being a *shared* budget,
because there is nothing else drawing on it.

The three ways in, in the order they should appear on an iPad:

1. **Send from your phone** — a QR code the customer scans. Part B. This is the
   headline and should be the visually dominant option.
2. **Take a photo now** — opens the iPad's rear camera directly.
3. **Choose a file** — the existing picker, kept for desktop and for a stylist
   working from a file the shop already holds.

> **Owner decision (2026-08-11):** "most customers will have their inspiration
> pictures on their own devices so will need an intuitive way of getting it from
> their device to the app."

That sets the bar for Part B and it is worth stating plainly: **the phone handoff
is the primary path, not a fallback.** A customer arriving with a screenshot from
Instagram or a photo of her sister's wedding is the normal case, not the edge
case. If the handoff is fiddly — a code to type, a scan that fails in shop
lighting, an unclear "now what?" after the scan — the whole reference step fails
for most customers, and the camera and file picker will not save it.

Concretely, this means the handoff is judged against the following, and each is a
test in §Automated tests rather than an aspiration:

- **No typing.** Scanning the QR opens the upload page directly; a short code is
  a fallback for a phone whose camera app will not scan, not the main route.
- **No app, no sign-in, no account** on the phone. The grant *is* the credential
  (§5).
- **One tap from landing to the phone's own photo library**, because that is
  where the picture already is. The phone page's primary control is "Choose from
  your photos", not a camera.
- **The iPad shows the arrival within a couple of seconds** and says whose turn
  it is next (§7). A customer who has uploaded and does not see it appear will
  upload again.
- **It works on an old phone.** The page must function on a several-year-old iOS
  or Android browser, because that is what walks into a shop.

### 2. Camera capture is nearly free — do it first

`<input type="file" accept="image/*" capture="environment">` opens the camera
directly on iPadOS and Android, and degrades to the ordinary picker on desktop.
It reuses the entire existing pipeline — same endpoint, same
`upload_processing.py`, same `USER_UPLOAD_MAX_BYTES` (15 MB) and
`USER_UPLOAD_MAX_IMAGE_PIXELS` (40 MP) bounds, same sanitisation to one clean
WebP with all metadata stripped.

Two things to get right:

- **`capture` is a hint, not a guarantee.** Some browsers ignore it. The control
  must be labelled by what it does ("Take a photo") and must still behave
  correctly if a file picker opens instead.
- **A modern phone camera photo can exceed the pixel ceiling.** A 48 MP sensor
  at full resolution is over `USER_UPLOAD_MAX_IMAGE_PIXELS`. The existing
  rejection is correct and must stay correct, but the message a customer sees
  cannot be a bare technical refusal — it needs to say what to do instead.
  Test this with a real oversized image, not a synthetic one shaped to pass.

### 3. Where the affirmation lives now

Today's affirmation checkbox sits above the picker and stays ticked between
uploads, with copy that says so. That is right for one person at one device. It
is wrong the moment the person choosing the photograph is not the person looking
at the iPad — see §6.

---

## Part B — The phone handoff

### 4. The shape

The iPad shows a QR code. The customer scans it with their own phone. Their
phone opens a small page scoped to that one design, shows the ADR 0019
disclosure, takes their affirmation, and lets them pick from their camera roll or
take a photo. The iPad's list updates as photographs arrive.

Nothing about this changes what happens to the image afterwards: same endpoint
semantics, same sanitisation, same private storage, same three-slot cap, same
deletion with the design.

### 5. The grant is a bearer credential, and the ADR must say so

A URL in a QR code is a **bearer credential**. Anyone who can see the iPad screen
can photograph that QR and upload to that design. This is the same category
CLAUDE.md §14 already names for signed design-image URLs — *"temporary bearer
URLs: short TTL, never persisted/cached/logged anywhere, never presented as
revocable or non-shareable."*

**That exposure is accepted and bounded, not removed.** The ADR must say so in
those words, exactly as ADR 0019, 0021 and 0022 were required to. Do not
describe the bounds below as eliminating it.

The bounds:

| Bound | Value | Why |
| --- | --- | --- |
| Scope | Upload only, into one named design | The grant can never read the design, its answers, its versions or any generated image. There is no read endpoint behind it. |
| Lifetime | Short, minutes not hours (suggest 15) | A QR photographed off a screen goes stale before it leaves the shop. Named setting, strict positive-int parsing per §8. |
| Uses | At most the design's remaining reference slots | A grant that has filled the design's three slots is spent. |
| Revocation | Explicit, and automatic on the design moving past the reference step | The stylist gets a visible "stop accepting photos" control. Unlike a signed storage URL, this one **is** genuinely revocable, because it resolves through Sitara. |
| Rate limits | Per-grant and per-hashed-IP, fail-closed on cache outage | Same discipline as `accounts/rate_limits.py`. Reuse it; do not write a second limiter. |

Storage rules for the grant:

- The secret is high-entropy, generated with `secrets.token_urlsafe`, and stored
  **hashed** — the row holds a digest, never the plaintext. The plaintext exists
  only long enough to render the QR.
- **Never logged.** Not in a request path, not in a failure path, not in Sentry.
  Same category as an attachment filename (ADR 0022) or a note (§7).
- Not returned by any endpoint other than the one that mints it for the owner of
  that design.
- Deleted with the design and covered by the existing retention purge.

An expired, revoked, spent or unknown grant returns **one indistinguishable
response**. A caller must not be able to tell "this design exists but the code
expired" from "no such code" — that is §15's private-resource enumeration rule
applied to a new identifier.

### 6. The affirmation must be made by the person whose photograph it is

This is the part that is easy to get wrong and matters most.

ADR 0019's disclosure exists because sending a reference image to the provider is
irreversible and the provider's terms take a perpetual, irrevocable licence over
inputs. The current UI puts that disclosure and its checkbox in front of whoever
is holding the device. On a shop-floor iPad that is very often the **stylist**,
not the person in the photograph.

Therefore:

- The phone page shows the **full ADR 0019 disclosure** before the picker is
  usable, in the same words as the iPad, not a shortened version. A phone screen
  is smaller; that is a layout problem, not a licence to abbreviate.
- The **affirmation is taken on the phone**, by the person choosing the image.
  A tick on the iPad does not carry across the handoff.
- The affirmation is per-grant and recorded on each upload exactly as
  `DesignInspirationUpload.rights_acknowledged_at` already does. An upload
  without one is refused server-side, as now.

Get this wrong and the product records a rights affirmation made by someone who
never saw the photograph. That is worse than no affirmation, because it looks
like one.

### 7. How the iPad learns a photo arrived

The iPad already has an authenticated, ownership-checked path to its own
design's upload list. Poll it while the handoff panel is open, on the same
backoff discipline TanStack Query already uses for generation progress
(§17 restricts TanStack Query to that flow — extending it here needs a sentence
in the ADR saying so, or a plain interval — **decided: a plain interval**, which
leaves CLAUDE.md §17 untouched).

No WebSockets, no SSE, no push. The panel is open for a minute or two at most.

---

## Part C — What this does to generation

Reference-image conditioning (ADR 0019) is unchanged in mechanism and unchanged
in exposure. What changes is the population of what gets sent: **only the
customer's own uploads, never a catalogue asset**, because there are no longer
any catalogue assets to select.

- `generation/reference_images.py` remains the only module that may mint a
  reference URL. Its bounds (`MAX_REFERENCE_IMAGES`, https only, length ceiling,
  `ReferenceImagesRejected` before any provider call) are untouched.
- The `publicly_eligible()` re-check at mint time stays for historical designs.
  Do not delete it on the grounds that the selection path is gone — a design
  generated today may still carry a selection made before this phase.
- The demo path still builds no reference URL at all. Untouched.
- `DEFAULT_IMAGE_MODEL` stays `black-forest-labs/flux-2-max`; it must accept
  `input_images` and this phase gives it no reason to change. No pricing-profile
  bump.

---

## Part D — The shared device, and who the account belongs to

**Not requested. Read this before deciding the phase's scope — the owner's
answer below makes it larger, not smaller.**

> **Owner decision (2026-08-11):** "The boutique, don't worry about the account as
> each shop would have their own account."

### 1. The ownership model, stated plainly

> **Owner decision (2026-08-11):** "the designs actually belong to the store not
> the customer but the store can decide to give the customer the designs if they
> want to."

**A concept produced on a shop's iPad is the shop's work product.** The shop
commissioned it, the shop paid the token for it, and the shop decides whether and
when to pass it to the customer. The customer has no claim on it and no account
through which to hold one. This is the ownership model, it is deliberate, and
every document in the repository should read that way.

It settles the walk-in question cleanly: **the walk-in customer never
registers.** One account per shop, no sign-up friction in front of a customer, no
per-visitor free tier to farm.

It also supersedes an assumption ADR 0004 and ADR 0023 are built on — that a
design's owner is the person who answered the questionnaire. From here, **one
account owns the work produced for many different customers**, permanently and by
design. That is a genuine change to the ownership model rather than a detail, and
the new ADR must record it as a decision taken, not a drift.

### 2. What that does and does not resolve

It resolves the original concern — customer B inheriting customer A's *account* —
by making it a non-event. There is one account, it is the shop's, and B signing
into it is the intended state.

What remains is narrower and is **not** an ownership question, so it should not
be argued as one. It is this: on a shared screen, work produced for one customer
should not be put in front of the next one. Not because the first customer owns
it — she does not — but because a shop showing Customer A's concepts, fabrics,
colours and annotation notes to Customer B is a shop making a choice it did not
intend to make. The annotation note is the sharpest case: it is the most personal
free text in the product, and it records what someone disliked about a garment
she intends to wear.

Two things are true at once and neither weakens the other: **the shop owns the
designs**, and **the shop is the data controller** for the customer photographs
and notes inside them. Ownership of the work product settles who may share it;
it does not remove the shop's ordinary obligations over personal data, and the
product should make the responsible choice easy rather than leave it to whoever
is holding the iPad.

### 3. The remedy

> **Owner decision (2026-08-11):** "Disable account gallery for now we'll think of
> a secure way of displaying it later."

- **The account concept gallery ships disabled.** Flag-gated off by default, on
  the same pattern as `ACCOUNT_EMAIL_DELIVERY_ENABLED` — **gated, not deleted.**
  Everything ADR 0024 built stays in the codebase, tested, behind a named setting
  that defaults to false, so re-enabling it later is a decision rather than a
  rebuild. The route returns the same controlled response as any other disabled
  surface; it does not 404 in a way that implies it was never there.
- **The walk-in flow shows only the current session's work** — questionnaire,
  references, the concept just generated, its one refinement, its annotation.
  This is a **server-side** scope on the request, not a hidden link: §10 is
  explicit that route guards are not authorisation.
- **An explicit "Finish and hand back" control** ending the walk-in session:
  clears the workspace pointer, drops in-memory state, revokes any live handoff
  grant, returns to a neutral start screen. It no longer needs to sign the shop
  out, which is the one real simplification the shop-account decision buys.
- **An idle timeout** doing the same unprompted, because customers walk away.
  Bounded, named setting, enforced server-side — a client-side prompt is not the
  boundary.
- **No "remember me" on the customer-facing flow.** Cookies stay `SameSite=Lax`
  and HttpOnly; nothing here weakens that.

**One consequence to accept deliberately:** with the gallery disabled and no
customer account, there is **no in-app way to find a past concept**. The emailed
render becomes the only durable route out of the product — which raises the
stakes on `ACCOUNT_EMAIL_DELIVERY_ENABLED`, still false, still never once
exercised against a real SMTP server. Until that gate opens and its checkpoint is
run, a concept generated in a shop can be seen only during the session that
produced it. That is a real product limitation, not a detail, and it belongs in
the ADR rather than in a footnote.

### 4. Where the emailed render goes

> **Owner decision (2026-08-11):** "By default the user's email which will be the
> shops and then the shop can send it to the customer later if they desire."

This is exactly what ADR 0021 already enforces — recipient is
`request.user.email`, read server-side, never client-supplied — so **no code
changes**, and §26's prohibition on a client-supplied recipient stands untouched.

What changes is the copy, which currently reads as though the render is going to
the person in front of the screen. It should say the render goes to the shop's
own account address, and that passing it to the customer is the shop's to do.
That also matches §1: the shop owns the design and decides whether to hand it on.

If a bride is ever to receive her concept directly, that is a separate phase with
its own verification design — not a copy change and not a field on this endpoint.

---

## Read first

`CLAUDE.md` in full (§2, §11, §13, §14, §15, §17 especially),
`docs/phases/PHASES.md`, and:

| Source | Why |
| --- | --- |
| `docs/decisions/0006-*` | The catalogue and its rights model. Retired by this phase. |
| `docs/decisions/0014-*` | Metadata influence, the acknowledgement snapshot, the immutability rule. |
| `docs/decisions/0018-*`, `0019-*` | User uploads and reference-image conditioning. The kept half. |
| `docs/decisions/0004-*`, `0023-*` | Session ownership — the assumption Part D breaks. |
| `apps/web/src/features/questionnaire/InspirationPicker.tsx` | The catalogue grid being deleted. |
| `apps/web/src/features/questionnaire/InspirationUpload.tsx` | The component that survives and grows. |
| `apps/web/src/features/questionnaire/screens.ts` | `INSPIRATION_SCREEN_ID` — how the step is wired. |
| `apps/api/sitara/designs/upload_service.py`, `upload_processing.py` | The upload path, its lock and its bounds. |
| `apps/api/sitara/generation/reference_images.py` | The only place a reference URL may be minted. |
| `apps/api/sitara/accounts/rate_limits.py` | The hashed-identifier limiter to reuse for grants. |
| `apps/api/sitara/catalogue/` | Everything being retired. |

### Findings already established — verify, do not re-derive

- **The inspiration step is not a questionnaire question.**
  `INSPIRATION_CATEGORY_ID` / `INSPIRATION_SCREEN_ID` are `"__inspiration__"`,
  deliberately double-underscored so they cannot collide with a schema id, and
  `screens.ts` notes the screen *"has no schema step of its own."* **This phase
  therefore needs no new questionnaire version.** Phase 23 does; this one does
  not.
- The catalogue holds no approved rights-cleared assets in any environment, and
  never has (Phase 5B checkpoint, still pending).
- Uploads and presets currently share one three-reference budget, enforced in
  `upload_service` under a row lock with a per-table backstop constraint. The
  lock stays; only the arithmetic simplifies.
- `USER_UPLOAD_MAX_BYTES` is 15 MB, `USER_UPLOAD_MAX_IMAGE_PIXELS` 40 MP,
  `USER_UPLOAD_OUTPUT_MAX_EDGE` 2048. Camera photos land near these.
- Fourteen non-test backend modules reference catalogue symbols. Removing the
  public endpoints is a contract change; removing the app is not in scope.
- `DesignInspirationUpload` already carries no filename, no client content type,
  and a server-generated key. The handoff must not add any of those back.

---

## Required commit boundaries

One reviewable concern each, in dependency order.

1. `feat(api): retire the public inspiration catalogue endpoints`
2. `feat(frontend): make the reference step uploads-only`
3. `feat(frontend): capture a reference photograph from the device camera`
4. `feat(designs): add bounded, revocable reference upload grants`
5. `feat(api): accept a reference upload against a grant`
6. `feat(frontend): hand off to the customer's phone by QR`
7. `feat(account): gate the concept gallery behind a disabled-by-default flag`
8. `feat(frontend): end a shop-floor session deliberately`
9. `test(e2e): drive the uploads-only reference step`
10. `docs(phase-22): record the catalogue retirement and the handoff grant`

---

## Automated tests

Beyond the tests named inline:

**Catalogue retirement** — the three public endpoints are gone (404 at the route
layer, not a 500); a draft PATCH naming `inspiration_asset_ids` is a controlled
400 with the unknown-field code, never a silent ignore; a historical design that
carries a persisted `inspiration_context` still renders its acknowledgement from
the snapshot, unchanged, byte for byte; the snapshot columns are untouched by any
migration in this phase.

**Grants** — an expired, revoked, spent and unknown grant are **mutually
indistinguishable** in status, code and body; a valid grant cannot read anything
(assert there is no reachable read path, not merely that the client has no
button); a grant for design A cannot upload to design B; the plaintext secret
appears in no log record and no Sentry payload (reuse the existing log-capture
fixture); the stored value is a digest, asserted by shape and by the absence of
the plaintext; concurrent uploads against one grant cannot exceed the design's
remaining slots (exercise the row lock, do not assume it); rate limits fail
closed on a cache outage.

**Affirmation** — an upload through a grant without a phone-side affirmation is
refused server-side; the affirmation is recorded per upload; an affirmation
ticked on the iPad does not satisfy a grant upload.

**Camera path** — an oversized camera-shaped image is refused with the controlled
code and a message that names what to do; an image with EXIF orientation and GPS
is accepted, oriented, and emerges with all metadata stripped (assert the output
bytes, not the intent).

**Shared device** — after the end-session control, a subsequent request from the
same browser is anonymous, the workspace pointer is gone, and the previous
account's gallery is a 404 rather than a redirect; a live grant is revoked by the
same action.

**E2E** — the reference step offers no catalogue; a design generates with an
uploaded reference in demo mode at zero cost; the handoff panel renders a code
and stops accepting when revoked. Zero provider calls, zero SMTP, throughout.

---

## Commands and validation

Per CLAUDE.md §20 in full. The local `.env` may have live generation on, so
backend runs need `-e DEMO_MODE=true -e ALLOW_PAID_AI_CALLS=false -e
LIVE_GENERATION_ENABLED=false` explicit. Frontend checks run on the host.
Regenerate the OpenAPI schema and the TypeScript client, twice, and prove
drift-free.

## Manual checkpoint

Operator-run, on real hardware, and **not part of the PR**: on an actual iPad in
the intended setting, complete the reference step **by phone handoff, with a real
phone that is not the iPad**; confirm the disclosure is readable on the phone
before the picker is usable; confirm a photographed QR stops working after
expiry; confirm "Finish and hand back" leaves the next person anonymous. Until
this is run, the honest claim is "implemented and exercised against the test
suite", nothing stronger.

> As commissioned this step read "three ways — camera, phone handoff, file
> picker". The 2026-08-12 amendment removed the camera and the file picker, so
> the handoff is the only path left to exercise. Corrected here rather than left
> as written, because unlike the narrative above it is a checklist an operator
> follows literally. See ADR 0026's Amendment.

## Non-goals

- Deleting the `catalogue` app, its models, its migrations or its admin.
- Rewriting or deleting any persisted `inspiration_context` snapshot or
  acknowledgement.
- Any read capability behind a handoff grant. Upload only, permanently.
- User uploads entering the shared catalogue. Still prohibited (§13).
- A device or kiosk *identity*. The shop account owning the work produced for
  many customers **is** now the model (Part D §1) — but it is an ordinary
  account, signed into normally, not a new identity type.
- A customer-facing account of any kind. The walk-in never registers.
- Restoring the account gallery. It ships gated off, pending its own design.
- Remote URL import, scraping, or automatic rights verification.
- Any change to the image model, the prompt builder, or ADR 0017's cost controls.
- Push/WebSocket transport for the handoff.
- Raising `MAX_INSPIRATION_IMAGES`.

## Documentation and decision record

- **ADR 0025 — the inspiration catalogue is retired from the product.** Must
  record that ADR 0006's machinery remains, dormant and staff-only; that no
  approved asset ever existed, so nothing is being taken from users; that ADR
  0014's persisted snapshots stay immutable and readable; and that removing the
  stronger rights model does **not** upgrade the weaker upload affirmation.
- **ADR 0026 — in-store reference capture by phone handoff.** Must state plainly
  that the QR is a **bearer credential** and that the exposure is *accepted and
  bounded, not removed*; list the bounds; record that the ADR 0019 affirmation
  moves to the person choosing the image; and record the indistinguishable
  failure response.
- **ADR 0027 — the shop owns the design, and the shared device.** Must record, in
  this order: that a concept produced on a shop's iPad is **the shop's work
  product**, commissioned and paid for by the shop, which decides whether to pass
  it to the customer; that this supersedes ADR 0004's and ADR 0023's assumption
  that a design's owner is the person who answered the questionnaire; that the
  walk-in customer never registers; that the shop is nonetheless the data
  controller for the photographs and notes inside those designs, which is
  compatible with owning them, not in tension with it; that the account gallery is
  gated off by default pending its own design; and the accepted consequence that
  with the gallery gated and email delivery still disabled, **a concept is
  reachable only during the session that produced it**.
- Update `CLAUDE.md` §13 where it describes curated presets as a live selection
  path, and §5 if the catalogue's description changes.
- Update `docs/phases/PHASES.md`.

## Decisions taken by the project owner

All eight questions were answered on 2026-08-11. Nothing in this phase is now
waiting on the owner; the answers are recorded here and applied in the body above.

| # | Question | Decision |
| --- | --- | --- |
| 1 | Retire the catalogue, or delete it? | **Retire.** ADR 0006's models, admin, ingest and rights machinery stay, dormant and staff-only. No destructive migration. |
| 2 | Keep `reference_images.py`'s catalogue branch? | **Keep**, guarded and commented, so a historical design with a selection keeps its live `publicly_eligible()` re-check. |
| 3 | TanStack Query or a plain interval for handoff polling? | Owner: "whatever best" → **plain interval**, leaving CLAUDE.md §17's confinement of TanStack Query to the generation-progress flow untouched. One less thing to argue about in review. |
| 4 | Handoff code lifetime? | **15 minutes**, revocable early, spent after three uploads. |
| 5 | Does Part D belong here? | **Yes** — and the shop-account answer made it larger, not smaller. It is now a change to who owns a design. |
| 6 | Keep the iPad's own file picker? | **Keep** — but the owner's framing moves the emphasis: most customers arrive with the picture already on their phone, so the QR handoff is the primary path and the picker is the stylist's fallback. See Part A §1 for the bar that sets. |
| 7 | Is the account gallery reachable on the shop iPad? | **Disabled entirely for now**, flag-gated off by default and *not deleted*, pending a secure display design later. Stronger than the option offered. See Part D §3, including the consequence. |
| 8 | Who is the emailed render for? | **The shop's own account address** — already exactly what ADR 0021 enforces, so no code change; the copy is corrected to say so, and passing the render to the customer is the shop's to do. |

### What these answers changed

- **Part D grew.** The shop account decides something ADR 0004 and ADR 0023 are
  built on, so ADR 0027 is no longer optional and is no longer only about
  ending a session — it records a changed ownership model.
- **The gallery is gated, not merely hidden.** That adds commit 7 and a named
  setting, and it removes the last in-app route to a past concept — stated as an
  accepted limitation in Part D §3, not buried.
- **Part B's bar rose.** The handoff is judged as the main path now, with the
  five concrete criteria in Part A §1 as tests rather than aspirations.
- **Nothing here needs a new questionnaire version.** Unchanged: the inspiration
  step has no schema step of its own.
