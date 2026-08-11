# Sitara Phase 24 — Generation credits: five free, then tokens

**Status:** specification only. Nothing here is implemented. Written to be picked
up on its own branch, after Phase 22 and Phase 23 — it depends on ADR 0023's
authenticated generation caller, and its per-token cost basis depends on whether
a refinement is included, which Phase 23 changes the meaning of. Owner statements
are marked **Owner decision**. Every open question was answered on 2026-08-11 —
the answers, and what each one changed, are recorded at the end.

## Main objective

Every account gets five free concept generations. After that, generating costs a
token. Tokens are allocated by staff — you — through an admin page, against
subscriptions sold outside the application. One token buys one concept.

> **Owner decision:** "each user gets 5 free generations then are required to buy
> something like tokens in order to use for generations but I want an admin page
> for me to allocate tokens to users where they have purchased a subscription
> with me to use the service. Each token would be worth one generation so
> calculate the cost they should be set for a reasonable profit for each
> generation."

---

## Part A — What a generation actually costs

### 1. The measured components

Everything below is per concept, in USD, at the models the repository currently
selects.

| Component | Basis | Cost |
| --- | --- | ---: |
| DesignSpec input | `DESIGN_SPEC_MAX_INPUT_CHARS = 20_000` → ~5,000 tokens at `claude-sonnet-4-6` $3.00/MTok | **$0.015** |
| DesignSpec output | ~5,800 tokens — the figure recorded in `settings.py`, *"measured against the live API"* — at $15.00/MTok | **$0.087** |
| Image | flux-2-max, ADR 0019's recorded figure | **$0.070** |
| | **Per concept** | **≈ $0.172** |

Bounds around that figure:

| Scenario | Cost |
| --- | ---: |
| Typical concept | $0.172 |
| Concept with one DesignSpec retry (`MAX_REFINEMENT_PROVIDER_REQUESTS` shape) | $0.274 |
| Absolute ceiling — two attempts, both hitting `DESIGN_SPEC_MAX_OUTPUT_TOKENS = 8192` | $0.346 |
| Concept **plus** its one refinement | ≈ $0.344 |

A refinement is a full second text call and a full second image — the same cost
as a concept, not a discount on one. Since the owner decided a refinement costs
its own token (§7), **the cost basis for one token is $0.172**, and the last row
above is what two tokens buy.

**Two of these numbers are not verified and this must not be forgotten.** ADR 0019
records the flux-2-max figure as *"an ESTIMATE, not a verified price"*, and
`LIVE_GENERATION_PRICING_PROFILE` ships with provider prices defaulting to zero
precisely because nobody has reconciled them against an invoice. The Anthropic
side is worse right now: the development key has been at its account usage cap
since 2026-08-02 and cannot be used to re-measure until 2026-09-01.

**Therefore: no token may be sold at a price derived from this table until the
numbers are reconciled against real provider invoices.** That reconciliation is
an operator task, it is a precondition of taking money, and it belongs in the
same place as the pricing profile so the two cannot drift. See §5.

### 2. What surrounds the provider cost

| Item | Per token | Note |
| --- | ---: | --- |
| Image storage | ~£0.001 | one permanent WebP per version, ~0.5–1.5 MB, plus references |
| Database, Redis, app hosting | ~£0.01 | amortised; dominated by fixed cost at this volume |
| Payment processing | ~£0.05 at £3, ~£0.25 at £12.50 | Stripe UK ≈ 1.5% + £0.20 per *transaction*, not per token — packs amortise it |
| VAT | 20% of the sale if registered | a £3.00 VAT-inclusive price nets £2.50 |
| FX | provider costs are USD, sales are GBP | at ~1.27 USD/GBP a 10% move shifts margin ~2 points at £3 — immaterial at the recommended prices, material at the floor |

**Marginal cost of one token ≈ £0.15**: $0.172 of provider spend (≈ £0.135 at
~1.27 USD/GBP) plus ~£0.011 of storage and hosting, before payment fees and VAT
on the sale. Call it **£0.15**, and **£0.25** if you want a number that absorbs a
DesignSpec retry without thinking about it.

### 3. The recommended price

Two different numbers, and it matters which is which.

**The cost-plus floor.** Never sell a token below **£0.75** — five times the
£0.15 marginal cost, three times the retry-absorbing £0.25. Below that, a run of
DesignSpec retries plus payment fees plus VAT makes a token loss-making, and the
provider figures are unverified in the direction that could hurt.

