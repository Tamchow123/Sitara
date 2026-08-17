# End-to-end and visual-regression tests (Phase 17, extended in Phases 19 and 21)

Playwright specs that drive the **real** application against a **running stack**,
always in demo mode. Nothing here mocks a stage: a generation is a genuine Celery
job moving through `queued → running_text → running_image → succeeded`, and the
assertions are written against the terminal state the server actually produces.

| Spec | Covers |
| --- | --- |
| `safety.spec.ts` | The stack reports demo mode; no browser request reaches a provider host; the questionnaire stores nothing in browser storage |
| `journeys.spec.ts` | §25 journeys 1–5 — draft persistence, keyboard-only wizard (choose, Back, Skip), the information drawer, the custom colour picker, and the reference step: no catalogue and no way in from this device, the QR handoff at the iPad end, a photograph sent from a separate browser context arriving and being removed, and the phone page with no code |
| `generation.spec.ts` | §25 journeys 6–10 — generate, resume mid-flight, a failed image with the brief intact, refinement and the version history |
| `annotations.spec.ts` | Phase 19, extended in 21 — the private annotation workspace: draw a pin and a rectangle with real pointer gestures, note them, prove persistence across a reload, prove hiding is not deleting, prove nothing reaches browser storage, a stranger's indistinguishable 404, the original render left untouched, and the naming prompt: its pre-fill, its stated exposure, the allowance stated before it is spent, and a refused name answered in the dialog |
| `gallery.spec.ts` | Phase 21 — the account gallery: a concept made through the real pipeline is findable afterwards under the same name, its card's links resolve to that concept and its workspace, its thumbnail actually decodes, and the list payload itself carries no bearer URL, storage key or spec description |
| `visual.spec.ts` | §26 visual regression, 15 baselines per viewport |

## Phase 21 — one account, shared by every journey that generates

An account is required for the last press before a concept is produced (ADR 0023).
`auth.setup.ts` registers **one** account for the whole run as its own Playwright
project, and `generation.spec.ts`, `annotations.spec.ts`, `gallery.spec.ts`,
`visual.spec.ts` and the signed-in half of `accessibility.spec.ts` reuse its
session through `test.use({ storageState })`.

**Why one and not one per spec.** Registration is throttled at
`AUTH_REGISTER_IP_LIMIT` (5) per IP per hour, and the whole suite arrives from a
single IP. Registering per generating spec across the desktop and mobile projects
is five or more, which trips the limit — the first attempt at this phase failed
exactly there, with the register form showing "Too many attempts". Lowering the
limit for the e2e stack would be weakening a security control to make a test pass.
Registering once is also closer to the truth: one account holding several concepts
is the shape the account gallery exists to show.

`journeys.spec.ts` deliberately stays anonymous — journeys 1–5 never generate, and
they exist to prove the questionnaire is still walkable end to end with no account.
`accessibility.spec.ts` keeps its landing, questionnaire and **review** axe passes
anonymous for the same reason, and adds the review screen's new sign-in state to
them; only its generation/result test is signed in.

The session lands in `e2e/.auth/` (gitignored) as a storage-state file — the
session cookie, which is what a browser holds. No password is written to disk.
It is kept out of the image by `apps/web/.dockerignore`. It was briefly also
kept out of the `web` container by a `tmpfs` shadow, when `e2e/` was bind-mounted
for the prose guard described below; that mount is reverted, so `.dockerignore`
is once again the only rule that has to hold.

**The axe engine lives in `helpers/axe.ts`, not in `accessibility.spec.ts`.**
`generation.spec.ts` points the same engine, with the same one disabled rule, at
the comparison view — the screen that carries two of every action and is
therefore where a name/role/value or duplicate-id problem shows up. It is not
checked from `accessibility.spec.ts` because reaching it there would mean paying
for a second full generation to see a screen `generation.spec.ts` is already
standing on.

