# 0021 — Account render delivery by email

- **Status:** accepted
- **Date:** 2026-08-08
- **Deciders:** Sitara maintainers
- **Phase:** Phase 19 (see ../phases/PHASES.md). Phase 18 (E2E tests and
  deployment) is deliberately **skipped**, so this ADR carries no
  deployment-smoke obligation; the live SMTP checkpoint below is the remaining
  manual gate.
- **Related:** ADR 0012 (private design-image storage), ADR 0017
  (live-generation security and cost controls — the fail-closed gate pattern this
  follows), ADR 0020 (private stylist annotation workspace)

## Context

Phase 19 replaces the result screen's *Download image* link with *Send to
account*: the owner asks for a copy of their concept — plain, or flattened with
their annotations — and receives it at their own account address.

That is a new class of operation for this codebase. Every other outbound path
is a response to a request the caller is already authenticated for. This one
sends a private image *somewhere*, asynchronously, over a protocol with no
transactional semantics, to an address. Three things follow from that and they
drive the whole design.

**An endpoint that mails an attachment to a caller-chosen address is an open
relay.** Not a privacy weakness — a piece of spam infrastructure with an
authentication bypass, since the attachment need not even be the caller's own.

**SMTP cannot be made exactly-once.** There is no way to atomically "send the
mail and record that we sent it". Any design here chooses which failure it
prefers.

**A download link and an email are not the same risk.** A signed URL is a
short-lived bearer token the user resolves themselves. An email hands the bytes
to at least two third parties who may keep them.

## Decision

### The recipient is read server-side from the authenticated account. Always.

`request.user.email`, on the server, on every request. **The endpoints accept no
request body at all** — not an ignored address field, no address field to ignore.
The client wrapper `sendRenderToAccount(designId, versionId, kind)` has no address
parameter to pass one through, mirroring the same guarantee in the shape of the
code rather than in a validator that a later change could relax.

An anonymous owner gets `409 email_recipient_unavailable`. There is **no
fallback**: no prompt for an address, no silent success, no "we'll email it when
you sign up".

This is enforced structurally and then guarded by a test that reads the module's
own AST: only `sitara/media/account_delivery.py` may reach `django.core.mail`,
including via a proper-prefix import (`import django`, `from django.core import
mail`) or a literal attribute chain. That guard is itself adversarial — it was
strengthened after a security review demonstrated a real evasion of its first
version planted in the tree.

### A separate capability gate, defaulting closed

`ACCOUNT_EMAIL_DELIVERY_ENABLED` defaults to `false` and is its **own** operator
decision, exactly as `LIVE_GENERATION_ENABLED` is for generation (ADR 0017). It
is not implied by `DEBUG`, by a configured `EMAIL_HOST`, by working credentials,
or by any other flag. Present SMTP configuration must never enable sending by
itself — the same rule §7 states for API keys.

In production, turning it on additionally requires a real `DEFAULT_FROM_EMAIL`
and a non-placeholder host; startup fails closed and names the setting without
echoing the rejected value.

**Tests and CI open zero SMTP connections.** The locmem backend is asserted, not
assumed.

### The annotated render is composed server-side, and the original is never touched

`compose_annotated_png` reads the stored original, draws the marks with the same
halo-then-colour rule the browser uses, and appends a numbered legend. It writes
nothing back: no new `DesignVersion`, no change to `image_storage_key`, bytes,
hashes or processor version. The rendered attachment exists only in memory, for
the life of the task.

Bounded at both ends: `ANNOTATION_RENDER_MAX_BYTES` and
`ANNOTATION_RENDER_READ_DEADLINE_SECONDS` bound the read,
`ANNOTATION_RENDER_MAX_PIXELS` the page, and `ACCOUNT_EMAIL_MAX_ATTACHMENT_BYTES`
(6 MiB) the encoded output — measured against a real worst case of ~4.2 MiB at
200 marks, not guessed. An oversized render is refused with a controlled code
rather than sent.

### Layered throttles, and a claim marker that stores no address

Four dimensions: per-session, per-hashed-IP, per-recipient-per-day and a global
daily count. A `DesignRenderDelivery` marker row makes a send idempotent per
`(version, kind)` — and it holds **state, counters and timestamps only.** Never
the recipient address, never note text, never the rendered bytes. A durable row
is a *worse* place to leak an address than a cache key: it survives into backups,
dumps and any admin view. A test asserts the marker carries no address- or
note-shaped field.