**The recommendation, which is not cost-plus.** A bridal concept is a
consultative, high-value, low-volume purchase made in a shop. Its price is set by
what the consultation is worth to the boutique, not by what an inference call
costs — and pricing off cost here would leave almost all of the value on the
table.

| Tier | Price | Per token | vs. £0.15 marginal cost | Gross margin |
| --- | ---: | ---: | ---: | ---: |
| Single | £3.00 | £3.00 | 20× | ~95% |
| Pack of 5 | £12.50 | £2.50 | 17× | ~94% |
| Pack of 20 | £40.00 | £2.00 | 13× | ~92% |
| Boutique tier, 100/month | £150.00 | £1.50 | 10× | ~90% |

> **Owner decision (2026-08-11):** the ladder is accepted — "Yes we can adjust
> prices later." It is operator configuration with recorded dates (§5), not a
> constant in code, precisely so that adjusting it later is a config change and a
> dated note rather than a deployment.

The ladder does three things at once: the single-token price anchors the value,
the packs give a boutique a reason to commit, and the monthly tier is the shape
that matches "purchased a subscription with me" — a shop buys a block, you
allocate it, they draw it down.

A boutique on the £150 tier costs roughly **£26/month** in provider spend at full
draw-down. That is the number to hold in mind: the business is not
compute-constrained at any plausible volume, so the free tier and the pricing
should be set by customer behaviour, not by cost anxiety.

---

## Part B — Where the tokens live

### 4. Postgres, not Redis, and the reason is not preference

ADR 0017 keeps its spend ledger in a dedicated `noeviction` Redis because it is a
**daily ceiling that resets** — losing it costs a day's safety margin, and the
`noeviction` requirement is already the acknowledgement that even that is
uncomfortable.

A token is different in kind. Someone paid money for it. A Redis eviction, a
failover, or a flushed cache that destroys paid tokens is a refund liability and
a trust failure, and no TTL policy makes that acceptable. So:

- **Durable Postgres rows**, in the transaction that changes them.
- **Append-only double entry.** A balance is a derived, cached number guarded by
  a `balance >= 0` database constraint; the ledger is the truth. A correction is
  a compensating entry, never an edit and never a delete — the same discipline
  the repository already applies to `inspiration_context` and image provenance.
- **The two ledgers stay separate and both apply.** A token grants permission to
  spend; ADR 0017's budget ceiling decides whether the spend is allowed. Holding
  a token never bypasses a throttle, the daily count limit, or the micro-USD
  ceiling, and §26 forbids relaxing any of them on the grounds that callers are
  identified. A user with tokens can still be told the service is at its daily
  limit — and their token is not consumed when that happens (§6).

### 5. Shape

```text
TokenAccount            one per User. Cached `balance`, DB-constrained >= 0.
                        `free_grant_issued_at` — null until the grant materialises (§8).

TokenLedgerEntry        append-only. account, delta (signed), kind, created_at,
                        actor (staff user or null), design/version FK where
                        applicable, idempotency_key, staff_reason.
                        No row is ever updated or deleted.

TokenReservation        open reservations, mirroring ADR 0017's reserve→commit→
                        release state machine. Keyed by the generate request's
                        idempotency key so a retried request cannot double-reserve.
```

Entry kinds: `FREE_GRANT`, `STAFF_ALLOCATION`, `STAFF_ADJUSTMENT`, `RESERVE`,
`COMMIT`, `RELEASE`.

**`staff_reason` is the one piece of free text on a durable row**, and it gets the
same treatment ADR 0022 gives a chosen attachment filename: never logged, never
sent to Sentry, never AI input, never returned to the account holder — it is a
staff note about a commercial arrangement, not user-facing copy. Bounded length,
control characters refused.

The pricing figures from §1 and §3 belong beside `LIVE_GENERATION_PRICING_PROFILE`
as **operator configuration with recorded dates**, not as constants in code, so
that a price change and a profile bump are visibly the same event.

### 6. Reserve, commit, release — and a failed generation costs nothing

This is the hard product rule: **a customer paid for a picture, not for an
attempt.** Every failure path returns the token.

Gate order in `DesignGenerateView.post` **and, identically, in the refine view**
(§7), extending what ADR 0023 established:

1. `_require_account(request)` — anonymous gets `401 authentication_required`.
   **Unchanged, and still first**, so an anonymous caller still learns nothing
   about whether a design id exists.
2. Ownership resolution — a foreign or absent design is an indistinguishable 404.
   **Still before any token logic**, so a probe cannot use a balance error to
   confirm a design exists.
