# 0027 — The shop owns the design, and the shared device

- **Status:** accepted
- **Date:** 2026-08-11
- **Deciders:** Sitara project owner
- **Phase:** Phase 22 (see ../phases/PHASES.md)
- **Related:** ADR 0004 (private design ownership) and ADR 0023 (an account is
  required to generate) — this **supersedes an assumption both are built on**;
  ADR 0021 (account render delivery by email); ADR 0024 (the account concept
  gallery — this gates it off); ADR 0026 (in-store reference capture)

## Context

Phase 22 asked a question the earlier phases had not had to answer: on a
shop-floor iPad used by one boutique for many walk-in customers, whose account is
it, and whose designs are they?

> **Owner decision (2026-08-11):** "The boutique, don't worry about the account as
> each shop would have their own account."

> **Owner decision (2026-08-11):** "the designs actually belong to the store not
> the customer but the store can decide to give the customer the designs if they
> want to."

## Decision

### 1. A concept produced on a shop's iPad is the shop's work product

The shop commissioned it, the shop paid the token for it, and the shop decides
whether and when to pass it to the customer. The customer has no claim on it and
no account through which to hold one.

This settles the walk-in question cleanly: **the walk-in customer never
registers.** One account per shop, no sign-up friction in front of a customer, no
per-visitor free tier to farm.

### 2. This supersedes an assumption ADR 0004 and ADR 0023 are built on

Both were written as though a design's owner is the person who answered the
questionnaire. From here, **one account owns the work produced for many different
customers, permanently and by design.**

That is a change to the ownership model, not a detail, and it is recorded as a
decision taken rather than as drift. The mechanics are unchanged — anonymous
session workspaces, the lazy claim at the next design request, the
indistinguishable 404 for anything inaccessible — but what they are protecting is
now a boutique's book of work, not one bride's private design.

### 3. Owning the work and being its data controller are both true

The shop owns the designs. The shop is also the data controller for the
photographs and the annotation notes inside them. These are compatible, not in
tension: ownership of the work product settles who may share it; it does not
remove the shop's ordinary obligations over personal data.

What remains after the ownership question is settled is narrower, and should not
be argued as an ownership question. It is this: **on a shared screen, work
produced for one customer should not be put in front of the next one.** Not
because the first customer owns it — she does not — but because a shop showing
customer A's concepts, fabrics, colours and annotation notes to customer B is a
shop making a choice it did not intend to make. The annotation note is the
sharpest case: the most personal free text in the product, recording what someone
disliked about a garment she intends to wear.

The product should make the responsible choice the easy one rather than leave it
to whoever is holding the iPad.

### 4. The account concept gallery ships switched off

> **Owner decision (2026-08-11):** "Disable account gallery for now we'll think of
> a secure way of displaying it later."

`ACCOUNT_GALLERY_ENABLED` defaults to false. **Gated, not deleted** — everything
ADR 0024 built stays in the codebase, tested, and comes back by setting one flag,
so re-enabling it later is a decision rather than a rebuild.

It is a third independent operator gate on the same pattern as
`LIVE_GENERATION_ENABLED` and `ACCOUNT_EMAIL_DELIVERY_ENABLED`: nothing else
turns it on, and being signed in is not enough. `GET /api/v1/designs/` answers
`503 gallery_disabled` — a controlled refusal, not a 404 that would imply the
surface was never there. Only the list is gated; `POST` still creates designs,
because the walk-in flow starts there and gating it would end the product rather
than the gallery.

### 5. "Finish and hand back", and an idle timeout behind it

An explicit control ends the walk-in session: it drops the workspace pointer,
revokes any live handoff code (ADR 0026), drops in-memory state and returns to a
neutral start screen.

**It does not sign the shop out.** That is the one real simplification the
shop-account decision buys: the account is the boutique's and the next customer
using it is the intended state, so a login screen between every customer would
cost the stylist a password each time and buy no privacy.

**It deletes nothing.** The concepts are the shop's work product.

Behind it, `WALK_IN_IDLE_TIMEOUT_SECONDS` (30 minutes) does the same thing
unprompted, because customers walk away and tapping the button is a habit
somebody has to remember. It is enforced **server-side**, on the workspace's own
`last_seen_at`, in both the read and the create resolution paths — a client-side
prompt is not a boundary, because a backgrounded tab runs no timers and a closed
lid runs nothing at all.

Thirty minutes is long enough not to interrupt a real consultation — a bridal
questionnaire is not quick, and there are pauses for tea and for fetching a
sample — and short enough that a customer who walked out is gone before the next
one sits down.

No "remember me" on the customer-facing flow. Cookies stay `SameSite=Lax` and
HttpOnly.

### 6. The emailed render goes to the shop

> **Owner decision (2026-08-11):** "By default the user's email which will be the
> shops and then the shop can send it to the customer later if they desire."

