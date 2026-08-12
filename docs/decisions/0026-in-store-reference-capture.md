# 0026 — In-store reference capture by phone handoff

- **Status:** accepted, amended 2026-08-12 (see [Amendment](#amendment-2026-08-12--the-phone-is-the-only-way-in) — the iPad's camera and file picker are removed and the code shows itself)
- **Date:** 2026-08-11
- **Deciders:** Sitara project owner
- **Phase:** Phase 22 (see ../phases/PHASES.md)
- **Related:** ADR 0018 (private user reference uploads), ADR 0019 (reference-image
  conditioning — its disclosure and its accepted exposure), ADR 0025 (the
  catalogue is retired, making uploads the only reference path), ADR 0027 (the
  shop owns the design, and the shared device), ADR 0014 §on temporary bearer
  URLs, which this reuses as a category

## Context

With the catalogue retired (ADR 0025) the reference step is the customer's own
photographs and nothing else. The problem is getting them there.

> **Owner decision (2026-08-11):** "most customers will have their inspiration
> pictures on their own devices so will need an intuitive way of getting it from
> their device to the app."

Sitara runs on a shop-floor iPad. The picture is on a phone in someone else's
hand. Before this phase the only ways across were a file picker on the shop's
device — which means the customer hands over an unlocked phone, or emails the
picture to the shop, or gives up. That is where the reference step was failing
for the normal case, not the edge case.

So the phone handoff is **the primary path**, not a fallback, and it is judged
against that: no typing, no app, no sign-in, one tap from landing to the phone's
own photo library, and it has to work on the several-year-old browser that
actually walks into a shop.

## Decision

The iPad shows a QR code. The customer scans it with her own phone, which opens a
small page scoped to one design, shows the ADR 0019 disclosure, takes her
affirmation, and lets her send photographs. The iPad's list updates as they
arrive.

Behind it is a `ReferenceUploadGrant`: a short-lived, revocable, upload-only
credential naming exactly one design.

### The QR is a bearer credential, and that exposure is accepted and bounded, not removed

Anyone who can see the iPad's screen can photograph the QR and upload to that
design. This is the same category CLAUDE.md §14 already names for signed
design-image URLs, and it is stated here in the same words it is required to be
stated in for ADR 0019's rights override, ADR 0021's three exposures and ADR
0022's fourth: **accepted and bounded, not removed.** Nothing below eliminates
it, and describing the bounds as eliminating it is prohibited.

The bounds:

| Bound | What it is |
| --- | --- |
| Scope | Upload, into one named design. No read path exists behind a grant and none will be added — this is a permanent non-goal, asserted structurally by a test that the resolver has exactly one caller. |
| Lifetime | `REFERENCE_UPLOAD_GRANT_TTL_SECONDS`, 15 minutes. A code photographed off a screen goes stale before it leaves the shop. |
| Uses | Bounded by the design's remaining reference slots. A full design spends the code. |
| Liveness | At most one live grant per design, enforced by a partial unique constraint and a Design row lock. Minting again revokes the old one, so re-showing the panel cannot leave an earlier photographed code working. |
| Revocation | Explicit ("Stop accepting photos"), and automatic on leaving the reference step, on the design filling up, and on the walk-in session ending (ADR 0027). Unlike a signed storage URL this one genuinely stops, because it resolves through Sitara. |
| Rate limits | Per address and per code, both fail-closed on a cache outage, reusing `accounts/rate_limits.py` rather than a second limiter. |

The secret is `secrets.token_urlsafe(32)`, stored only as a SHA-256 digest, and
returned exactly once — to the owner who minted it, so their screen can draw a
QR. It is never logged, never in Sentry, never in a response body again, and
never in a URL path or query string: it travels in the request **body**, so it
cannot reach a web-server access log, a `Referer` header or a browser history
entry, and it reaches the phone in the URL **fragment**, which browsers do not
send to servers.

That last point had a hole, found in review and closed here: Sentry's browser
SDK attaches the page URL to every event, and the app's scrubber only stripped a
query string. `/r#<token>` has no query string. The scrubber now cuts at the
first `?` or `#`, breadcrumbs included.

### Every unusable code gets one answer

Expired, revoked, spent and never-existed are one 404 with one code and one body.
That is CLAUDE.md §15's private-resource enumeration rule applied to a new
identifier: a caller must not learn that a design exists but its code lapsed.

Two consequences of taking that seriously. "Spent" is decided by the design's
slots filling up under the row lock in `upload_service`, not by a second check in
the grant resolver, so there is one locked answer to "is there room" rather than
two that can disagree. And both rate-limit windows run **before** the code is
resolved, so a real code and an unknown one start answering 429 at the same
request count rather than one of them betraying itself.

A rejection about the caller's own file — too large, not an image, already
added — is answered honestly instead. It reveals nothing about the design, and
the person holding the phone can act on it.

The success body is a constant. It carried a remaining-slots count until review
pointed out that the cap is on the design and `MAX_INSPIRATION_IMAGES` is public,
so a phone could subtract its own uploads and recover how many references were
already there. In the ordinary shop flow — stylist adds one on the iPad, then
mints a code — that leaked on the very first upload. The phone now learns
capacity only by trying.

### The affirmation is made by the person whose photograph it is

This is the part that matters most and is easiest to get wrong.

ADR 0019's disclosure exists because sending a reference to the provider is
irreversible and the provider's terms take a perpetual, irrevocable licence over
inputs. Before this phase that disclosure sat in front of whoever was holding the
device — on a shop-floor iPad, very often the stylist.

So: the phone shows the **full** disclosure, in the same words as the iPad, before
its picker is usable; the affirmation is taken **on the phone**, per upload; and
an affirmation ticked on the iPad does not carry across. The disclosure is one
shared component for exactly this reason — two copies would drift, and the one
that got shortened would be the phone's, because it is the one that does not fit.

The QR control is deliberately **outside** the iPad's own affirmation checkbox.
Gating it behind that tick would say the opposite of this decision: that whoever
holds the shop's screen can consent on the customer's behalf. (The amendment
below removes the iPad's checkbox altogether, along with the two controls it
gated. The principle is unchanged and the phone's affirmation is untouched —
there is simply no longer a second, weaker place to take one.)

Get this wrong and the product records a rights affirmation made by someone who
never saw the photograph. That is worse than no affirmation, because it looks
like one.

### The iPad polls

A plain two-second interval while the panel is open. CLAUDE.md §17 confines
TanStack Query to the generation-progress flow and this phase decided not to
widen it for a panel open for a minute or two. No WebSockets, no SSE, no push.

It polls a **narrow read** — `GET /designs/<id>/references/`, which returns the
design's own uploaded references and nothing else — rather than the full design
detail. The question being asked every two seconds is only "has a photograph
arrived yet?", and answering it with the whole draft meant re-sending the
versioned questionnaire schema, the customer's saved answers and the latest job
snapshot on every poll, over the same shop wifi the customer's phone is using to
push a multi-megabyte photograph. The narrow read is owner-only through the
ordinary ownership filter and carries no image bytes and no signed URL; a
handoff grant does not open it, because a grant is upload-only, permanently.

## Consequences

- Nothing about what happens to the image afterwards changes: same endpoint
  semantics, same sanitisation to one clean WebP with EXIF and GPS stripped, same
  private storage, same three-slot cap, same deletion with the design.
- The set of people who can put an image into a design widens from "whoever holds
  the shop's session" to "whoever holds the shop's session, plus whoever holds a
  live code". That is the point, and it is what the bounds above are for. (After
  the amendment below it is no longer a widening but a **replacement**: only a
  live code puts an image in. Removing one stays the session's.)
- A customer's phone becomes a client of Sitara's API. It has no account, no
  workspace and no session relationship to the iPad — the grant is its entire
  authorisation, and it is CSRF-protected like every other unsafe endpoint.
- The manual checkpoint is outstanding: a real iPad, a real phone that is not the
  iPad, the handoff end to end (one path, after the amendment below), and a
  photographed code confirmed dead after expiry. Until that is run the honest
  claim is "implemented and exercised against the test suite", nothing stronger.

### What would trigger revisiting this

A read capability being wanted behind a grant — for instance, letting the
customer see and remove what she sent. That is not a widening of this decision,
it is a different one, and it would need its own review of what a bearer
credential is then allowed to see.

## Alternatives considered

**A short typed code instead of a QR.** Rejected as the primary path: typing is
the friction this exists to remove. A short code is also a second credential
space with its own entropy and rate-limit questions. The full URL is shown as
selectable text for a camera that will not scan, which is the fallback the phase
asks for without inventing a second credential.

**Emailing or texting the customer a link.** Rejected. It needs an address or a
number from the customer — more friction than scanning, and a new piece of
personal data to hold — and `ACCOUNT_EMAIL_DELIVERY_ENABLED` has never once been
exercised against a real SMTP server.

**A session-scoped pairing rather than a per-design grant.** Rejected. A grant
that named a session rather than a design would follow the iPad to the next
customer, which is precisely the failure ADR 0027 is about.

**Rendering the QR server-side.** Rejected. It would need a Python dependency and
put a presentation concern in the API, and it would not achieve "encoded exactly
once" anyway, because the typed fallback means the plaintext reaches the browser
regardless.

## Amendment (2026-08-12) — the phone is the only way in

> **Owner decision (2026-08-12):** "I've decided I want the default to be the QR
> code so only provide that option. […] reduce the text on the inspiration page
> to only show what is necessary and have the QR code shown immediately."

The original decision made the handoff *primary* and kept the iPad's camera
capture and file picker behind it. In use that ordering was the whole answer:
the picture is on the customer's phone, so the two device-local controls were
alternatives nobody on a shop floor wanted. They are removed.

Three changes, all in the browser. No endpoint, model, migration, gate or bound
changes.

1. **One way in.** The iPad's camera capture and file picker are gone. The
   `POST /designs/<id>/inspiration-uploads/` endpoint and its client wrapper stay
   — it is the owning session's own upload path, still tested, and removing a
   working authorised endpoint to reflect a UI choice would be a different and
   larger decision. **Standing note for whoever reaches for it next:** it now has
   no UI caller, so nothing on screen demonstrates the affirmation rule any more.
   Any future caller of `uploadInspirationImage`, or of `RightsDisclosure`'s
   now-unused `scope="own-device"`, must re-derive that rule from this ADR before
   wiring it up — the affirmation belongs to the person who chose the photograph,
   and a `rights_acknowledged` flag set by anything other than that person's own
   tick is the exact substitution this decision exists to prevent.
2. **The step says only what it needs to.** The reference screen is now a
   heading, a one-line budget, and the panel.
3. **The code shows itself** when the step opens, rather than after "Show the
   code" — a button whose answer, on a step whose only way in *is* the code, was
   never going to be no.

### What this does not change, and must not be read as changing

**The affirmation.** The iPad's rights checkbox and its copy of the ADR 0019
disclosure went with the two controls they gated, because that is all they gated.
The affirmation this ADR is built on is the **phone's** — full disclosure in the
same shared component and the same words, taken per upload, from the person who
actually chose the photograph, and enforced server-side. It is unchanged in
wording, placement and enforcement. Removing the weaker, wrongly-placed one does
not upgrade the remaining one: a per-upload self-affirmation is still **not**
verified, cleared or approved rights (CLAUDE.md §13).

**The bearer exposure.** Still **accepted and bounded, not removed.** Every bound
in the table above holds unchanged, and no read capability exists or may be added
behind a grant.

### What it costs

A grant is now minted on **every visit** to the reference step rather than on
demand. That is a real cost and it is bounded by the bounds that already existed:
the 15-minute TTL, at most one live grant per design, revocation on leaving the
step, and the mint throttles (`REFERENCE_UPLOAD_GRANT_MINT_LIMIT`, 30 per design
per hour; `…_MINT_IP_LIMIT`, 90 per IP per hour). The single-live-grant rule is
what makes it safe rather than merely frequent: a stylist who steps back and
forward over the step invalidates the code a customer may be mid-scan on, and the
customer sees a code that no longer works rather than two codes that both do.

**A stop must stay stopped.** Showing the code automatically must not undo an
explicit "Stop accepting photos" — that would make this ADR's revocability claim
false at the one moment it is relied on. Two things enforce it: the auto-show is
one-shot per mount, and a stop is remembered for the tab's life, so stepping away
from the step and back does not hand the code back either. Only pressing "Show a
new code" asks for one again. Pinned by tests proven to fail without each guard.

**Two mints must not race.** Because each visit mints, a fast back-and-forward
could put two mints in flight at once — and the server resolves them in whichever
order they reach the design's row lock, each revoking the other's grant on the
way. The response arriving last in the browser could then describe a grant the
server had already killed, leaving a QR on screen with a ticking countdown that
no phone can use and nothing to say so. A mint is therefore shared: a second
mount joins the one already in flight instead of starting its own. For the same
reason a departing panel revokes only a grant it still holds — a revoke is
design-scoped, so an unconditional one could land after a newer mint and kill the
code that replaced it. Both live in `handoff-coordination.ts`, memory only, and
neither is a token cache: leaving the step still revokes.

**A customer with no usable phone now has no way to attach a reference.** There
is no device-local fallback left. References are optional and the design flow
completes without them, so this costs a customer inspiration rather than a
concept — but it is a genuine narrowing, accepted as part of this decision rather
than mitigated.
