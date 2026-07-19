"""Permanent private design-image storage (Phase 11).

Plain package (not a Django app — it has no models): canonical image
processing, deterministic key building, the crash-safe ingest service and
signed delivery for generated design images. Everything here operates on the
``design_images`` storage alias resolved at call time via
``django.core.files.storage.storages`` — never a module-level storage
instance — so tests and environment overrides always take effect.
"""
