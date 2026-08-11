# Sitara Phase 21 — Named concept delivery, sign-in before generation, and the account concept gallery

**Status:** specification only. Nothing here is implemented. Written to be picked
up on its own branch. Requirements confirmed by the project owner; their answers
are recorded inline as **Owner decision** so a later reader can tell a decision
from an inference.

## Main objective

Four changes. The first three are on the path between a finished concept and the
stylist's own inbox; the fourth moves where the product asks for an account, and
is much the largest.

1. **The stylist names the file.** Pressing *Send to account* asks for a name
   first, and the attachment arrives under that name. The name is remembered for
   later sends of the same thing, and each thing may be sent at most three times.
2. **A stylist can find their earlier concepts from their profile.** The account
   page grows a gallery. Today it carries a placeholder that literally promises
   this ("your design gallery — arrive in later phases",
   `apps/web/src/app/account/page.tsx`).
3. **Sending defends itself against an account-less caller** — which after change
   4 should be nearly unreachable, and must still be enforced server-side.
4. **Generation requires an account.** A visitor may complete the entire
   questionnaire anonymously, but the final action before a concept is produced
   requires sign-in or registration, and returns them to exactly where they were
   with their answers intact.

> **Scope warning, for the owner to rule on.** Change 4 is not the same size as
> the other three. It alters the permission model of the generation endpoints,
> re-frames ADR 0004's anonymous-ownership story, and invalidates the anonymous
> path that most of the existing Playwright suite drives — including the first
> test in `annotations.spec.ts`, which exists specifically to prove an anonymous
> owner is refused a send. It is specified here in full, but it is a reasonable
> candidate for its own phase. See **Question 1**.

## The two invariants this phase changes — read before designing anything

### A. The attachment filename is currently server-owned

`apps/api/sitara/media/account_delivery.py` is the single choke point through
which every outbound email passes, and its docstring says:

> `filename` and `content_type` are server-owned constants from
> `sitara.media.annotation_render`, **never derived from a design title or any
> other user input**, so the attachment headers carry nothing private and offer no
> injection surface.

Change 1 contradicts that on purpose. The sentence must go, but the reasoning
behind it does not stop being true, and each half needs a different answer:

- **"no injection surface"** — must be *restored by validation*, not abandoned. A
  filename reaches an email header; `\r` or `\n` there is header injection. See
  §1.
- **"headers carry nothing private"** — **cannot** be restored, and must be
  *accepted and recorded*. Whatever a stylist types travels in the message
  headers, shows in their mail client, and is retained by the relay and the
  receiving host. Tell the user before they type. **Never describe this as
  mitigated or removed** — the same discipline CLAUDE.md §26 requires for ADR
  0019 and ADR 0021.

The recipient rule is **untouched and absolute**: the address is always
`request.user.email`, read server-side, and no field of any request may express a
destination. Change 1 renames the attachment; it changes nothing about where the
message goes.

### B. The delivery marker row holds no user content

CLAUDE.md §14 states the marker row stores **"state, counters and timestamps only
— never a recipient address, note text or rendered bytes"**, because a durable row
survives into backups and admin views.

**Owner decision (Q1): a repeat send remembers the previous filename.** That
requires persisting user free text on that row. Strictly the rule is not broken —
a filename is none of the three things it names — but the reasoning applies, so
the phase must add the row's protections rather than assume them:

- Bounded length, validated before storage.
- **Never logged**, in any path, including failure paths.
- Read-only in admin, and not shown in list displays where it would be
  incidentally readable next to an account.
- Removed when the design or version is deleted, and covered by the existing
  retention purge.
- Not returned by any endpoint other than the one that pre-fills the field for the
  owner of that design.

Record it in the ADR as a deliberate narrowing of §14's claim, with the sentence
updated rather than left standing while being false.

## Safety mode

Unchanged and non-negotiable:

- `DEMO_MODE=true`, `ALLOW_PAID_AI_CALLS=false`, `LIVE_GENERATION_ENABLED=false`,
  `ACCOUNT_EMAIL_DELIVERY_ENABLED=false`. This phase adds no provider call.
- Tests and CI open **zero SMTP connections** and assert the locmem backend.
- **"Fully functional" means complete, wired end to end, and exercised against the
  locmem backend.** It cannot mean mail is being delivered:
  `ACCOUNT_EMAIL_DELIVERY_ENABLED` ships false, and enabling it is an operator
  decision with its own prerequisites (a real `DEFAULT_FROM_EMAIL`, a
  non-placeholder host, both validated at startup in production). No SMTP send has
  ever been performed in this project; nothing in this phase may imply otherwise.
- A present mail configuration must never enable sending by itself.

One welcome side effect of change 4, worth stating because it argues *for* it:
tying generation to an account makes per-account cost control possible for the
first time, which complements Phase 16's session/IP throttles rather than
duplicating them.

## Read first

`CLAUDE.md` in full (§7 §8 §9 §11 §14 §15 especially), `docs/PROPOSAL.md`,
`docs/phases/PHASES.md`, and:

| Source | Why |
| --- | --- |
| `docs/decisions/0021-*` | Account render delivery. Amended here. |
| `docs/decisions/0020-*` | Annotation workspace and the annotated send. |
| `docs/decisions/0004-*` | Private design ownership and the workspace claim. Re-framed by change 4. |
| `docs/decisions/0017-*` | Live-generation security and cost controls; where limits already live. |
| `docs/decisions/0012-*` | Private design-image storage and signed URLs. |
| `docs/phases/phases-19.md` §8 | The send endpoints as built, including §8.1's recipient rule. |
| `apps/api/sitara/media/account_delivery.py` | The choke point being changed. |
| `apps/api/sitara/designs/views.py` | `_DesignVersionSendView`, and the generation endpoints change 4 gates. |
| `apps/api/sitara/designs/queries.py` | `accessible_designs` — ownership filtering before lookup. |
| `apps/web/src/features/annotations/SendToAccountButton.tsx` | The send control. |
| `apps/web/src/app/account/page.tsx` | The placeholder the gallery replaces. |
| `apps/web/src/lib/navigation.ts` | `safeNextPath` — the existing open-redirect guard. Reuse it. |
| `apps/web/e2e/journeys.spec.ts`, `generation.spec.ts`, `annotations.spec.ts` | All drive anonymous generation. Change 4 invalidates that. |

### Findings already established — verify, do not re-derive

- Both send endpoints currently accept **no request body at all**, and an AST test
  asserts `django.core.mail` is imported in exactly one module, catching
  proper-prefix imports and attribute chains. Keep it passing.
- `recipient_for(user)` takes the **user row, never an address string**, so no call
  site can express a destination. Keep that shape.
- An anonymous owner currently gets `409 email_recipient_unavailable`, no
  fallback. That response must survive as server-side defence even once change 4
  makes it hard to reach from the UI.
- `safeNextPath` already rejects absolute URLs, protocol-relative `//`, `://` and
  backslashes.
- `GET /designs/` lists owned designs with `latest_job`, ownership-filtered before
  lookup, and a list request must not create an empty workspace.
- `MAX_REFINEMENTS = 1`, so a design has **at most two versions**. That bounds the
  arithmetic in §2.
- Existing send limits are **rates** (`ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR`,
  `_PER_DAY`, `_IP_LIMIT_PER_HOUR`, `ACCOUNT_EMAIL_RECIPIENT_LIMIT_PER_DAY`). The
  cap in §2 is a different thing — a lifetime total per artefact — and must not be
  implemented by reusing a rate limiter.

## Required commit boundaries

One reviewable concern each, in dependency order.

