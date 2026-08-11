# 0022 — Caller-named render attachments, capped and remembered

- **Status:** accepted
- **Date:** 2026-08-11
- **Deciders:** Sitara project owner
- **Phase:** Phase 21 (see ../phases/PHASES.md)
- **Related:** ADR 0021 (account render delivery by email — this **amends** it),
  ADR 0020 (private stylist annotation workspace), ADR 0017 (the fail-closed gate
  pattern), ADR 0023 (an account is required to generate), ADR 0024 (the account
  concept gallery)

## Context

ADR 0021 shipped *Send to account*: the owner asks for a copy of their concept —
plain, or flattened with their annotations — and it arrives at their own account
address. The attachment filename was server-generated and opaque.

Two things were wrong with that in use. A stylist working on several concepts
receives several near-identical messages and cannot tell which attachment is
which without opening each one. And an owner who presses the button repeatedly
has no stated limit, so nothing bounds how many copies of one render the system
will produce over its lifetime — ADR 0021's rate limits bound the *rate*, not the
*total*.

So Phase 21 lets the caller name the file, remembers the name they chose, and caps
each artefact at three sends for its whole life.

Naming the file is a smaller change than it sounds, and a larger one than it
looks. Smaller, because the name is a string the owner types about their own
design. Larger, because it is the first caller-controlled value this system puts
into an email header.

## Decision

**The caller names the attachment, within validation, and the name is
remembered.** The send endpoints now accept a request body carrying exactly one
field, the filename. The name is validated, applied to the attachment, and stored
on the delivery marker row so the next send of the same artefact pre-fills it.

**Each artefact may be sent at most `ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER` (3)
times, ever.** `send_count` is a durable column on the delivery marker row,
incremented inside the same reservation ADR 0021 already used to make a send
at-most-once. It is a lifetime total, not a window: it never resets, it is checked
against the database column and **never against a rate limiter or a cache** — a
cache eviction must not hand back three more sends — and it is deliberately
separate from ADR 0021's per-hour/per-day/per-IP rate limits, which continue to
apply on top of it. Each artefact counts separately: the plain render of version
1, the annotated render of version 1, and the two renders of a refinement are four
different artefacts with four independent allowances.

**It counts messages the backend accepted, not attempts.** A failed send does not
consume an allowance, because charging someone for the system's own failure is not
a limit, it is a punishment. Repeated failure cannot be used to grind, because the
existing rate limits still apply to every attempt.

**A second deliberate press is now distinguishable from a redelivery**, which it
was not before. Until Phase 21 `sent` was terminal for the row's lifetime, so both
cases looked identical and both no-opped. An `attempt_epoch` counter is
incremented once per deliberate press and a task acts only on the epoch it was
queued for: a redelivered task carries a superseded epoch and no-ops, a new press
carries a fresh one. Without that the row would have to choose between refusing
legitimate second sends and mailing duplicates. **Exactly-once is still not
claimed** — ADR 0021's reasoning about SMTP is unchanged.

**The recipient is unchanged and is not negotiable.** It is still
`request.user.email`, read server-side. The endpoints accept a filename and
nothing else; there is no address field, in any form, and the frontend client has
no address parameter. An anonymous owner still gets `409
email_recipient_unavailable` with no fallback.

**The default name is never derived from a note.** A note is the most personal
free text in the product (CLAUDE.md §7). The pre-filled name comes from the
concept and its version number, never from what someone wrote about disliking a
neckline.

## What this gives up

ADR 0021 recorded three accepted exposures. This ADR **adds a fourth, and it
must not be described as mitigated or removed.**

**"Headers carry nothing private" is given up, not mitigated.** ADR 0021 could
say that the only private thing in the message was the attachment body. That is
no longer true: the filename is a string the owner wrote about their own design,
it appears in the `Content-Disposition` header of the outbound message, and mail
headers are the part of a message most likely to be logged by an intermediary,
retained in a bounce report, or displayed in a notification preview. A stylist
who names a file after a client has put that name into mail infrastructure
nobody here controls.

This is accepted because the alternative is worse for the person it protects: an
owner who cannot tell their own attachments apart is being protected from a
disclosure they chose, at the cost of the feature working at all. The upload UI's
precedent applies — the exposure is stated to the user before they can incur it.
The naming dialog says plainly that the name travels in the email.

The **injection surface** that a caller-controlled header value opens *is* closed,
and that distinction matters: giving up "no private data in headers" is a privacy
trade the owner makes knowingly; allowing a newline into a header would be a
defect. Specifically, in `media/account_delivery.py`:

- C0, DEL and C1 control characters are **refused, not stripped**. Stripping
  `a\r\nBcc: x` would silently produce the plausible-looking `aBcc: x`; refusing
  means the send falls back to a name nobody chose, which is the honest outcome.
  This matters because Python's own header quoting escapes `"` and `\` but *not*
  control characters, so nothing downstream would have caught a CRLF let through
  here.
