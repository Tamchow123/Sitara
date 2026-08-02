# End-to-end and visual-regression tests (Phase 17)

Playwright specs that drive the **real** application against a **running stack**,
always in demo mode. Nothing here mocks a stage: a generation is a genuine Celery
job moving through `queued → running_text → running_image → succeeded`, and the
assertions are written against the terminal state the server actually produces.

| Spec | Covers |
| --- | --- |
| `safety.spec.ts` | The stack reports demo mode; no browser request reaches a provider host; the questionnaire stores nothing in browser storage |
| `journeys.spec.ts` | §25 journeys 1–5 — draft persistence, keyboard-only wizard (choose, Back, Skip), the information drawer, the custom colour picker, inspiration selection and synthetic upload/removal |
| `generation.spec.ts` | §25 journeys 6–10 — generate, resume mid-flight, a failed image with the brief intact, the one refinement, the two-version history |
| `visual.spec.ts` | §26 visual regression, 15 baselines per viewport |

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

```powershell
# 1. Bring the stack up with the gates closed and stages slowed enough to observe.
$env:DEMO_MODE="true"; $env:ALLOW_PAID_AI_CALLS="false"
$env:LIVE_GENERATION_ENABLED="false"; $env:DEMO_STAGE_DELAY_MS="5000"
docker compose up -d

# 2. Install the deterministic demo fixture pack (zero-cost, locally generated).
docker compose exec api python manage.py install_demo_asset_pack --dev-synthetic

# 3. Run.
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

## Known gap: the inspiration catalogue

Journey 5 needs an approved catalogue asset, and there is deliberately **no**
fixture, seed command or import path that creates one: the catalogue is
staff-managed, every asset needs staff-verified evidenced rights, and fabricating
that evidence is forbidden (CLAUDE.md §13). On a clean stack the journey therefore
**skips**, visibly, rather than failing or passing silently. It runs in full
against a stack whose catalogue an operator has approved assets into.