The button disables itself between click and response, not only after the
outcome — a rapid double-click is a second independent route to two real sends
that an hourly throttle cannot catch. It stays disabled while it still reads
"Sent to your email ✓", because a press on a button in that state is a mis-click
that the already-terminal `202` would answer indistinguishably from a real send.

### Nothing sensitive is logged

No recipient address, no note text, no signed URL, no storage key, no image hash.
Sensitive-path logs carry the operation name, row UUIDs and exception types, per
§15. Request bodies are not captured by Sentry.

## Recorded, accepted exposures

These are **accepted and recorded, not removed.** Anywhere they are restated —
code comments, UI copy, later documentation — they must be described the same
way.

**1. The account address is unverified.** Registration does not verify email, so
a user may register with an address they do not control and have their own renders
delivered there. The throttles bound the volume. Email verification is deferred
to a later phase. This is a deliberate, recorded acceptance.

**2. A private concept image leaves the system.** It reaches the configured SMTP
provider and the recipient's mail host, both of which may retain it indefinitely
and outside Sitara's control. The UI says so in one plain sentence before the
first send — in the workspace itself, not buried in a policy page.

**3. A rare duplicate send is preferred over a silent loss.** Exactly-once
delivery is unachievable across SMTP, a non-transactional external side effect.
If a worker dies between claiming a send and completing it, the claim expires
after `ACCOUNT_EMAIL_SEND_CLAIM_TTL_SECONDS` (900s, constrained at startup to
exceed the task's hard time limit) and the send is retried once — so in that
narrow window the owner may receive two copies of their own render rather than
none. The alternative would silently drop a copy the user explicitly asked for.
This is a deliberate, recorded acceptance, and the duplicate only ever goes to
the account's own address.

## Consequences

**Easier.** A user leaves with something durable. The result screen no longer
needs to expose a bearer URL as a primary action.

**Harder.** There is now a Celery task whose failure is *invisible to the user in
the moment* — they see "queued", and a later delivery failure has no UI. The
delivery marker's terminal states are the operator's record of that; a UI for it
is deferred.

**Removing the Download link is UX, not a privacy control.** The signed URL still
exists, it is still a bearer URL, and a browser can still save a displayed image.
What changed is where the product points. This must never be described as having
closed an exposure — the code comment at `ResultImage.tsx` says so, and so does
this ADR.

**Deferred.** Email verification; a delivery-status UI; any recipient other than
the account's own address; PDF or multi-page export; retrying beyond the single
claim-expiry retry.

**Would trigger revisiting.** Email verification landing would remove exposure 1
and is the single highest-value follow-up. A request to send to anyone else —
a tailor, a family member — is **not** an extension of this decision: it is a
different feature with a different threat model (consent, abuse, rate limiting by
recipient domain) and needs its own ADR. A transactional-email provider with an
idempotency key would let exposure 3 be reduced rather than accepted.

**Outstanding manual checkpoint.** No real SMTP send has been performed. The
capability ships disabled, the attachment ceiling is sized from measured
composition figures rather than a delivered message, and no provider has been
selected. Enabling this in production is an operator decision that also requires
choosing a provider and re-reading its retention terms against exposure 2.

## Alternatives considered

**Keep the download link and add nothing.** Rejected by the phase brief: the
requirement is that a user can *keep* the concept, and a short-TTL signed URL is
not that.

**Accept a recipient in the request body, validated against the account's own
address.** Rejected. It is the open-relay shape with a check in front of it, and
the check is one refactor away from being dropped. Accepting no body at all
cannot be weakened by accident.

**Send the signed URL instead of the bytes.** Rejected. The URL expires, so the
mail becomes useless; and it is a bearer token in an inbox, which is worse than
the attachment it would replace.

**Exactly-once delivery via a two-phase commit with the mail server.** Rejected
as unachievable — SMTP offers no such handshake. The claim-plus-expiry design
makes the failure mode explicit and bounded instead of pretending it does not
exist.

**Attach the annotations as data (JSON, or a separate legend file).** Rejected.
The point is a picture a tailor can look at.

**Render the flattened image in the browser and upload it.** Rejected. It would
require reading the private original into a `<canvas>` to composite (see ADR
0020) and would let a client choose the bytes that get mailed — the server must
compose what it sends.