3. Idempotency key and content-type checks, as now.
4. **Balance check** — a cheap read. Zero balance returns `402 insufficient_tokens`
   with the balance and how to obtain more. (402 Payment Required is the correct
   code; 403 is the fallback if anything in the stack handles 402 badly.)
5. `enforce_live_admission` — throttles, daily count, budget preflight. Unchanged.
6. **Reserve one token**, atomically: `SELECT FOR UPDATE` the account row, insert
   a `RESERVE` entry, decrement the cached balance, keyed on the idempotency key.
7. Enqueue.

The token is reserved **last**, immediately before the enqueue, so the smallest
possible number of things can fail after it is held. Any failure after step 6 —
including an enqueue failure — must release it, and that path needs its own test
that forces the failure rather than asserting the happy path.

Then:

- **Terminal success** → `COMMIT`. The reservation closes; the token is spent.
- **Terminal failure, any cause** → `RELEASE`. Including: the worker's atomic
  budget reservation refusing the job (which happens *after* the queue, so this
  path is real and routine), a provider timeout, a safety-scan rejection, a
  storage failure.
- **Stuck job** → `RELEASE`, by the reconciliation task already running on Celery
  Beat from Phase 16. An orphaned reservation must not silently hold a paid
  token; add its release to that task rather than writing a second sweeper.

Demo mode reserves and commits **nothing**. `DEMO_MODE=true` is zero-cost by
definition (§7), and charging a token for a fixture image would make it not so.
A demo generation must not decrement a balance, must not consume a free grant,
and must say on screen that it did not.

### 7. A refinement costs its own token

> **Owner decision (2026-08-11):** a refinement is charged as a second token.

So **one token = one generation, exactly as originally stated**: pressing
*Generate* costs a token, pressing *Refine* costs another. The cost basis for a
token is therefore $0.172, not $0.344, and the margins in §3 reflect that.

This is the simpler rule to explain and the one the owner asked for. It also
costs more to build and needs three things handled deliberately.

**1. Both endpoints need the full ledger treatment.** `POST /designs/<id>/refine/`
gets the identical reserve→commit→release path as `POST /designs/<id>/generate/`,
with the same gate order (§6), the same idempotency keying, the same
release-on-any-failure rule, and the same reconciliation sweep. Not a shortcut, not
a shared helper that special-cases one of them — the same service, called twice.
A refinement that fails returns its token exactly as a generation does.

**2. The customer must be told before they spend, not after.** The bad moment this
creates is specific and predictable: a shop with one token left generates a
concept, sees it, wants one change, and is told they have none. Nothing about that
is dishonest, but it is avoidable, so:

- Before generating, the screen says what a concept costs **and** that refining it
  will cost another.
- At a balance of exactly one, it says so plainly before the generate — "this is
  your last concept; refining it will need another" — because that changes what a
  reasonable person does next.
- The refine control shows its cost on the control itself, not in a tooltip.
- At zero, the refine control is disabled with the reason visible, never a control
  that fails when pressed.

**3. This phase must not ship before Phase 23.** Refinement currently changes
nothing — that is the whole of Phase 23's Part A, traced to the line. Charging a
second token for a feature that provably cannot alter the image would be
indefensible, and no amount of correct ledger code fixes it. **Phase 23 is a hard
prerequisite of Phase 24**, not a preference, and the ADR should say so.

### 8. The free five

> **Owner decision (2026-08-11):** "The boutique, don't worry about the account as
> each shop would have their own account."

This is the answer that shrinks §8 from the centre of the phase to a small
feature, and it is worth being explicit about why.

**An account is a shop, and a shop is a person you have spoken to.** You allocate
their tokens by hand against a subscription you sold them. Nobody registers on
the iPad — the walk-in customer never has an account at all (Phase 22 Part D).
So the farming attack that drives most free-tier design does not have a
front door here: creating a hundred fake shops gets someone five hundred free
concepts, but they have to create a hundred accounts to do it, and you would
notice a hundred accounts you never sold anything to.

What "five free" now means: **a trial for a boutique evaluating the service** —
and, since a refinement costs its own token (§7), **five draws in total, not five
concepts**. A trial shop can take two concepts through a refinement each and have
one draw left. That is the consistent reading of "each token would be worth one
generation", and the screen must say *draws remaining*, never *concepts
remaining*, or the first refinement will feel like a charge nobody mentioned.

Five is about right for a trial: enough to form a view, not enough to run a season
on. At $0.86 it is a rounding error against the first £150 they spend.