This is exactly what ADR 0021 already enforces — the recipient is
`request.user.email`, read server-side, never client-supplied — so **no code
changes**, and §26's prohibition on a client-supplied recipient stands untouched.
What changes is copy: the render goes to the shop's own account address, and
passing it to the customer is the shop's to do.

If a bride is ever to receive her concept directly, that is a separate phase with
its own verification design — not a copy change and not a field on this endpoint.

## Consequences

### A concept is reachable only during the session that produced it — accepted, not mitigated

With the gallery gated off and `ACCOUNT_EMAIL_DELIVERY_ENABLED` still false and
still never once exercised against a real SMTP server, there is **no in-app way
to find a past concept**. The emailed render is the only durable route out of the
product, and that route is closed.

This is a real product limitation, not a detail, and it is accepted rather than
mitigated. Read it alongside "A handed-back design stays reachable to the shop"
below, which qualifies it: "only during the session" is about what the product
OFFERS a route to, not about what a URL still in the browser's history resolves
to for an account that owns the design. It raises the stakes on the email gate considerably: until that gate
opens and its checkpoint is run, a concept generated in a shop can be seen only
while the session that produced it is still open. The gallery's own switched-off
message says so in as many words, and an earlier draft of that message which
implied concepts were "emailed as usual" was caught in review and removed —
precisely because softening this consequence is the failure mode.

### A handed-back design stays reachable to the shop — accepted, not removed

Review of the hand-back control raised this, and it is worth stating plainly
rather than leaving as an unexamined default.

"Finish and hand back" ends the browser's claim on a workspace. It does not end
the shop's ownership of the work, because §1 says the shop owns it:
`accessible_designs` gives a signed-in account every design it has ever
produced. The shop is signed in all day. So a design URL still sitting in the
iPad's history still resolves after a hand-back, and a deliberate
back-navigation reaches it.

Two things follow, and both are deliberate.

The exposure is **accepted and bounded, not removed**, in the same words this
project is required to use for ADR 0019's rights override, ADR 0021's three,
ADR 0022's fourth and ADR 0026's bearer credential. Its bounds: it needs a URL
the browser still holds and a deliberate act to go back to it; nothing in the
product offers a route to it, because the gallery — the surface that would make
past work browsable — is gated off by §4; and an **anonymous** browser has no
ownership to fall back on, so for it the workspace is genuinely gone, answered
by the same indistinguishable 404 as a design that never existed. Both halves
are pinned by tests, so a future change has to face the decision rather than
discover it.

What the client side *can* do about it, it does. The control performs a full
document load rather than a soft in-app navigation, which discards every
in-memory copy of the previous customer — the cached result payload, the
short-lived signed image URLs, any annotation state still mounted — and
replaces the current history entry so the screen just left is not one
back-press away. It cannot erase the entries before that; no web API can, and
claiming otherwise would be the failure mode this section exists to avoid.

Closing it properly means deciding what a signed-in shop may see of its own
past work on a shared screen — which is exactly the deferred question in §4 and
in "What would trigger revisiting this". Doing it here, as a side effect of a
button, would have been that redesign taken without review.

### Other consequences

- One account accumulating many customers' work makes the gallery's eventual
  return a harder design problem than it was when ADR 0024 shipped, not an easier
  one. Whatever comes back has to answer "whose work is on this screen right now"
  in a way a list ordered by date does not.
- The walk-in flow is unchanged for the customer: the questionnaire stays
  anonymous (ADR 0023), and only the terminal generate and refine actions need
  the shop to be signed in — which it is, all day.
- Ending a session is cheap and repeatable, so a cautious stylist can tap it
  between every customer without cost. The idle timeout means a careless one
  still gets most of the benefit.

### What would trigger revisiting this

A secure design for displaying past work on a shared device — most likely one
that scopes the view to something narrower than "the account", or that requires
a deliberate act to widen it. That is the work the owner deferred, and the flag is
there so it can be picked up without rebuilding what ADR 0024 already got right.

## Alternatives considered

**One account per customer.** Rejected by the owner decision, and rightly: it
puts a registration form in front of a walk-in customer before she has seen
anything worth registering for, which is the same objection ADR 0023 records
about the questionnaire.

**A device or kiosk identity.** Rejected. The shop account owning the work
produced for many customers *is* the model now; adding a device identity would be
a second, parallel notion of who is using Sitara for no gain.

**Deleting the gallery.** Rejected. It works, it is tested, and the problem is
where it is shown rather than what it does. A flag keeps the option open at the
cost of one setting.

**Signing the shop out on "Finish and hand back".** Rejected once the account
became the boutique's. It would cost a password per customer and protect nothing
the workspace reset does not already protect.

**A client-side idle prompt instead of a server-enforced timeout.** Rejected. A
backgrounded tab runs no timers, and the boundary has to hold when the browser is
not running.