**`src/app/refinement-budget-copy.test.ts` scans these specs' prose** for
superseded claims about the refinement budget, and `e2e/` is NOT bind-mounted
into the `web` service — so in that container it would read whatever the last
`docker compose build web` baked in and report green on a claim added afterwards.
Mounting `e2e/` was tried and reverted: `.dockerignore` governs COPY and not bind
mounts, so the mount reopened `e2e/.auth`'s live session cookie inside the
container, and the `tmpfs` that shadowed it could not create its mountpoint under
a read-only bind on any checkout where `.auth` does not already exist — which is
every CI run, and it took the `web` container down with it. The guard now refuses
to run when its root is the container's `/app`, and has a row in CLAUDE.md §20's
table.

**One account holding many concepts is why `gallery.spec.ts` never counts cards.**
Every generating spec adds to the same gallery, and the desktop and mobile
projects both run, so the number of cards on the account page depends on which
specs ran before. It locates its card by the link to the design it just made, and
asserts nothing about totals or ordering position — an assertion like "the first
card is mine" would pass or fail on run order rather than on behaviour. Ordering
and paging are covered where they are deterministic, in
`designs/tests/test_gallery_api.py`.

Two consequences worth stating plainly rather than discovering later:

**The `review` visual baseline still means what it always meant.** A signed-in
review screen shows the generate button; an anonymous one now shows a sign-in link
instead. `visual.spec.ts` registers so the baseline keeps capturing the same
screen rather than being silently re-pointed at a different one.

**One assertion moved from the browser to the API, and was not dropped.** The
private-workspace journey used to end by proving an anonymous workspace owner is
refused a send — no fallback, no prompt for an address, no silent success. That
journey can no longer own a concept anonymously, so the assertion had nowhere left
to stand. It is now proved by
`test_an_anonymous_owner_is_told_to_sign_in_and_nothing_is_sent` in
`apps/api/sitara/designs/tests/test_render_send_api.py`, which builds an
anonymously-owned version directly and asserts `409 email_recipient_unavailable`
with an empty outbox. That test's docstring records that it is now the only proof.

## Phase 19 — what `annotations.spec.ts` deliberately does and does not prove

**Marks are drawn, not seeded.** The gestures go through `page.mouse`, because the
path from a press to a stored normalised coordinate is exactly what a unit test
cannot cover. `geometry.test.ts` covers the maths exhaustively; this spec covers
the fact that the maths is wired to a real pointer and a real image box.

**The send stops at QUEUED, and that is not a shortcut.** Delivery is asynchronous
and the suite opens **no SMTP connection at all** — Django's test environment
resolves the locmem backend, and `ACCOUNT_EMAIL_DELIVERY_ENABLED` is false by
default, so the honest terminal states are "queued" or the controlled
`503 email_delivery_disabled`. The spec accepts either and asserts both are silent
on SMTP. Asserting on a *delivered* message would need a real mail server or a
fiction about one.

**The lifetime ceiling is not driven from a browser, and that is a limitation, not
a claim.** Nothing is reserved while `ACCOUNT_EMAIL_DELIVERY_ENABLED` is false — the
gate is checked before the reservation — so the per-render counter never moves and
a "fourth send" is unreachable through the UI. Reaching it would mean configuring
outbound mail on the e2e stack, which CLAUDE.md §7 forbids. The ceiling, its
refusal wording and the remembered name are proved against the real reservation in
`test_render_send_api.py`
(`test_the_fourth_send_is_refused_with_the_numbers_that_explain_it`,
`test_the_refusal_names_the_used_count_and_the_ceiling_separately`,
`test_the_name_is_remembered_for_the_next_send`). What the browser specs do prove
is the part only a browser can: that the remaining count is on screen **before**
the last send rather than only in the refusal after it.

**The original render is compared by object key, not by URL.** Signed URLs are
short-lived and minted per request, so a changed query string is expected; what
must never change is which stored object it points at.

## Safety

