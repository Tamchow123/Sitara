# 0026 — In-store reference capture by phone handoff

- **Status:** accepted
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
holds the shop's screen can consent on the customer's behalf.

Get this wrong and the product records a rights affirmation made by someone who
never saw the photograph. That is worse than no affirmation, because it looks
like one.

### The iPad polls

A plain two-second interval while the panel is open. CLAUDE.md §17 confines
TanStack Query to the generation-progress flow and this phase decided not to
widen it for a panel open for a minute or two. No WebSockets, no SSE, no push.

## Consequences

- Nothing about what happens to the image afterwards changes: same endpoint
  semantics, same sanitisation to one clean WebP with EXIF and GPS stripped, same
  private storage, same three-slot cap, same deletion with the design.
- The set of people who can put an image into a design widens from "whoever holds
  the shop's session" to "whoever holds the shop's session, plus whoever holds a
  live code". That is the point, and it is what the bounds above are for.
- A customer's phone becomes a client of Sitara's API. It has no account, no
  workspace and no session relationship to the iPad — the grant is its entire
  authorisation, and it is CSRF-protected like every other unsafe endpoint.
- The manual checkpoint is outstanding: a real iPad, a real phone that is not the
  iPad, all three paths, and a photographed code confirmed dead after expiry.
  Until that is run the honest claim is "implemented and exercised against the
  test suite", nothing stronger.

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