Two mitigations still earn their place, and both are cheap:

1. **Materialise the grant on first generation, not at registration.** A shop
   account that never generates costs nothing, and the ledger stops filling with
   grants to accounts that were never trading. Ship this regardless.
2. **Staff can zero a grant** through the same admin action, with a reason —
   because you will occasionally create a test account, and you should be able to
   take its trial back without a migration.

Two things that are now **out of scope**, and the reason matters:

- **The hashed-IP cap on free grants is dropped.** Its purpose was to bound
  anonymous self-registration. With no anonymous self-registration it protects
  nothing, and it would have cost a real thing: several boutiques behind one
  retail-park NAT, or one chain's shops behind one corporate egress, would be
  refused their trial for no reason.
- **Email verification stops being the missing security control** and becomes
  ordinary hygiene — you want a working address to send the render to (Phase 22
  Phase 22 Part D §4), not a bot defence. It stays a separate phase; it stops
  being urgent.

What replaces the farming risk is a smaller, duller one worth naming: **an
account is a shop, so a shop that stops trading keeps its tokens forever** unless
tokens forever, since they do not expire. That is bookkeeping, not risk.

---

## Part C — The admin page

### 9. What it does

Django admin, staff-only, behind an explicit permission rather than bare
`is_staff` — allocating tokens is issuing something with monetary value and
deserves its own grant. It follows §16: the admin action calls the same service
any future trusted write path would call, and never writes rows itself.

- Find an account by email or username.
- See the current balance, the free-grant state, open reservations, and the full
  ledger.
- **Allocate N tokens with a mandatory reason.** No reason, no allocation.
- **Correct by compensating entry.** An over-allocation is fixed by a negative
  `STAFF_ADJUSTMENT`, never by editing or deleting a row. The ledger is read-only
  in admin — the action is the only writer.
- **Bounded.** A single allocation is capped (500 is ample against a 100/month
  tier) so a stray keypress cannot mint 100,000 tokens. Exceeding the cap is a
  validation error, not a confirmation dialogue.
- **Audited.** Who, when, how many, why — and the reason field is staff-visible
  only, per §5.

### 10. What the account holder sees

- Their balance, wherever the account surface already lives.
- Before generating: what it will cost them, in plain words — "this will use 1 of
  your 4 remaining concepts" — and for the free tier, that it is free and how
  many remain.
- At zero: a state that explains how to get more and does **not** dead-end at a
  broken checkout link, because there is no checkout (§ Non-goals).
- After a failed generation: explicit confirmation that nothing was consumed.
  This is the moment trust is won or lost, and it should not be inferred from a
  balance that happens not to have moved.

---

## Read first

`CLAUDE.md` in full — §7, §11, §14, §15, §16, §26 — `docs/phases/PHASES.md`, and:

| Source | Why |
| --- | --- |
| `docs/decisions/0017-*` | The reserve→commit→release state machine this phase mirrors, and the ceilings it must not relax. |
| `docs/decisions/0023-*` | Why the generation caller is authenticated at all. This phase is only possible because of it. |
| `docs/decisions/0019-*` | The unverified flux-2-max price. |
| `apps/api/sitara/generation/admission.py` | `enforce_live_admission` — where the token gate slots in. |
| `apps/api/sitara/generation/budget.py` (and its Lua) | The existing atomic reserve. Read it before writing a second one. |
| `apps/api/sitara/designs/views.py` (`DesignGenerateView.post`) | The gate order in §6. |
| `apps/api/sitara/generation/tasks.py` | Terminal transitions — where COMMIT and RELEASE hang. |
| `apps/api/config/settings.py` | `LIVE_GENERATION_PRICING_PROFILE`, `MAX_REFINEMENTS`, spec token bounds. |
| `docs/phases/phases-22.md` §Part D | The shop-account ownership decision this phase assumes. |
| `docs/phases/phases-23.md` | A hard prerequisite (§7) — a refinement must work before it is charged for. |

### Findings already established — verify, do not re-derive

- `ANTHROPIC_MODEL` defaults to `claude-sonnet-4-6`; `DESIGN_SPEC_MAX_INPUT_CHARS`
  is 20,000 and `DESIGN_SPEC_MAX_OUTPUT_TOKENS` 8,192, with a settings comment
  recording ~5,800 output tokens measured live.
- `DEFAULT_IMAGE_MODEL` is `black-forest-labs/flux-2-max`; ADR 0019 records
  ~$0.07 as an estimate.