Every run requires all three gates closed. `safety.spec.ts` asserts the running
stack actually reports demo mode, so a misconfigured stack fails loudly instead of
quietly making paid calls:

```text
DEMO_MODE=true  ALLOW_PAID_AI_CALLS=false  LIVE_GENERATION_ENABLED=false
```

These are passed explicitly on the command line below rather than left to `.env`,
because a developer's local `.env` may well have live generation switched on.

## Running locally

**Rebuild `api` first if the backend changed.** The `api` service has no source
mount — it runs its baked image — so `docker compose up -d` alone will happily
start a container whose Python predates whatever you just wrote. The failure this
produces is *plausible*, which is what makes it expensive: a route added in the
working tree answers `405 Method Not Allowed`, and a spec asserting on the new
behaviour fails as if the feature were broken. This cost a full e2e cycle in Phase
21, when the new send-state `GET` returned 405 from a stale image.

```powershell
# 1. Rebuild the API image if anything under apps/api changed. Cheap, and the
#    alternative is debugging the wrong thing.
docker compose build api

# 2. Bring the stack up with the gates closed and stages slowed enough to observe.
$env:DEMO_MODE="true"; $env:ALLOW_PAID_AI_CALLS="false"
$env:LIVE_GENERATION_ENABLED="false"; $env:DEMO_STAGE_DELAY_MS="5000"
docker compose up -d

# 3. Install the deterministic demo fixture pack (zero-cost, locally generated).
docker compose exec api python manage.py install_demo_asset_pack --dev-synthetic

# 4. Run.
cd apps\web
npm run e2e              # functional journeys + safety
npm run e2e:visual       # visual regression only
npm run e2e:update       # re-record baselines after an INTENDED design change
```

`DEMO_STAGE_DELAY_MS=5000` is required, not cosmetic. It is the maximum the
setting allows, and it holds `running_text` and `running_image` long enough for
`visual.spec.ts` to capture a *known* progress stage rather than whichever one
happens to be current — the generation baseline flapped between runs until the
spec anchored on `running_text`.

## Gotchas that cost real debugging time

**`localhost`, never `127.0.0.1`.** Django's `CSRF_TRUSTED_ORIGINS` lists the
`localhost` form. Driving the same port by IP makes every unsafe request fail with
a 403 `csrf_failed` that looks exactly like an application bug and is not one.

**Re-run `docker compose up` with the gate variables every time — including a
partial recreate.** `docker compose up -d --force-recreate web` also recreates
the API it depends on, and it does so with whatever environment the shell has.
Without the three overrides the stack falls back to your local `.env`, which on
a developer machine may well have live generation ON. This happened during
Phase 17: the stack came back up reporting `demo_mode: false,
generation_enabled: true`. Nothing was spent, because `safety.spec.ts` is a
Playwright project dependency and failed before any journey ran — but the lesson
is that the gate variables belong on every `up`, not just the first one.

**Run the whole suite, not one project.** `--project=desktop` is a debugging
shortcut, not a verification. The focus-obscuring check passed on desktop and
failed at 390px on the first CI run, because the Next.js dev overlay sits over a
footer link only at that width. Verify with `npm run e2e`, which runs both.

**Rebuild `celery` after any backend change.** Neither `api` nor `celery` mounts
source — both run code baked into their image — and `docker compose build api`
does not rebuild the worker. The two then drift silently: a stale worker rejected
every demo generation with `design_changed` while the API, on a newer image,
validated the same row happily. Always:

```powershell
docker compose build api celery celery-beat
```

**The stack runs `next dev`, not a production build.** The `web` service
bind-mounts `src/` and `public/`, so these specs exercise the dev server. Layout
is identical, but this is why baselines are recorded here rather than against
`npm run build`.

**One worker, always.** `workers: 1` is pinned in `playwright.config.ts` even
locally. These journeys drive one shared backend with one Celery worker, so two
in parallel contend for the same generation queue; the desktop and mobile
projects failed together and passed individually until this was pinned.

