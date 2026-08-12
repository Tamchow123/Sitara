# 0023 — An account is required to generate a concept

- **Status:** accepted
- **Date:** 2026-08-11
- **Deciders:** Sitara project owner
- **Phase:** Phase 21 (see ../phases/PHASES.md)
- **Later:** an assumption this shares with ADR 0004 — that a design's owner is
  the person who answered the questionnaire — was **superseded 2026-08-11 by ADR
  0027**. The account that must be signed in to generate is the boutique's, not
  the customer's, and the customer never registers at all. The requirement below
  is unchanged; who satisfies it is.
- **Related:** ADR 0003 (session authentication), ADR 0004 (private design
  ownership — this **re-frames** it rather than replacing it), ADR 0027 (the shop
  owns the design), ADR 0017
  (live-generation security and cost controls), ADR 0021 (account render delivery
  by email), ADR 0022 (caller-named render attachments), ADR 0024 (the account
  concept gallery)

## Context

Until Phase 21 a visitor could do the entire journey anonymously: answer the
questionnaire, attach references, generate a concept, refine it once, and annotate
the result. Ownership rested on the Django session's workspace pointer (ADR 0004),
which is private and works — but only for as long as that browser session lasts.

Three things made that unsatisfactory at exactly one point in the flow:

1. **Generating is the moment Sitara spends money.** ADR 0017's ceilings, throttles
   and daily budget all key off a session or a hashed IP for an anonymous caller,
   which are the weakest identities the system has.
2. **A concept is worth keeping.** An anonymous concept is reachable only from the
   browser that made it, and only until that session expires. Someone who has just
   spent ten minutes describing their wedding outfit has no way back to the result.
3. **Phase 21 gives the result a destination.** The stylist can email the render to
   themselves (ADR 0021) and find earlier concepts in a gallery (ADR 0024). Both
   need an account address and an account to hang the history on. A flow where the
   concept exists but the account does not leaves both features stranded.

The obvious move — require an account up front — was rejected by the project owner
before implementation began: nobody should be asked to register before they can see
what they would be registering for.

## Decision

**Only the terminal action needs an account.** `POST /designs/<id>/generate/` and
`POST /designs/<id>/refine/` require an authenticated user. Everything before them
is unchanged and still open to an anonymous visitor:

- `GET /questionnaire/active/`
- `POST /designs/`, `PATCH /designs/<id>/`, `GET /designs/<id>/`
- `POST /designs/<id>/validate/`
- the inspiration catalogue and the design's own private reference uploads

An anonymous caller gets `401` with the stable machine code
`authentication_required` and a message that says the answers are saved. Not a
redirect: this is a JSON API, and a 302 to a login page is not something an API
client can act on.

The frontend replaces the review screen's *Generate my concept* button with a
*Sign in to generate* link carrying `?next=<current path>` through `safeNextPath`,
and offers registration alongside it with the same destination. `/register` now
honours a return path for the first time rather than always landing on `/account`.

**Nothing auto-fires on return.** The visitor comes back to their review screen and
must press again. An automatic generation would spend a paid render on a click made
minutes earlier for a different purpose.

### ADR 0004 is re-framed, not replaced

Anonymous ownership stays exactly as ADR 0004 defines it, and it is still the
mechanism that carries a design through the questionnaire and across the login
boundary. What changes is only that one paid, terminal action now additionally
requires the workspace's owner to be an account. Anonymous drafting, the session
workspace pointer, the lazy claim at the next design request, and the rule that a
workspace owned by another user is never transferred are all untouched.

### The account check runs before the ownership lookup

Both views call `_require_account()` first, so an anonymous caller naming a design
UUID gets `401` rather than the `404` that ownership filtering would produce. This
is a **second convention** alongside the repository's usual "indistinguishable 404"
rule (CLAUDE.md §15), and it is deliberate: a `401` on an endpoint that refuses
every anonymous caller identically reveals strictly less than a `404` would, because
it is returned before any design is looked up and is therefore independent of
whether that design exists. Ordering it the other way would mean resolving a private
row for a caller who cannot be permitted to act on it in any case.

## Consequences

### Cost control improves, but that is a consequence and not the justification

Every generation is now attributable to an account, which makes ADR 0017's
per-identity ceilings meaningfully stronger than session- or IP-keyed ones. This was
**not** the reason for the decision — the reason is that a concept needs somewhere
to live — and it must not be cited as one, because a cost-control justification
would invite widening the gate to endpoints that cost nothing.

### The refusal must not lose work

The answers live on the server, so they survive the round trip by construction. The
copy says so, and the frontend keeps the whole review rendered behind the sign-in
prompt rather than replacing it. A regression test asserts both halves of that
message, because a message conveying only one of them would still pass a naive
substring check.

### The lazy-claim window is now on everyone's path — accepted, not removed

ADR 0004 claims an anonymous workspace for the signing-in user **lazily**, at the
next design request, not during login. Between signing in and that next request the
workspace is unclaimed, and a different account signing into the same browser
session could claim it first.

This behaviour is unchanged by Phase 21. What changes is its **frequency**: before,
only a visitor who happened to sign in mid-design passed through the window; now
every first-time user does, because the flow routes them through sign-in at the
moment they have a complete design. The severity is unchanged — the outcome is a
safe `404`, no data is lost, nothing crashes, and it still requires an attacker to
already share the victim's live browser session, which is a shared or public
computer. The exposure is **accepted and recorded**, not eliminated. It must not be
described anywhere as removed or mitigated.

The security reviewer raised this against the backend slice and the reliability
reviewer against its documentation; both judged it minor, and both asked for it to
be written down here rather than carried as a silent assumption.

### What would trigger revisiting this

- A decision to claim the workspace eagerly (see below), which would close the
  window structurally.
- Any proposal to widen the gate beyond `generate` and `refine`. The questionnaire
  staying anonymous is the whole point of the decision, and
  `test_answering_the_questionnaire_still_needs_no_account` exists to fail loudly
  if a future change sweeps `validate` or the draft endpoints into the gate.
- Any proposal to auto-fire a generation on return from sign-in.

## Alternatives considered

**Require an account before the questionnaire.** Rejected by the project owner:
asking someone to register before they have seen anything is the single most
effective way to lose them, and it would make the product's cultural depth
invisible to exactly the people it is for.

**Claim the workspace eagerly, inside login and registration.** This would close the
lazy-claim window structurally rather than accepting it. Rejected for this phase
because it puts a write to the designs domain inside the accounts login path — a
coupling this codebase has consistently avoided, and one that would make a failure
in design-workspace resolution able to fail a sign-in. Recorded here as the known
structural fix if the window's frequency ever stops being acceptable.

**Redirect (302) instead of refusing (401).** Rejected: the endpoint is a JSON API
consumed by `fetch`, and a redirect to an HTML login page is not an outcome a client
can render honestly. The frontend owns the routing; the API states the reason.

**Let anonymous visitors generate and require an account only to email or gallery
the result.** Rejected: it leaves the expensive action attributable to nothing, and
it produces concepts that cannot be reached again, which is the problem the phase
exists to fix.

**Auto-fire the generation on return from sign-in.** Rejected: it spends a paid
render on consent given for a different act, and a visitor who changed their mind
during registration has no way to stop it.
