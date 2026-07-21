"""Deterministic zero-cost demo pipeline (Phase 15).

Everything under this package is local, zero-network and deterministic: a
versioned demo-asset manifest and selector (this module), and — added in
later Phase 15 commits — deterministic DesignSpec/refinement engines and
asynchronous pipeline adapters. Nothing here ever constructs an Anthropic or
Replicate client or makes a provider network call."""