## Visual baselines

The 30 committed baselines are ~11 MB, because they are full-page shots of
photo-heavy questionnaire screens. That is a deliberate, and not free, trade-off:
a re-record after an intended design change adds roughly that much again to
history. Re-record only when a change is intended, never to make a red run green.

## The committed baselines are stale, and have been since Phase 19

**Measured on 2026-08-17, on the Phase 23 branch: 7 of 14 fail.** `landing`,
`concepts`, `result` and mobile `questionnaire-ceremony`; the generation spec
aborts at `result`, so `refinement` and `history` are simply unreached and their
state is unknown rather than good.

Only `landing` and `concepts` are recent — the Phase 23 refinement-budget copy
correction moved them. The rest predate this phase by three. `git log` puts the
last full re-record at `2471175` (Phase 17), and the committed
`result-desktop-win32.png` still shows:

- a **Download image** link under the render, which Phase 19 (ADR 0020) replaced
  with Annotate and Send to account;
- the kicker **"REFINE THIS CONCEPT · ONE REFINEMENT"** and the sentence "You may
  request exactly one change", which ADR 0029 superseded;
- a **Styling details** refinement category, which ADR 0028 retired;
- a signed-out **Sign in / Create account** header, on a screen ADR 0023 now
  requires an account to reach;
- refinement category cards with no option descriptions, which the ADR 0018
  Phase 23 addendum added.

This is the same defect class the two refinement-budget prose guards were written
for — a check that CI does not run drifted quietly, and its "expected" image is
now a museum of superseded claims, including the literal words "ONE REFINEMENT"
that every other surface was corrected away from. The guards scan `.ts/.tsx/
.css/.md`; nothing scans a PNG, and nothing can.

**Do not re-record these as a side effect of an unrelated change.** Approving a
baseline asserts the screen is right, and re-recording all of them in one pass
would bless four phases of UI nobody has looked at. It wants its own commit, with
each new image actually reviewed. The instruction two paragraphs above — "never
to make a red run green" — is exactly the rule that applies here, and it points
at reviewing the drift rather than at hiding it.

Baselines live in `visual.spec.ts-snapshots/` and are **platform-specific** —
Playwright suffixes them with the OS (`-win32`, `-linux`). The committed set was
recorded on Windows, which is why CI runs the functional specs only and not the
visual project. Re-recording on another platform would add a parallel set, not
replace these.

Determinism comes from four places: reduced motion plus disabled animations and a
hidden caret; a fixed locale and timezone; a `settle()` that waits for fonts,
image decode and for every pending query to have landed; and masks over anything
genuinely non-deterministic — the concept image is delivered by a short-TTL
signed URL and is masked, though its frame is still compared.

`settle()` is where nearly all the flakiness lived, and it took three attempts:

- **The site-wide demo banner** renders `null` until the public config resolves
  and is ~43px tall, so a shot taken early is not slightly different — it is the
  whole page displaced by 43px. Waiting for the *absence* of a pending state
  could not catch it, because the questionnaire announces nothing while config is
  in flight; it simply renders nothing where the banner will go. The fix is a
  *positive* wait for `.demo-banner`, which is sound because these runs are
  always in demo mode and `safety.spec.ts` proves it.
- **Announced pending states** ("Checking your design…") displace the page the
  same way and are waited out separately.
- **`networkidle` alone caught neither**, because React renders the resolved
  markup a frame after the response lands.

**The Vitest suite must run on the host, not in the `web` container.**
`design-tokens.test.ts` reads the vendored design system from
`design_handoff_sitara_flow/`, which sits at the repository root — outside the
`apps/web` Docker build context — so the container cannot see it at any point,
rebuild or not. Running `docker compose exec web npm test` reports 32 failures
that do not exist: 16 suites cannot resolve the token source or the axe matcher.
On the host the same suite is 48 files / 825 tests green, and CI's `frontend`
job runs it against a full checkout, which is the authoritative gate.
`.claude/phase-council.json` therefore runs `npm --prefix apps/web test`.

