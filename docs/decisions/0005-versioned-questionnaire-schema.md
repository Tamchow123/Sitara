# ADR 0005 — Versioned questionnaire schema (Phase 5A)

- **Status:** Accepted (2026-07-17, after the Phase 5A test suite passed)
- **Amended by ADR 0018 (Phase 16B, 2026-07-23):** questionnaire **v3** added as a
  new draft (v1 active/fingerprint-locked and v2 untouched); the strict option
  shape gains optional bounded `visual_key`/`group` machine-id metadata.
- **Amended below (design-handoff phase, 2026-07-28):** questionnaire **v4**
  added as a new draft; the question vocabulary gains `colour_choice` and
  `colour_list`, revising the "three question types — no others" boundary
  recorded in this document. See
  [Amendment — questionnaire v4 colour question types](#amendment--questionnaire-v4-colour-question-types-design-handoff-phase).
- **Deciders:** Sitara project
- **Related:** ADR 0002 (application foundation), ADR 0004 (private design
  ownership)

## Context

The questionnaire is the source of every design brief, and its rules must
drive two validators without duplication: Django's authoritative
server-side answer validation (a later phase) and the frontend's Zod
validation, which Phase 7 will *derive* from the same machine-readable
constraints. That demands one versioned, backend-owned schema — not
question definitions scattered through Python and TypeScript.

## Decision

### The backend questionnaire schema is authoritative

`QuestionnaireVersion` (UUID primary key, globally unique positive
`version`, `status` in draft/active/retired, required JSON `schema`,
nullable `created_by`/`activated_by` staff references with `SET_NULL`)
holds the complete definition. `GET /api/v1/questionnaire/active/` serves
exactly `{id, version, schema}` — no staff fields, no lifecycle
timestamps — with `Cache-Control: no-store`, and answers a safe
`503 questionnaire_unavailable` when no valid active version exists.
The stored schema is re-validated before every serve; a corrupted active
schema yields the same 503, logging only the version id and exception
type. The endpoint is identity-free (no authentication classes), so a GET
never creates a Django session or a DesignSession. Frontend validation is
**derived** from this schema in Phase 7; questionnaire rules are never
hand-duplicated in Zod.

### A deliberately small, declarative schema format

Supported question types: `single_choice`, `multi_choice`, `text` — no
others. Constraints are bounded and typed: `min_items`/`max_items`/
`exclusive_values` for multi-choice, `min_length`/`max_length` (mandatory
cap) for text; a single choice is constrained by its declared options.
Compatibility rules are allowlisted data, not code:
`when {question_id, operator: equals|in|not_in, values}` →
`then {action: show|hide|require|restrict_options, question_id, values?}`.
The pure-Python validator (`schema_validation.py`) rejects unknown keys at
every level, verifies every referenced question and option exists, and
enforces global size/count ceilings. There is **no generic expression or
rules engine**: no eval, no executable expressions, no imports from schema
data, no arbitrary JSON Schema extensions. Sitara needs perhaps a dozen
compatibility rules; an expression engine would turn admin-editable data
into an injection and complexity surface for zero product benefit. If a
rule shape is ever genuinely needed, it is added to the allowlist with
tests — a deliberate, reviewed schema-format change.

### Stable machine identifiers

Step, question, rule and option identifiers match
`^[a-z][a-z0-9_]{1,63}$` and are unique in their scope (question ids
globally, option values per question). Persisted answers in later phases
reference these ids forever, which is why…

### …published versions are immutable; one version is active

Once a version is active or retired, its `version` number and `schema`
refuse to change through normal model or admin operations; active versions
cannot be deleted in admin; retired versions remain inspectable. Changes
ship by creating a new draft and activating it through
`activate_questionnaire_version` — a transaction that locks the target
row, validates the complete schema (malformed data is never silently
activated), retires the current active version, and stamps
`activated_at`/`activated_by`. Ordinary saves never activate anything and
the admin form cannot set status. The database has the final word: named
constraints enforce `version > 0`, a valid status, and — via a PostgreSQL
partial unique constraint (`questionnaire_single_active`) — **at most one
active row**, even against competing or bypassed activation attempts.

### Cultural taxonomy: carefully bounded, human-reviewed

The seeded v1 fixture covers garment/ceremony, optional regional styling
direction, silhouette, colour palette, fabrics, embellishment, modesty and
coverage preferences, dupatta/saree draping, and capped final notes. It
keeps gharara (fitted through the upper leg and knee before the lower
flare) and sharara (flaring broadly from the waist or upper leg) as
distinct garments with distinct silhouette constructions; keeps saree
draping separate from lehenga styling; offers regional directions as
broad influences with "no specific regional direction" available and
explicit copy that traditions vary between communities; and contains no
designer or brand names (test-enforced denylist). **Limitation:** this
taxonomy is a bounded editorial artefact written by this project, not an
authority on South Asian bridal tradition — option lists and wording need
ongoing human review by people with cultural knowledge, and the versioning
mechanism exists precisely so corrections can ship as new versions.

## Non-goals (Phase 5A)

Phase 5A stores **no answers**: no answer submission or validation
endpoint, no `Design.questionnaire_version` linkage, no `Design.answers`
writes. The inspiration catalogue — assets, usage-rights records, uploads,
image processing, signed URLs — is Phase 5B. No frontend questionnaire
pages, no Zod/React Hook Form, no OpenAPI generation, no provider calls.

---

## Amendment — questionnaire v4 colour question types (design-handoff phase)

**Status:** accepted. Amends the "three question types — no others" boundary
recorded above. Follows the [ADR 0018](0018-questionnaire-feedback-and-visual-choice-ux.md)
precedent of amending this record rather than superseding it.

### What changed

The schema vocabulary gains **two** question types, taking it from three to
five:

| Type | Shape | Constraints |
|---|---|---|
| `colour_choice` | single-select; the answer is a declared swatch **or**, when permitted, a six-digit lower-case hex | `allow_custom: bool` |
| `colour_list` | the design's own palette; declares **no** options, since its values are user-supplied | `max_items` (**mandatory**, capped at `MAX_CUSTOM_COLOURS_LIMIT`) |

### Why the boundary moved

Questionnaire v4 replaces the single `colour_palette` (multi-select, max 4)
with three per-role colour questions — fabric, embroidery, dupatta — and lets a
bride add colours of her own. A user-invented hex **cannot be a pre-declared
option**: it is not a machine identifier, and it does not exist when the schema
is published. That leaves two designs:

1. Model custom colours **outside** the questionnaire, as a separate field on
   the design.
2. Extend the vocabulary.

We took (2). §12 requires frontend validation to be *derived from* the
machine-readable schema with Django authoritative; option (1) would have put a
user-facing answer outside the versioned schema, so it would have needed its own
parallel validation, its own versioning story and its own place in the review
screen and the DesignSpec — duplicating the machinery this ADR exists to
centralise. Colours belong in the same versioned, validated, rule-governed
answer pipeline as every other answer.

This is a **bounded** extension, not a step toward a general type system. Both
types are declarative, both have a closed constraint key set, neither adds an
expression language, and the "no eval, no executable expressions, no generic
rules engine" rule is untouched.

### The custom-palette link is structural

A schema may declare **at most one** `colour_list`, and that one is implicitly
the palette every `allow_custom` question draws from. This was chosen over an
explicit `question_id` reference so there is no reference that can dangle; the
uniqueness rule is enforced by the schema validator, and `allow_custom: true` is
rejected outright unless a `colour_list` exists.

The trade-off is real and recorded: it is the only place in this format where
one question refers to another without a named, validated reference field. If a
future requirement ever needs a second palette (per-layer colours, say), this
becomes a rework rather than an addition — at which point an explicit
`constraints.custom_colours_question_id` is the natural upgrade.

### Colour questions are barred from the rule vocabulary

A rule may **not** name a colour question in a `when` condition, and may **not**
`restrict_options` on one (`RULE_REFERENCEABLE_TYPES` in
`schema_validation.py`, mirrored by `rules.py` and the frontend). A custom hex
is not a declared option, so it could neither satisfy a condition nor be
described by a restriction — allowing either would create a value the
restriction could never express. `show`, `hide` and `require` still work: a
saree hides `dupatta_colour`, because a saree has no dupatta.

Consequently a colour answer never enters the selected-values map that rule
evaluation reads, so a user-supplied hex can never influence visibility,
requiredness or another question's allowed options.

### Answer-validation consequences

- Validation stays **total**: every malformed shape becomes a controlled
  `QuestionnaireAnswerError`, never a raw `TypeError`/`KeyError`.
- Hex is accepted case-insensitively and **normalised to lower case**, so
  persistence never holds two spellings of one colour. Strictly six digits — no
  shorthand, named colours, `rgb()`/`hsl()`, or alpha.
- A `colour_choice` answer's validity depends on its sibling `colour_list`
  answer, which dict order gives no guarantee of reaching first, so the palette
  is resolved in a **pre-pass**. A malformed palette resolves to empty, so a
  custom colour referencing it fails too rather than being silently accepted.
- The palette is **length-bounded before it is walked**. Unlike a
  `multi_choice`, whose items are checked against a small declared set and so
  fail on the first unknown value, every syntactically valid hex passes — which
  would otherwise let a caller decide how much work validation does on an
  anonymous-reachable endpoint.

### Version strategy

v4 is **additive**. v1 remains the published active seed; v2 and v3 are
untouched drafts. Answers already persisted against an earlier version keep
being read through that version's own schema — `colour_palette` and
`coverage_preferences` arrays are preserved verbatim and are **never** collapsed
to a first entry or otherwise reinterpreted. The immutability rule above is
unchanged.
