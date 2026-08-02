"""The provider-facing JSON schema for structured design output.

Anthropic's structured outputs compile the supplied JSON schema into a
constrained-decoding grammar. Size-and-shape keywords — ``pattern``,
``minLength``/``maxLength``, ``minItems``/``maxItems`` — are the expensive part:
the grammar has to encode counting and character classes, so a schema that is
small on disk can compile to a grammar that is not. DesignSpec v3 crossed that
line and every live structured request failed with:

    invalid_request_error: The compiled grammar is too large ...

so this module hands the provider a copy with those keywords removed.

**This does not weaken validation.** Model output is untrusted either way, and
the strict model is still applied to whatever comes back — constrained decoding
only ever made valid output *likelier*, it was never the boundary. What is lost
is the decoder's guarantee, not the check: an over-long ``concept_summary`` now
fails our own validation and becomes a retryable ``parse_error`` instead of
being impossible to emit.

To keep the model informed of the bounds it can no longer be forced into, each
stripped constraint is restated in the field's ``description``, which costs
prompt tokens rather than grammar size.

Field names, types, nesting, ``required``, ``enum`` and ``$ref``/``$defs`` are
all preserved exactly: the provider still receives the full shape.
"""

from typing import Any

# Removed from the provider-facing copy. Numeric bounds (``minimum``/
# ``maximum``) are deliberately NOT in this set — they are cheap to compile and
# carry real meaning for the few integer fields that use them.
STRIPPED_KEYWORDS = frozenset(
    {
        "pattern",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    }
)


def _describe(constraints: dict[str, Any]) -> str:
    """Restate stripped constraints as a short human-readable sentence."""
    parts: list[str] = []

    minimum = constraints.get("minLength")
    maximum = constraints.get("maxLength")
    if minimum is not None and maximum is not None:
        parts.append(f"{minimum}-{maximum} characters")
    elif maximum is not None:
        parts.append(f"at most {maximum} characters")
    elif minimum is not None:
        parts.append(f"at least {minimum} characters")

    min_items = constraints.get("minItems")
    max_items = constraints.get("maxItems")
    if min_items is not None and max_items is not None:
        parts.append(f"{min_items}-{max_items} items")
    elif max_items is not None:
        parts.append(f"at most {max_items} items")
    elif min_items is not None:
        parts.append(f"at least {min_items} items")

    pattern = constraints.get("pattern")
    if pattern is not None:
        parts.append(f"matching {pattern}")

    return "Must be " + ", ".join(parts) + "." if parts else ""


def _strip(node: Any) -> Any:
    if isinstance(node, list):
        return [_strip(item) for item in node]
    if not isinstance(node, dict):
        return node

    constraints = {key: node[key] for key in STRIPPED_KEYWORDS if key in node}
    stripped = {key: _strip(value) for key, value in node.items() if key not in STRIPPED_KEYWORDS}

    note = _describe(constraints)
    if note:
        existing = stripped.get("description")
        # Only ever appended to a string description; a schema that used
        # ``description`` for something else is left alone rather than coerced.
        if isinstance(existing, str) and existing.strip():
            stripped["description"] = f"{existing.rstrip()} {note}"
        elif existing is None:
            stripped["description"] = note

    return stripped


#: The field the provider is NOT asked to produce. ``source_selections`` is the
#: user's own canonical machine-value echo, which the caller already holds and
#: passes in the request — asking the model to reproduce it exactly bought
#: nothing and cost everything: it is a flat object of ~20 OPTIONAL fields, so
#: the grammar has to accept every subset in every order. Measured against the
#: live API, that one field is on its own over the limit, while the rest of the
#: v3 schema is comfortably accepted.
#:
#: Removing it is a correctness gain as well as the fix. The echo is now
#: injected from the request after parsing, so a model that miscopied a
#: selection can no longer have that mistake persisted as the design's own
#: source_selections. Every cross-field rule still runs, because the merged
#: whole is validated against the strict model.
ECHOED_FIELD = "source_selections"


def _prune_unreferenced_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop ``$defs`` entries nothing references any more.

    Version-agnostic: it follows ``$ref`` rather than naming SourceSelectionsV1
    /V2/V3, so a future spec version needs no change here.
    """
    defs = schema.get("$defs")
    if not isinstance(defs, dict):
        return schema

    def referenced(node: Any, found: set[str]) -> set[str]:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                found.add(ref.removeprefix("#/$defs/"))
            for value in node.values():
                referenced(value, found)
        elif isinstance(node, list):
            for item in node:
                referenced(item, found)
        return found

    body = {key: value for key, value in schema.items() if key != "$defs"}
    live = referenced(body, set())
    # A retained definition may itself reference others; iterate to a fixpoint.
    while True:
        expanded = set(live)
        for name in live:
            referenced(defs.get(name, {}), expanded)
        if expanded == live:
            break
        live = expanded

    kept = {name: value for name, value in defs.items() if name in live}
    return {**body, "$defs": kept} if kept else body


def provider_output_schema(model_cls: type) -> dict[str, Any]:
    """The JSON schema to send as ``output_config.format``.

    Constraint-free, and without the echoed ``source_selections`` field. Pure:
    the model's own schema is never mutated, and repeated calls return equal
    results.
    """
    schema = _strip(model_cls.model_json_schema())

    properties = schema.get("properties")
    if isinstance(properties, dict) and ECHOED_FIELD in properties:
        schema["properties"] = {k: v for k, v in properties.items() if k != ECHOED_FIELD}
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [name for name in required if name != ECHOED_FIELD]

    return _prune_unreferenced_defs(schema)
