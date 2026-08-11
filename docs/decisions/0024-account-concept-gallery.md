# 0024 — The account concept gallery

- **Status:** accepted
- **Date:** 2026-08-11
- **Deciders:** Sitara project owner
- **Phase:** Phase 21 (see ../phases/PHASES.md)
- **Related:** ADR 0004 (private design ownership), ADR 0009 (structured
  design-spec generation), ADR 0012 (private design-image storage — the signed-URL
  rules this works within), ADR 0013 (generation progress and results), ADR 0014
  (rights-safe inspiration metadata influence — the provider-facing prohibition
  this does **not** touch), ADR 0020 (private stylist annotation workspace),
  ADR 0023 (an account is required to generate)

## Context

Before Phase 21 a concept was reachable only from the browser that made it, for as
long as that session lasted. ADR 0023 gives every concept an account to belong to;
this ADR gives the account a screen on which to find them.

The design list endpoint already existed but could not answer the question a
gallery asks. It carried a design's id, title, status and timestamps — nothing
about versions, so a card had no image to show and no way to say a concept was
still generating or had failed.

Two questions had to be answered, and both are about what a *list* may carry that
a *detail* response already carries safely. A list is a different privacy
proposition from a detail view: it multiplies whatever it holds by the number of
rows, and it is fetched on a screen someone lands on rather than one they
deliberately open.

## Decision

**The list carries no signed URL. Each card mints its own.** A gallery of twenty
cards would otherwise produce twenty short-lived bearer URLs in one response,
most of which nobody looks at. Signed design-image URLs are issued only by the
ownership-checked images endpoint (ADR 0012, CLAUDE.md §14) and are temporary
bearer URLs — anyone holding one can use it until it expires. Minting twenty per
page load, on every page load, to satisfy a screen where most cards are scrolled
past, is the wrong trade even though every URL would be legitimately the owner's.

So each card requests its own thumbnail through that same endpoint. This is N
requests for N cards, accepted knowingly: the cost is request count, the
alternative cost is bearer tokens nobody asked for. The URL lives in the card
component's state for as long as the card is mounted, is never written to
`localStorage`, `sessionStorage` or `IndexedDB`, is never logged, and has no
refresh timer — a gallery is a screen someone passes through, and an expired
thumbnail degrades to the same placeholder as any other load failure.

**A version's own `job_status` is carried; the job snapshot is not.** A card must
be able to say "still being made" or "did not finish", and one lifecycle enum per
version says that. The sanitised job snapshot (`latest_job`) remains a design-detail
concern: never the attempt id, the error code, the generation kind or the
timestamps. Read that as a ceiling rather than a precedent — the next job field
does not get in because this one did.

**The concept's name is carried. Its description is not.** This is the deliberate
narrowing in this ADR and it must not be read as more than it is.

`Design.title` is only ever set by someone explicitly naming a design, and the
questionnaire never does — so it is the empty string for every concept made
through the actual product. Building a card on it produced an empty heading and an
alt text of " — version 1". That was found by running the gallery end to end, not
by a unit test, because a jsdom fixture that sets a title hides it.

The name a person recognises their concept by is the DesignSpec's `title`, which
the result screen's own `<h1>` already shows to the same owner over the same
ownership check. So `display_title` is derived server-side — the design's own title
when it has one, otherwise the newest generated version's spec title, otherwise a
plain placeholder — and it is **the only spec-derived text in the payload.**

The boundary is the difference between a **name** and a **description**:

- Admitted: the spec's `title`. Short (3–120 characters in the DesignSpec schema),
  a distinct field from the descriptive ones, already exposed to this same owner
  through the result endpoint, and the thing without which a gallery cannot be
  navigated at all.
- Excluded, and asserted field by field rather than by a blanket rule:
  `concept_summary`, `garment_breakdown`, `colour_story`, `fabrics_and_texture`,
  `embroidery`, `image_alt_text`, the `image_prompt` and its builder version, the
  whole `design_spec` object, and the inspiration-context provenance.