1. `feat(email): accept a validated caller-named attachment`
2. `feat(designs): cap sends per version and remember the chosen name`
3. `feat(api): take a filename on the send endpoints`
4. `feat(frontend): name the file before sending`
5. `feat(api): require an account to start a generation`
6. `feat(frontend): ask for sign-in before producing a concept`
7. `test(e2e): register before generating in every journey`
8. `feat(api): list owned concepts for the gallery`
9. `feat(frontend): add the account concept gallery`
10. `docs(phase-21): record the delivery and sign-in decisions`

---

## Part A — A caller-named attachment, capped and remembered

### 1. Sanitise at the choke point, validate at the edge

Two layers, because they answer different questions.

**At the edge (serializer).** A `RenderSendSerializer` with exactly one optional
field, `filename`. Reject every other field as unknown (§15 already requires
rejecting unknown write fields rather than ignoring them). Validation must be
**total over arbitrary JSON** — a number, `null`, a list or a nested object
becomes a controlled 400 with the stable code `filename_invalid`, never a
`TypeError`.

**At the choke point (`account_delivery.py`).** A
`safe_attachment_filename(raw: str | None) -> str` that is safe for *any* input,
because the choke point must not depend on its caller having validated. Same
reasoning that makes `recipient_for` take a row.

| Step | Rule | Why |
| --- | --- | --- |
| 1 | Unicode NFC normalise | Two spellings of one name should not behave differently |
| 2 | Reject any C0/C1 control character, including `\r`, `\n`, `\t` | **Header injection.** The one that matters most |
| 3 | Strip `/`, `\`, `:`; reject any `..` | A filename is not a path |
| 4 | Collapse internal whitespace to single spaces; trim; strip leading dots | `"..hidden"`, ragged spacing |
| 5 | Cap the base name at 60 characters | Relay and client limits; a header is not an essay |
| 6 | Strip any extension the user typed, then append `.png` server-side | The client never chooses the type. `"dress.png"` must not become `"dress.png.png"`; `"dress.exe"` survives only as `dress.png` |
| 7 | If nothing survives, fall back to the existing server-owned constant | An unusable name is a naming failure, not a send failure |

**Owner decision (Q2): "whatever is best" — so this phase decides, and the
decision is that non-ASCII letters are allowed.** Refusing them would mean a
stylist cannot name a concept in Devanagari, Urdu or Bengali, which is exactly the
flattening CLAUDE.md §2 forbids. Django encodes non-ASCII attachment filenames per
RFC 2231; prove that with a test that asserts the generated header rather than
trusting the library.

Rejected characters produce a 400 at the edge rather than a silent repair, so the
stylist learns their name was not used. The choke point still repairs
defensively.

`send_render_attachment` keeps its keyword-only shape and gains nothing that can
express a destination. **Rewrite its docstring**: the sentence quoted above is now
false and must not be left standing.

**Never log the filename.** It is free text about a garment someone intends to
wear — the same category as a note.

### 2. Three sends per artefact, counted separately

**Owner decision (Q1): at most three sends, with a separate counter for each
modification and for the annotation, still three each.**

Interpretation, to be confirmed (**Question 2**): the counter is keyed by
**(`DesignVersion`, kind)**, where kind is `plain` or `annotated`. So the original
version's plain render has its own three, its annotated render has its own three,
and a refinement's two kinds have their own three each. With `MAX_REFINEMENTS = 1`
that is a ceiling of **twelve emails per design**, which is worth seeing written
down.

Design requirements:

- The counter is a **lifetime total, not a rate**, and must be durable — it belongs
  on the delivery marker row, which already exists to hold counters, not in a
  cache. A cache eviction must never hand back three more sends.
- Make the ceiling a named setting (`ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER`, default
  3) rather than a literal, so an operator can lower it without a code change.
  Parsing stays strict per §8.
- Increment under a row lock inside the same transaction that records the send, so
  two concurrent requests cannot both see "two used" and both proceed. This is the
  same `select_for_update` discipline §16 requires where invariants span requests.
- **Decide and state whether a failed send consumes a count.** Recommendation: it
  does **not** — a relay failure is not the user's doing — but the attempt still
  counts against the existing per-hour and per-IP *rate* limits, so failure cannot
  be used to grind. Confirm (**Question 3**).
- Refuse the fourth with a distinct, stable code (`send_limit_reached`) and a
  message that says how many were used, not a generic 429. The UI must show the
  remaining count *before* the last one is spent, so the ceiling is never a
  surprise.
- The existing rate limits stay exactly as they are. This cap is additional.

### 3. Remember the name

Store the validated filename on the same marker row as the counter, under §B's
protections, and pre-fill the field with it on a later send of the same artefact.
When there is no stored name, pre-fill from the design's own title, truncated —
**never from a note** (CLAUDE.md §7: a note is the most personal free text in the
product, and it must not become a default filename).

### 4. The recipient rule, re-proved now that a body exists

A body removes the structural guarantee that no address could be supplied, so the
guarantee becomes an explicit, tested one. Each of these must 400 **and queue no
mail**:

- `{"email": "attacker@example.test"}`
- `{"to": [...]}`, `{"recipient": ...}`, `{"cc": ...}`, `{"bcc": ...}`,
  `{"from_email": ...}`, `{"reply_to": ...}`
- `{"filename": "x", "email": "attacker@example.test"}` — a valid field beside a
  forbidden one must fail whole, not partially succeed
- `{"filename": "a\r\nBcc: attacker@example.test"}`

And the positive case: with delivery enabled and the locmem backend, a request
whose body names address B, from a user whose row holds address A, delivers to
**A**.

### 5. Both endpoints

**Owner decision (Q3): the plain concept send takes a name too.** Both endpoints
already share `_DesignVersionSendView`; keep them from diverging.

---

## Part B — An account before a concept

### 6. Generation requires authentication

**Owner decision (Q4): a visitor may complete the whole questionnaire
anonymously, but the last action before a concept is produced requires sign-in or
registration.**

Backend:

- The generation endpoints require an authenticated user. An anonymous caller gets
  a controlled, documented refusal with a stable code
  (`authentication_required`) — not a redirect, which is not an API's job.
- The questionnaire, draft and design-creation endpoints **stay anonymous-capable**.
  Nothing about answering questions changes.
- Refinement generation requires authentication for the same reason; in practice
  the user already is.
- This is an API contract change: regenerate the OpenAPI schema, and expect the
  contract-drift check to show it.

Frontend:

- The questionnaire runs unchanged for an anonymous visitor to the very end.
- The final control routes to `/login?next=<current path>` (and offers
  registration — **Owner decision (Q5): yes**), through `safeNextPath`.
- On return, the visitor lands where they left off with their answers intact and
  the generate action now available. Whether it fires **automatically** on return
  or waits for a second press is **Question 4**; the safer default is to wait,
  because auto-firing spends a generation the user has not re-confirmed.

**The failure mode that matters most.** After sign-in, the anonymous workspace is
claimed by the existing Phase 4 mechanism — but ADR 0004 is explicit that **a
workspace owned by another user is never transferred or reused**. If the claim
cannot happen, an anonymous visitor who has just completed a long questionnaire
loses all of it. Before this change that cost a design; after it, it costs the
entire questionnaire at the exact moment we forced the interruption. The phase
must:

- Establish whether that state is reachable at all for a *fresh* anonymous
  session, and if it is, name the sequence.
- If reachable, keep the answers recoverable rather than silently discarding them,
  and never present a claim failure as a generation failure.
- Test it explicitly, not just reason about it.

### 7. The send control keeps its own defence

Change 6 makes an account-less owner of a *generated* concept nearly unreachable,
which means the send path's anonymous branch stops being a user-facing case and
becomes defence in depth. Keep it: `409 email_recipient_unavailable` for an
authenticated user with no address, no fallback, no prompt for an address, and the
server-side ownership check unchanged. Do not delete the tests that cover it on
the grounds that the UI no longer reaches it.

---

## Part C — The account concept gallery

### 8. A list endpoint the gallery can render

Extend the owned-designs list with what a gallery needs and no more: design id,
display title, created and updated timestamps, the demo flag, a version count, and
the versions themselves in creation order with each version's id, number and job
state. **Owner decision (Q6): one card per design, with a refinement's versions
grouped inside it so it reads visibly as one session's work.**

- Ownership filtering before lookup; `Cache-Control: no-store`; a list request must
  not create an empty workspace.
- **No signed image URLs in the list payload.** They are short-TTL bearer URLs that
  must never be persisted, cached or logged, and a list is exactly what gets cached
  by accident. The gallery mints one per card through the existing
  ownership-checked images endpoint, so a URL exists only for a concept actually
  scrolled to.
- Bounded page size. Two hundred concepts must not become a two-hundred-image
  page.
- No storage key, bucket name, endpoint, prompt, or note text in the payload.

### 9. The gallery itself

Replace the "What's next" placeholder.

- A card per design: thumbnail of the latest version, title, when it was made, a
  demo/AI label matching the result screen's, and links to the concept and its
  annotation workspace.
- **Versions grouped within the card**, ordered, so an original and its refinement
  read as one piece of work rather than two unrelated concepts — with the version
  each thumbnail belongs to made explicit, since a refinement looks similar to its
  parent by design.
- **Owner decision (Q5): all concepts appear, including generating and failed
  ones**, labelled by state rather than shown as a broken image. `latest_job`
  already carries what is needed.
- Honest distinct states: loading, empty (with a route into the questionnaire),
  a thumbnail that fails without taking the card or page down, and total failure.
- Accessible: real headings, alt text derived as the result screen derives it,
  keyboard-reachable cards, focus order following visual order, and the version
  grouping conveyed in the accessible name rather than by layout alone.
- Newest first (**Owner decision, Q8** from the previous round).

---

## OpenAPI and generated client

Two contract changes — a request body on the send endpoints, and authentication on
the generation endpoints. Regenerate; never hand-edit:

```powershell
docker compose exec api python manage.py spectacular --file openapi/schema.json --validate --fail-on-warn
docker compose exec web npm run generate:api
```

The schema-freshness and generated-types drift checks both run in CI and must be
clean. The client gains a filename parameter and **must not** gain anything
resembling a recipient parameter.

## Automated tests

Beyond the tests named inline:

**Filename** — table-driven over control characters, CRLF, path separators, `..`,
leading dots, over-long input, a typed extension, input that sanitises to nothing,
whitespace only, and a non-ASCII name. Assert the **generated attachment header**
for the non-ASCII and quoting cases, not just the return value.

**Send cap** — three succeed and the fourth is refused with `send_limit_reached`;
the plain and annotated counters are independent; a refinement's counters are
independent of its parent's; two concurrent fourth attempts cannot both pass
(exercise the row lock, do not assume it); the count survives a cache flush; and
whichever answer Question 3 gets is pinned by a test.

**Remembered name** — pre-filled on a second send; never returned to a
non-owner; absent from logs; removed with the design; read-only in admin.

**Zero SMTP** — the locmem assertion and the single-`django.core.mail`-importer
AST test both still pass.

**Generation auth gate** — anonymous generation refused with the documented code;
questionnaire and draft endpoints still work anonymously; a signed-in user
unaffected; the claim-failure path covered explicitly per §6.

**Gallery** — another user's concepts never appear; no empty-workspace creation on
list; pagination bounds; `no-store`; nothing sensitive in the payload; generating
and failed states; grouped versions in the right order.

**E2E** — this is where change 4 bites. Every journey that generates must register
first, which touches `journeys.spec.ts`, `generation.spec.ts` and
`annotations.spec.ts`. The first annotation test is *specifically* an anonymous
owner being refused a send; decide whether it becomes an API-level test or is
rewritten, and say which in the completion report rather than quietly deleting it.
Add: naming a file and sending it (asserting the queued outcome only), the fourth
send being refused, and the gallery listing a concept generated in the same run.
Zero cost and zero SMTP throughout, in demo mode.

## Commands and validation

Per CLAUDE.md §20 in full. The local `.env` may have live generation on, so
backend runs need the safety overrides explicit. Frontend checks run on the host.

## Manual checkpoint

Operator-run, budgeted, and **not part of the PR**: with
`ACCOUNT_EMAIL_DELIVERY_ENABLED=true` against a real mail host, send one concept
with a deliberately awkward name (non-ASCII, spaces, a typed extension) and
confirm the attachment arrives correctly named with well-formed headers. Until
then the honest claim is "complete and exercised against locmem", nothing
stronger.

## Non-goals

- Any client-supplied recipient, in any field, under any flag. Permanently.
- Sharing, public links, or sending to anyone but the account holder.
- Deleting or renaming concepts from the gallery.
- Note text in an email body, subject, or as a filename suggestion.
- Raising or bypassing the send cap from the client.
- Any change to the image model, prompt builder, or generation cost controls
  beyond the authentication gate.
- Removing anonymous questionnaire use. Answering questions stays anonymous.

## Documentation and decision record

- **ADR 0022 — caller-named render attachments, capped and remembered.** Amends
  ADR 0021. Must state plainly that "headers carry nothing private" is **given up,
  not mitigated**; that the injection surface is closed by validation; that the
  filename is persisted under stated protections and never logged; and that the
  cap is a durable lifetime total distinct from the existing rate limits.
- **ADR 0023 — an account is required to generate.** Re-frames ADR 0004: anonymous
  *questionnaire* use stays, anonymous *generation* ends. Must record the
  claim-failure risk in §6 and how it is handled, and note the cost-control
  benefit as a consequence rather than the justification.
- **ADR 0024 — the account concept gallery.** Records keeping signed URLs out of
  the list payload and minting per card instead.
- Update `CLAUDE.md`: §7 and §14 where they say the send endpoints accept no
  request body and the marker row holds no user content; §11 for the generation
  gate; §26's list keeps "accept a client-supplied email recipient anywhere" while
  narrowing the body claim.
- Update `docs/phases/PHASES.md` with Phase 21.

## Questions for the project owner

Proceeding on the bracketed assumptions; correct any of them.

1. **Should change 4 be its own phase?** It alters the generation permission
   model, re-frames ADR 0004 and requires rewriting most of the Playwright suite.
   Bundled here it will dominate the PR and make the delivery changes harder to
   review. [Assumed **kept here**, since you asked for it in this breath — but it
   is the one I would most readily split.]
2. **Is the cap keyed per (version, kind)?** That is my reading of "a separate
   counter for each modification or the annotation but still max 3 for them", and
   it means up to twelve emails per design given one refinement. [Assumed **yes**.]
3. **Does a failed send consume one of the three?** [Assumed **no** — a relay
   failure is not the user's fault — with rate limits still applying so repeated
   failure cannot be used to grind.]
4. **After signing in at the end of the questionnaire, should generation fire
   automatically or wait for a second press?** [Assumed **wait**: auto-firing
   spends a generation the user has not re-confirmed since being interrupted.]
5. **What should the three-send ceiling say in the UI before it is reached** — a
   visible "2 of 3 sends used", or only a message on refusal? [Assumed
   **visible beforehand**, so the limit is never a surprise.]
6. **Does the cap apply to demo concepts as well as live ones?** Demo sends cost
   no provider money but still cost an email. [Assumed **yes**, since the limit
   exists to prevent mail abuse, not provider spend.]