- `MAX_REFINEMENTS = 1`.
- `_require_account` runs before ownership resolution in `DesignGenerateView.post`.
- `_budget_preflight` is a UX optimisation; the authoritative atomic reservation
  happens inside the Celery task, after the queue.
- Phase 16 already runs a stuck-job reconciliation task on Celery Beat.
- Email verification is a recorded Phase 3B non-goal.
- The development Anthropic key is at its account cap until 2026-09-01.

---

## Required commit boundaries

1. `feat(tokens): add the durable token account and append-only ledger`
2. `feat(tokens): reserve, commit and release a token around a generation`
3. `feat(tokens): charge a refinement its own token`
4. `feat(tokens): grant five free draws on first generation`
5. `feat(api): refuse a generation with no tokens`
6. `feat(admin): allocate tokens with a mandatory audited reason`
7. `feat(frontend): show the balance and what each action will cost`
8. `docs(phase-24): record the token ledger and the pricing basis`

---

## Automated tests

**The ledger** — balance never goes negative, at the database constraint and not
only in application code; a correction is a compensating entry and no row is ever
updated or deleted; the cached balance always equals the sum of the ledger
(assert it as a property, over a generated sequence of operations, not a fixed
case).

**Reserve/commit/release** — success commits exactly one token; **the refine
endpoint behaves identically to the generate endpoint on every one of these
assertions**, run as the same parameterised suite over both rather than a
generate-only suite plus a thin refine smoke test; **every** terminal
failure releases it, tested per cause (budget refusal in the worker, provider
timeout, safety rejection, storage failure, enqueue failure); a retried request
with the same idempotency key reserves once, not twice; two concurrent generates
on one account with one token — exactly one succeeds, tested against PostgreSQL
with real concurrency, not mocks; a stuck job's reservation is released by the
existing Beat task.

**Ordering and disclosure** — an anonymous caller still gets `401` before any
token logic runs; a signed-in caller's foreign design still gets an
indistinguishable `404` before any balance is read; a zero-balance caller gets
`402 insufficient_tokens` and **no** token is reserved; a throttled caller with
tokens keeps their token.

**Demo mode** — a demo generation *and a demo refinement* reserve nothing, commit
nothing, consume no free draw, and leave the balance byte-identical.

**Disclosure before the spend** — a caller at exactly one token is told before
generating that refining will need another; the refine control is disabled with a
visible reason at zero rather than failing when pressed; the balance is described
as draws, never as concepts.

**The free grant** — materialises on first generation, not at registration; is
issued exactly once per account under concurrency; five *draws* rather than five
concepts, so a free refinement decrements the same balance; no IP-derived limit
exists on the grant path (dropped — see §8) and no raw address is stored
anywhere.

**Admin** — allocation without a reason is refused; over the per-allocation cap is
refused; a non-staff user cannot reach the action; the ledger is read-only in
admin; the reason never appears in a log, a Sentry payload, an API response to
the account holder, or any provider request (assert the negatives).

**Cost controls unchanged** — ADR 0017's throttles, daily count limit and
micro-USD ceiling behave identically with tokens present. Assert that holding
tokens bypasses none of them; this is the §26 prohibition and it needs a test
that would fail if someone relaxed it.

## Commands and validation

CLAUDE.md §20 in full. Concurrency tests must run against PostgreSQL. The local
`.env` may have live generation on, so the safety overrides go on the command
line explicitly.

## Manual checkpoint

In demo mode: register, generate five times, confirm each is free and the counter
decrements honestly; the sixth is refused with a clear route to more; allocate
tokens through admin; the sixth then succeeds and the balance decrements; force a
failure and confirm the token comes back and the screen says so.

**No live checkpoint and no real money in this phase.** The pricing table in §3 is
a recommendation on unverified provider figures, and the reconciliation in §1 is
a precondition of selling anything.

## Non-goals

- **Any payment provider.** No Stripe, no checkout, no webhooks, no PCI surface.
  Money changes hands outside the application and you allocate manually — that is
  what the admin page is for. A future payments phase would need: an idempotent
  webhook receiver, a reconciliation job against the ledger, refund handling that
  writes compensating entries, and its own security review.
- Token expiry, transfer between accounts, or refunds in-app.
- Per-boutique multi-seat accounts. One login per shop; staff seats are a
  possible later phase, not this one.
- Email verification (its own phase, and the real fix for §8).
- Relaxing any ADR 0017 ceiling.
- Charging for anything other than a generation.

## Documentation and decision record