- Lone surrogates are refused for a different reason: a JSON body may carry an
  unpaired `\ud800`, Python holds it in a `str` happily, and nothing objects
  until the UTF-8 encode inside the RFC 2231 header path raises — turning a bad
  *name* into a failed *send*. Totality has to mean total for the caller's
  outcome, not merely free of exceptions in one function.
- Path separators are removed, the name is NFC-normalised first so two spellings
  of one name cannot behave differently, length is bounded, and the extension is
  fixed server-side rather than taken from the caller.
- **Only bidi controls are stripped, not the whole `Cf` category.** U+200C and
  U+200D (ZWNJ/ZWJ) are `Cf` and are load-bearing in Devanagari, Urdu and
  Bengali; removing them would corrupt exactly the names this feature exists to
  allow, which is the flattening CLAUDE.md §2 forbids. The overrides and isolates
  (U+202A–U+202E, U+2066–U+2069) are stripped, because they can make a filename
  read as something it is not.

Django's own RFC 2231 encoding of non-ASCII filenames is relied on rather than
reimplemented, so a Bengali or Hindi title survives instead of being reduced to
ASCII.

**The recipient rule's protection changes in kind, and that is the second thing given
up.** ADR 0021 did not merely assert that no address is accepted — it made one
*impossible to express*, because there was no request body to put it in, and it
argued explicitly that this "cannot be weakened by accident" while a validator is
"one refactor away from being dropped". A body now exists. The rule is therefore
enforced by `RenderSendSerializer`, which permits `filename` and nothing else and
rejects `email`, `to`, `cc`, `bcc`, `from_email` and `reply_to` **by name** rather
than merely by omitting them, so a smuggled recipient is a controlled 400 rather
than a silently ignored key.

That is a real downgrade from impossibility-by-construction to
validation-by-convention, and ADR 0021's own argument now applies to this design.
It is stated here, in the list of accepted losses, rather than only in ADR 0021's
amendment note — someone reading only the ADR that made the change should see both
costs, not just the header-privacy one. The prohibition itself is unchanged and
absolute: **no client-supplied recipient, in any field, ever.** Anyone loosening
that serializer is reintroducing the open-relay shape ADR 0021 rejected, and should
read its Alternatives section before doing so.

**The stored name is persisted, and is therefore protected.** It lives on the
delivery marker row so a repeat send can pre-fill it. That row now holds one piece
of user-authored text where before it held only state, counters and timestamps —
which is a real change to what a database backup and an admin view contain, and is
why CLAUDE.md §14 is amended rather than left to imply otherwise. It is never
logged, never sent to Sentry, never returned to any caller but its owner, and
never becomes AI input. It is still true, and still important, that the row holds
**no recipient address**, no note text and no rendered bytes.

## Consequences

- The send endpoints are no longer body-less. CLAUDE.md §7 and §14 said they
  accept no request body at all; that claim is narrowed to what it was protecting
  — no client-supplied recipient — rather than deleted.
- A refused name is answered in the dialog with the text the person typed still
  in the field. A validation failure that clears the input punishes someone for a
  rule they have not been shown yet.
- The remaining allowance is stated before it is spent, not after. "You have two
  sends left" before pressing is respectful; discovering the limit by hitting it
  is not.
- The cap is reachable only where the send is reachable. With
  `ACCOUNT_EMAIL_DELIVERY_ENABLED=false` the delivery gate is checked before the
  reservation, so the counter never moves and the ceiling cannot be exercised
  from a browser at all. It is proven at the API level instead, and
  `apps/web/e2e/README.md` records that limitation rather than leaving a reader
  to assume the e2e covers it.
- **No SMTP send has ever been performed.** This ADR changes what a message would
  contain; it does not change the fact that none has been sent. Tests and CI open
  zero SMTP connections and assert the locmem backend. Do not describe email
  delivery as exercised.

## Alternatives considered

**Keep the server-generated name.** Rejected: it is the status quo whose only
defect is the one this change exists to fix. It protects the owner from a
disclosure the owner wants to make.

**Derive the name from the DesignSpec title automatically, with no dialog.**
Rejected as worse on both counts at once: it puts spec-derived text into a mail
header *without* the owner choosing it, and it still gives every send of the same
concept the same name.

**Let the caller supply the full filename including extension.** Rejected. The
extension determines how a mail client treats the attachment; taking it from the
caller invites a mismatch between the declared type and the bytes.

**Make the cap a rolling window.** Rejected. A lifetime total is what bounds the
system's total output for one artefact, which is the thing being bounded. A window
that resets is another rate limit, and ADR 0021 already has three.

**Store the chosen name in the browser instead of the database.** Rejected: it
would not survive a new device, which is most of the point of remembering it, and
it would put user-authored text into browser storage that ADR 0020 keeps clear.