The same applies to typecheck, lint and build: the container mounts only `src/`
and `public/`, so it cannot see `e2e/` or `playwright.config.ts` at all. All four
frontend commands therefore run on the host in `.claude/phase-council.json`,
matching CLAUDE.md §20. None of them is the authoritative gate — CI's `frontend`
job runs them against a full checkout on pinned Node 22, and `tsconfig.json`'s
`**/*.ts` sweeps in `e2e/` there. The local commands exist because it is cheap to
catch the error before pushing.

`reducedMotion` must sit under `use.contextOptions`, not directly on `use`.
Playwright has no top-level option of that name and silently ignores unknown
keys, so the wrong placement type-errors but still runs — with reduced motion
never applied and nothing at runtime to say so. `npm run typecheck` caught it;
note that the containerised typecheck cannot, because the `web` service mounts
only `src/` and `public/`.

## Known gap: freehand on a small touch screen

`annotations.spec.ts` draws with a mouse. Freehand strokes on a phone-sized touch
screen are genuinely coarse — a fingertip is a blunt instrument at that scale, and
the simplifier thins a stroke to 500 points regardless. The tool stays available
on mobile rather than being hidden, because a rough circle around a hem is still
useful, but detailed strokes want a wider screen. Stated here rather than papered
over; §15 asks for the limitation to be documented honestly.

## Resolved gap: the inspiration catalogue (Phase 22)

Journey 5 used to need an approved catalogue asset, and there was deliberately
**no** fixture, seed command or import path that created one: the catalogue was
staff-managed, every asset needed staff-verified evidenced rights, and fabricating
that evidence is forbidden (CLAUDE.md §13). On a clean stack the journey therefore
**skipped**, visibly.

Phase 22 (ADR 0025) retired the catalogue from the product, so the skip is gone
along with the thing it was waiting for. Journey 5 is four tests that all run:

1. **No catalogue, and no way in from this device.** No picker grid is offered,
   and — since ADR 0026's amendment — no file input of any kind and no rights
   affirmation either. Both are asserted as the absence of the *control*, not of
   a particular label, so a rename cannot slip past. The code is on screen
   without anything being pressed.
2. **The QR handoff, at the iPad end.** The code renders by itself, the typed
   fallback carries the same `/r#…` URL with the secret in the fragment, "Stop
   accepting photos" really removes it, and a stop stays stopped — the panel
   offers "Show a new code" rather than quietly minting one, because a stop that
   undid itself would make ADR 0026's revocability claim false.
3. **A photograph sent from the phone arrives, and can be taken off.** The code
   is read from the iPad's typed fallback and opened in a **separate browser
   context** — no shared cookie, session or storage, because the scanned code is
   the whole authorisation. There the ADR 0019 disclosure is readable and the
   picker is disabled *before* the affirmation is ticked; the upload lands; the
   iPad's poll announces the arrival; and removal — the stylist's job, since the
   phone deliberately cannot do it — frees the slot. The phone is also asserted
   to be told nothing about the design: no gallery, no remaining count. The file
   is one of the project's own synthetic questionnaire visuals — §13 permits
   locally generated synthetic images for clearly labelled engineering tests and
   forbids downloaded or unlicensed ones.
4. **The phone page with no code** says so, and offers no picker.

Test 3 replaces the device-local upload the iPad used to cover. That coverage was
not optional to lose: with the picker gone, this is the only way a reference ever
reaches a design.

What is still **not** covered is the handoff itself — a real camera, a real
second device, a code read off glass. Two contexts in one browser prove the
plumbing; they prove nothing about the thing the path exists for. ADR 0026 keeps
it as an outstanding manual checkpoint: a real iPad, a real phone that is not the
iPad, and a photographed code confirmed dead after expiry.