- **ADR 0030 — generation credits.** Must record: why the ledger is durable
  Postgres and not Redis, in the terms of §4; the reserve→commit→release mirror
  of ADR 0017 and that the two ledgers are independent and both binding; that a
  failed generation consumes nothing; that a refinement costs its own token and
  therefore that **Phase 23 is a hard prerequisite**; the
  free-grant farming exposure stated plainly as **accepted and bounded, not
  removed**, with the layers that bound it and the named thing that would fix it.
- **The pricing basis** goes in the ADR with its date and its unverified
  provenance, so a future reader can tell a measured number from an estimate.
- Update `CLAUDE.md` §7 and §11, and `docs/phases/PHASES.md`.

## Decisions taken by the project owner

All five questions were answered on 2026-08-11. Nothing in this phase is waiting
on the owner except the September price reconciliation, which is a calendar
constraint rather than a decision.

| # | Question | Decision |
| --- | --- | --- |
| 1 | Who holds the account on a shop iPad? | **The boutique.** One account per shop; the walk-in never registers. §8 rewritten: the free five becomes a boutique trial, the hashed-IP grant cap is dropped as protecting nothing while penalising shops behind shared egress, and email verification drops from missing control to ordinary hygiene. The consequence it *creates* is in Phase 22 Part D — one account owns the work produced for many customers, and the designs belong to the store. |
| 2 | Does a refinement cost a token? | **Yes — its own token.** One token = one generation, exactly as first stated. Cost basis per token is $0.172, margins in §3 improve, and §7 is rewritten: both endpoints get the full ledger treatment, the cost is disclosed before the spend, and **Phase 23 becomes a hard prerequisite**. |
| 3 | Are the recommended prices right? | **Accepted** — "we can adjust prices later", which is why they are dated operator configuration beside `LIVE_GENERATION_PRICING_PROFILE` and not constants in code. |
| 4 | Do tokens expire? | **No expiry**, free or purchased. Simpler, kinder, and at this volume the carrying cost is negligible. A shop that stops trading keeps its balance; that is bookkeeping, not risk. |
| 5 | When to re-measure provider prices? | Blocked until **2026-09-01** by the Anthropic account cap — see below. Build and test now; sell after. |

### What these answers changed

- **§7 inverted.** A refinement charges, so the refine endpoint gains the whole
  reserve→commit→release path rather than riding on the generate reservation.
  More code, clearer rule.
- **The free five is five *draws*, not five concepts.** The UI must say "draws
  remaining", or a shop's first refinement reads as an unannounced charge.
- **Margins rose** — 20× at £3 rather than 12× — because a token now buys one
  provider round-trip rather than two.
- **A sequencing constraint became hard.** Phase 23 before Phase 24, recorded in
  the ADR, because charging for a refinement that provably cannot change the
  image is not defensible at any price.
- **§8 shrank** from the centre of the phase to a small feature.

## The one thing still blocked: the Anthropic account cap

> **Owner question (2026-08-11):** "How do I remove the account cap or should I
> change the key?"

**Changing the key will not help.** A usage cap is enforced on the
*organisation*, not on the credential. A new key issued inside the same
organisation hits the same ceiling on its first call. A key from a *different*
organisation would work, and is the wrong move — it splits billing, splits usage
history, and leaves a second set of credentials to manage and rotate.

The cap is raised in the Anthropic Console, and which lever applies depends on
which limit was hit:

- **A spend limit you set yourself** — Console → *Settings* → *Limits* (or
  *Billing*). Self-imposed, so raise it yourself, effective immediately.
- **A usage tier ceiling** — tiers advance on cumulative spend and account age.
  Adding credits is what advances them; it is not instant in the way a
  self-imposed limit is.
- **A monthly invoiced budget**, if the organisation is on invoiced billing —
  that one goes through Anthropic support rather than the Console.

Check *Limits* first; if the number shown there is one you chose, that is the
whole answer.

**Two things this does not change**, and both matter more than the cap:

1. **Raising it enables no spend from this repository.** `DEMO_MODE=true`,
   `ALLOW_PAID_AI_CALLS=false` and `LIVE_GENERATION_ENABLED=false` are separate
   gates and all still closed. A raised cap plus a working key still produces
   exactly zero provider calls — which is the design (§7 of `CLAUDE.md`), not an
   oversight.
2. **The price reconciliation still needs a real invoice**, not a working key.
   The point of waiting is to compare recorded prices against what was actually
   billed. That needs a budgeted live run and a statement afterwards, so
   September is the earliest honest date whichever lever you pull.
