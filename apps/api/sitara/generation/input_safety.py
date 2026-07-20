"""Generated-output and free-text safety scanning (Phase 8).

Re-exports :mod:`sitara.content_safety` unchanged — the scanning logic moved
to a dependency-free package-root module (Phase 13) so the catalogue app's
approval-time defence could reuse it without an app-to-app import reversal.
Every existing ``from .input_safety import ...`` / ``from
sitara.generation.input_safety import ...`` call site keeps working as-is."""

from sitara.content_safety import (
    GeneratedContentRejected,
    RejectionCategory,
    UnsafeUserTextError,
    contains_markup,
    contains_phrase,
    contains_url,
    iter_strings,
    scan_design_spec,
    scan_generated_text,
    scan_user_text,
    strip_format_characters,
)

__all__ = [
    "GeneratedContentRejected",
    "RejectionCategory",
    "UnsafeUserTextError",
    "contains_markup",
    "contains_phrase",
    "contains_url",
    "iter_strings",
    "scan_design_spec",
    "scan_generated_text",
    "scan_user_text",
    "strip_format_characters",
]
