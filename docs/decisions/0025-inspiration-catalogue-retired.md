# 0025 — The inspiration catalogue is retired from the product

- **Status:** accepted
- **Date:** 2026-08-11
- **Deciders:** Sitara project owner
- **Phase:** Phase 22 (see ../phases/PHASES.md)
- **Related:** ADR 0006 (rights-controlled inspiration catalogue — this retires
  its **product surface**, not its machinery), ADR 0014 (rights-safe inspiration
  metadata influence — its persisted snapshots are untouched), ADR 0018 and
  ADR 0019 (the user's own reference uploads, which become the only reference
  path), ADR 0026 (in-store reference capture)

## Context

ADR 0006 built a staff-managed catalogue of rights-cleared inspiration images: an
ingest pipeline that sanitises bytes, a rights record with a verifier and an
expiry, an approval workflow, a `publicly_eligible()` queryset that re-checks
every condition on every request, and three public endpoints serving the
approved set and its image variants.

It was built carefully and it works. Two facts decided its retirement.

**No asset was ever approved.** The catalogue has never had a single publicly
eligible row, in any environment. The manual checkpoint that would have put three
genuine rights-cleared images through it (CLAUDE.md §13) was never run. So the
three public endpoints have, for their entire life, returned an empty list to
every caller. Nothing is being taken away from anyone, because nothing was ever
there to take.

**The customer's own photograph is the real input.** The project owner's
observation from the shop floor, recorded in the Phase 22 brief: *"most customers
will have their inspiration pictures on their own devices"*. A bride arrives with
a screenshot from Instagram or a photograph of her sister's wedding. She does not
arrive wanting to browse a curated grid, and a grid of somebody else's approved
looks is not what she is trying to describe.

A public endpoint with no caller is a surface with no owner. It still has to be
reasoned about in every security review, still appears in the contract, still has
to be kept correct — and it is the kind of surface that quietly acquires a
consumer later precisely because nobody remembers why it was there.

## Decision

**The catalogue is retired from the product. Its machinery stays.**

Removed:

- `GET /api/v1/inspiration-assets/` and the two image-variant endpoints.
- `sitara/catalogue/views.py`, `urls.py`, `serializers.py`, `openapi.py` and the
  API test module.
- The catalogue picker in the questionnaire's reference step, and
  `inspiration_asset_ids` from the design write serializer — a request naming it
  now gets the ordinary unknown-field 400 rather than a silent ignore.

Kept, intact and staff-only:

- The `catalogue` app: models, migrations, the rights record, ingest, the
  sanitiser, `publicly_eligible()`, the services and the Django admin. Reaching
  any of it now requires an admin login.
- Every `DesignInspiration` selection row already in the database.
- Every `DesignVersion`'s frozen `inspiration_context` snapshot, hash and schema
  version, and the acknowledgement the result screen renders from it.

No migration in this phase touches any of those tables.

### Removing the stronger rights model does not upgrade the weaker one

This is the part most at risk of quiet drift, so it is stated as a decision.

The catalogue's rights position was the strong one: staff-verified, evidenced,
unexpired, and re-checked against `publicly_eligible()` on every single request.
A user's own uploaded reference rests on something much weaker — one
self-affirmation, per upload, by whoever is holding the device.

With the catalogue gone, the weak model is now the *only* model. It is tempting,
and wrong, to describe what remains in the language the stronger one earned.
The upload affirmation is not verification, is not clearance, and must never be
called either in code, copy, documentation or an API description. CLAUDE.md §13's
two-bar distinction survives the removal of one of the bars.

### Historical selections render, but cannot be made

A design generated before this phase may carry selections and a frozen
`inspiration_context`. Those are immutable audit data (ADR 0014) and the result
screen still renders their acknowledgement from the stored snapshot, byte for
byte. What changed is only that no design can gain a selection any more.

The design detail payload therefore reports a historical selection as
`{id, position, available: false}` with no asset object. Documenting an asset
payload whose URLs no longer resolve would be worse than documenting none.

### `reference_images.py` keeps its eligibility re-check

`generation/reference_images.py` still re-validates a curated selection against
`publicly_eligible()` when minting a reference URL, and that branch is
deliberately **not** deleted on the grounds that the selection path is gone. A
design generated today may still carry a selection made before this phase, and
the re-check is what stops a since-expired or since-revoked asset reaching a
provider. Removing a guard because its input is now rare is how the rare case
becomes the incident.

## Consequences

- The reference step is one thing: the customer's own photographs. No grid, no
  shared budget to arithmetic against, no catalogue request to fail. The three
  reference slots are the uploads' alone.
- Sitara's provider-facing rights posture is now entirely the ADR 0019 override:
  the bytes of a user's chosen reference are sent to the image provider under
  terms that take a perpetual, irrevocable licence over inputs. That exposure is
  **accepted and disclosed, not removed** — and with the catalogue gone there is
  no longer a stronger path to point at beside it.
- Bringing the catalogue back is a phase, not a revert. The models are here, but
  the endpoints, the serializers, the picker and the write field are not, and the
  rights checkpoint that was never run would still need running first.
- One thing genuinely got smaller: the set of things that can reach a provider.
  There is now exactly one way an image gets to the image model, and it is an
  image the person in front of the screen chose and affirmed.

### What would trigger revisiting this

A licensing arrangement that makes a curated library genuinely available —
approved assets with evidenced, unexpired rights — plus a product reason to
browse rather than to describe. Both, not either. The machinery kept here is
what makes that a phase rather than a rebuild.

## Alternatives considered

**Delete the catalogue app entirely.** Rejected. The rights machinery is the
carefully-built part and the hardest to get right a second time; the ingest
sanitiser in particular is depended on by the user-upload path. Deleting the
models would also mean a migration against `DesignInspiration` rows and the
frozen snapshots that reference them, which ADR 0014 forbids.

**Leave the endpoints in place, serving an empty list.** Rejected. That is the
surface with no owner: it costs review attention on every security pass and it is
exactly what a future contributor wires a new consumer into without knowing the
rights checkpoint was never run.

**Keep the picker, hidden behind a flag.** Rejected as the worst of both. A
hidden picker is still a code path, still a contract entry, and still something a
flag flip could expose without the checkpoint — and this decision is not "not
yet", it is "the customer's own photograph is the input".
