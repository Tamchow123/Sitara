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