Naming each excluded field individually means a future widening of this payload has
to *delete an assertion* rather than quietly satisfy a general one. A list of
twenty rows is a bad place to publish the generated description of what twenty
people are wearing to their weddings; it is a fine place to print twenty names.

Because it reads stored JSON rather than a validated field, the derivation is total
over arbitrary input: a non-mapping, a non-string name, a nested object, a missing
key, a blank or whitespace-only name, and a legacy version with no spec at all all
fall through to the placeholder rather than raising. It truncates at the same
ceiling a user-supplied title has.

**The gallery renders only once the session is confirmed.** An expired cookie
answers the list with an empty array, and "you have made nothing" is the most
alarming possible lie to tell someone about their own work. A `loading` or
`unavailable` session state shows neither the gallery nor an empty state, and asks
for no designs. A failed list says it failed and offers a retry, rather than
rendering as an empty gallery.

**Paging exists on the endpoint; a "load more" control does not.** The list is the
one design endpoint whose result set grows for as long as someone keeps designing,
so it is bounded (default 20, ceiling 50). An oversized `limit` is clamped, because
asking for more than the ceiling has a correct answer; a nonsense value is refused,
because silence teaches a buggy client that it worked. `offset` is refused above a
ceiling for a sharper reason: Django writes OFFSET into the SQL text with `%d`
interpolation rather than binding it, so an arbitrary-precision Python int becomes
an arbitrary-precision SQL literal, and PostgreSQL raises `DataError: bigint out of
range` past 2⁶³−1 — measured, not assumed. A `DataError` is not an `APIException`,
so DRF would not wrap it and the caller would receive an HTML 500 instead of this
project's JSON envelope. The UI asks for one page and states plainly how many of
the total it is showing when there are more, rather than implying the list is
complete.

## Consequences

- Nothing in this ADR relaxes ADR 0014's provider-facing prohibition. `display_title`
  goes to the **owner's browser**, over an ownership-checked endpoint. It is not
  sent to any AI provider, and the reference-image override (ADR 0019) is
  unrelated to it.
- The list endpoint's contract grew: `display_title`, `is_demo`, `version_count`
  and a `versions` array with paging metadata. `title` is kept beside
  `display_title` rather than replaced, because `title` is what the write endpoint
  accepts and echoes back, and a client that set one is entitled to read exactly
  what it stored.
- Both N+1 queries the nested payload invites — versions per design, and the
  attempt behind each version — are prefetched, and the ordering is applied in
  Python rather than with `order_by`, which would discard the prefetch cache. A
  regression test measures the query count at two data sizes and compares them
  rather than pinning a magic number.
- `OFFSET`/`LIMIT` paging scales with a caller's total design count, and nothing
  caps how many designs one owner may create. That is pre-existing and is recorded
  as technical debt rather than fixed here: a per-owner cap is a product decision,
  and keyset pagination would change the contract the frontend is built against.
- Annotation notes appear nowhere in the payload, and the demo/live label is read
  from the version's stored `is_demo` rather than re-derived from the current
  `DEMO_MODE` setting — which would relabel old concepts whenever an operator
  changed it.

## Alternatives considered

**Include a signed thumbnail URL per row.** Rejected as above: it mints bearer
tokens for cards nobody looks at. One response would carry twenty of them.

**Show a placeholder for every card and no images.** Rejected: a gallery of
identical grey boxes does not help anyone find a concept, which is the whole
requirement.

**Keep `title` and accept empty headings.** Rejected once the e2e showed what it
actually looks like. A gallery whose cards are all nameless is not a gallery.

**Put the whole DesignSpec in the row and let the client choose.** Rejected — it is
the exact disclosure the field-by-field exclusion exists to prevent, and it would
also make the payload large.

**Derive the display name in the browser from the title and version number.** This
was the first plan, taken specifically to avoid touching the spec boundary. It was
abandoned because what it produces — "Untitled concept — version 1" on every card —
is indistinguishable between concepts, so it satisfies the privacy rule by making
the feature useless. Choosing between them is what forced the name/description line
to be drawn explicitly rather than left implicit.

**Add a "load more" control.** Deferred, not rejected. The endpoint supports it;
the UI states its own limit honestly in the meantime.
