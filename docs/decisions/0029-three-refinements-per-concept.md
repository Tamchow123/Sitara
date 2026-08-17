# 0029 — Three refinements per concept, chained

- **Status:** accepted
- **Date:** 2026-08-14
- **Deciders:** Sitara project owner
- **Phase:** Phase 23 follow-up (see ../phases/PHASES.md)
- **Related:** **amends ADR 0015** (single-round constrained refinement) and
  builds on **ADR 0028** (a refinement changes its own selection); ADR 0004
  (private design ownership, whose version numbering this relies on); ADR 0017
  (live-generation cost controls, unchanged); ADR 0027 (the shop owns the
  design and the shared device)

## Context

ADR 0015 gave a design exactly one refinement. That number was chosen when a
refinement's *effect* was unproven, and the reasoning was explicitly about
scope: one bounded change was enough to demonstrate constrained editing without
opening an unbounded conversation with a paid provider.

ADR 0028 then found that a refinement provably could not alter the image prompt
at all for three of eight categories, and fixed it. The first live rounds after
that fix are what changed the number: with a refinement that actually moves the
render, one round is not enough to work a concept into shape, and the person
waiting is standing at a shop counter with the iPad in front of her (ADR 0027).
The project owner's decision is three.

Three is a product judgement, not a derived figure. It is not "unbounded with a
generous cap": the ceiling is still a hard, server-enforced refusal, and the
reasons ADR 0015 gave for having a ceiling at all — bounded provider spend, and
a refinement that stays a constrained edit rather than a chat — are unchanged.

## Decision

### The budget is per DESIGN, counted from the lineage

`MAX_REFINEMENTS = 3`. `refinements_used(design_id)` counts `DesignVersion`
rows **with a parent**, which is what "a refinement happened" durably means.

Deliberately not `version_number - 1`: those agree on every lineage this code
can build, and counting the thing the rule is about is what keeps them agreeing.
Deliberately not an attempt count either — a failed attempt persists no version
and must cost the customer nothing, which is the behaviour a failed refinement
has to have.

`MAX_DESIGN_VERSIONS` stays a separate setting (default `MAX_REFINEMENTS + 1`)
because it backstops a different failure: `MAX_REFINEMENTS` bounds what a
customer may ask for, `MAX_DESIGN_VERSIONS` bounds how many rows any path may
create. Startup now **refuses** a configuration where the version cap leaves no
room for the refinement budget. Without that check the failure is nasty: the
design reports a round remaining, accepts the request, spends on the provider,
and fails at row creation with a version-limit error the customer's screen has
no wording for.

### A lineage is a chain, never a tree

The source of a refinement must be the design's **latest** version. This
replaces ADR 0015's `version_number == 1` rule, which *was* the whole
one-refinement rule.

Refining an older version would give one parent two children, and two children
of one parent have equal claim to being "the" next concept — a question the
version numbering, the result page and the `refined_versions` guard all have no
answer for. So round two refines round one's *output*, and an out-of-date source
is refused with `refinement_source_unavailable` before any provider is selected.

`refined_versions.exists()` is kept, and is not thereby dead code. Sequentially
the latest-version rule always reaches an already-refined source first, because
a version with a child is by construction not the latest. The window it still
covers is the concurrent one: two refinements of the same latest version whose
pre-lock validation both ran before either child existed, serialised by the
advisory lock, the loser re-checking inside it.

### The remaining count is server-owned and published

`GET /designs/<id>/versions/<id>/result/` carries `refinements_remaining`.

The frontend used to derive "already refined" from `lineage.kind` and the latest
job's status. Both inferences were only ever correct because the answer was one.
With three the client would have to count versions it is never sent, so the
server says. A count rather than a boolean, because the UI now has to answer
"how many left?" out loud as well as "may I?".

`isSupersededVersion` is the second half: a succeeded refinement job names the
version it produced, and if that is not the version being viewed then this one
is somewhere back up the chain. That is read from data already on the payload,
not from a version list the result endpoint does not carry.

## Consequences

- A concept can be worked for three rounds, each starting from the previous
  round's output rather than the original.
- The cost ceiling per design rises accordingly: up to three billable
  refinement attempts instead of one, each still subject to every ADR 0017
  control unchanged. **Those ceilings are not relaxed** — the daily count
  limit, the micro-USD budget reservation and the per-session/hashed-IP
  throttles all apply to a third refinement exactly as to a first.
- An older version's result page becomes read-only with a stated reason and a
  link forward to the latest concept, rather than silently omitting the form.
- An operator lowering `MAX_REFINEMENTS` under a live design is legal and
  reports zero remaining, never a negative.
- ADR 0015's other guarantees are untouched: one allowlisted category per
  request, the diff checked field-by-field, a fresh text-to-image generation
  rather than image editing, the original image's bytes never sent anywhere,
  seed reuse documented as a continuity aid only.

### The comparison view stops being a farewell screen

While the budget was one, the comparison view was the last screen of the flow:
two finished renders, side by side, with nothing left to decide. `ResultImage`
took `designId`/`versionId` as OPTIONAL props whose doc comment said the
comparison view omitted them because it "shows two renders read-only", and so
neither card offered Annotate or Send to account.

