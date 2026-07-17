# ADR 0005 — Versioned questionnaire schema (Phase 5A)

- **Status:** Accepted (2026-07-17, after the Phase 5A test suite passed)
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