That description was never accurate — the full-size signed-URL anchor sits above
that gate and each card's brief carries Copy and Download — and chaining makes
it actively wrong. The comparison view is now where a customer stands *between*
rounds, and the concept she is looking at is the one she wants to mark up or send
to herself. Both cards therefore carry both actions, each addressed to its own
version, and the two ids are REQUIRED so the omission is not expressible.

Three consequences follow from putting two of everything on one screen:

- **The accepted-exposure sentence is stated once**, not once per control. ADR
  0021's disclosure is a statement about what sending does; printed twice it
  reads as two different exposures, and printed under one of two identical
  controls it reads as applying to that one alone. A screen with ONE image keeps
  it inside `ResultImage`, in the same branch as the button it describes, so it
  never prints while the image is pending, errored or expired and no send control
  exists. A screen with TWO cannot decide it there: nominating a card would drop
  the sentence entirely whenever THAT card's image failed and its sibling's did
  not, leaving a Send button with nothing said about what sending does. So the
  comparison screen passes `showSendDisclosure={false}` to both cards and renders
  the sentence itself, asking the exported `hasDeliverableImage` of each card the
  same question those branches answer.
- **The two briefs need disjoint DOM ids.** `BriefSection` wires each id into
  `aria-controls` and `aria-labelledby`, so two briefs minting the same ids send
  an assistive technology following either reference to whichever copy comes
  first in the document — the other concept's card. `DesignBrief` therefore takes
  a required `idPrefix`; the single-brief result screen passes `"brief"`, so the
  ids there are exactly what they have always been.
- **Only one send dialog may be open at a time.** `ModalDialog` contains a Tab
  cycle behind a scrim, which a screen reader's browse mode walks straight past;
  opening the second dialog on top of the first leaves the second's focus restore
  pointing at an element inside the first. A small coordinator lends the claim to
  one control at a time and renders the other disabled with its reason, which
  takes it out of the browse-mode action set as well as the tab order. The
  thorough fix — a portal plus `inert` on the rest of the document — is
  deliberately deferred: `inert` is unimplemented in jsdom, so no component test
  could prove it, and portalling out of the RTL container would silently stop the
  existing `axeViolations(container)` assertions from auditing the dialog at all.
  Screens with a single send control render no coordinator and are unchanged,
  because the coordinator's default is permissive rather than absent.

Both action sets also carry a visually-hidden version qualifier. A screen
reader's rotor is a flat list of links and buttons and does not show the
`<article>` that groups them, so two bare "Annotate"s name the same thing twice;
nothing changes for a sighted reader, who has the card heading above them.

The previous card is no longer labelled "Original concept": after round two the
version being compared against is itself a refinement, so "Previous concept" is
the only label true in every round. The same wording on the generation-progress
back-link and in the refinement panel's caveat is corrected separately.

**A sizing note for whoever first enables email delivery.** Three chained rounds
means a design can reach four versions, each with a plain and an annotated render,
each sendable `ACCOUNT_EMAIL_MAX_SENDS_PER_RENDER` (3) times — 24 possible messages
for one concept, against `ACCOUNT_EMAIL_SEND_LIMIT_PER_HOUR` 10 and `_PER_DAY` 30.
Nothing here relaxes any of those, and none of them is relaxed elsewhere either:
this is recorded because the hourly ceiling is now reachable by ordinary use rather
than only by abuse, and that is a capacity decision for the operator to take
deliberately when `ACCOUNT_EMAIL_DELIVERY_ENABLED` is first turned on, rather than
discover from a customer being refused.

None of this touches what is sent anywhere. `ACCOUNT_EMAIL_DELIVERY_ENABLED`
remains default-false, the recipient remains `request.user.email` read
server-side with no client-supplied address accepted in any field, the send body
remains the single filename, and the per-render lifetime cap is unchanged and
still counted per `(design_version, kind)` — so a second card's control spends
that version's own allowance and not the one beside it. Removing the download
link remains a UX decision and not a privacy control.

### Known debt: a half-finished refinement still strands the design

A refinement whose text stage succeeded and whose **image** stage then failed
leaves a child version with no permanent image. The parent is then refused
because a newer version exists; the child is refused because its image is
incomplete. Nothing on the design is refinable.

This is **pre-existing, not introduced here** — under the one-refinement rule
the parent was refused by `refined_versions.exists()` and the child by
`version_number != 1`, so the design was equally stuck. What this decision
changes is the *reporting*: `refinements_remaining` now says rounds are left
while none of them can be used.

It is recorded rather than repaired because every cheap repair is unsafe. An
imageless child cannot be told apart from one whose image stage is *running
right now*, so ignoring it in the branching guard would let a concurrent second
refinement of the parent branch the lineage — precisely what `refined_versions`
exists to prevent. Recovering a half-finished refinement needs the attempt's own
state, and that is its own slice. The behaviour is pinned by
`test_a_child_whose_image_stage_failed_strands_the_design_KNOWN_DEBT` so it
cannot drift unnoticed and so the eventual fix has a test to invert.
