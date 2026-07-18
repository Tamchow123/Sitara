"""Safe exceptions for permanent design-image storage (Phase 11).

Every exception here is deliberately GENERIC: messages never include storage
keys, image hashes, image bytes, prompt text, questionnaire answers, provider
URLs or credentials — they are safe to log and to map onto stable machine
error codes at the pipeline/API boundary.
"""


class DesignImageError(Exception):
    """Base class for permanent design-image failures."""


class DesignImageProcessingError(DesignImageError):
    """The staged bytes were rejected or could not be canonically processed.

    Confirmed-bad content: retrying without different input cannot succeed."""


class DesignImageIngestRetry(DesignImageError):
    """A transient/ambiguous storage failure during ingest.

    The permanent-content state is UNKNOWN (a connection blip, a backend
    restart, an interrupted write). Safe to retry: the staged source object is
    durable and re-running the deterministic ingest never repeats a provider
    call."""


class DesignImageIngestFailed(DesignImageError):
    """A confirmed, non-retryable ingest failure.

    Corrupt or conflicting permanent content, a missing/divergent staged
    object, a key-renaming backend, or violated preconditions. Never resolved
    by regeneration — recovery is an operator decision."""


class DesignImageImmutable(DesignImageError):
    """Existing permanent image provenance conflicts with the new result.

    Once a DesignVersion carries permanent image metadata it is immutable: a
    different original, thumbnail, hash, key or processor output must create a
    NEW DesignVersion, never overwrite the existing one."""


class DesignImageNotReady(DesignImageError):
    """The DesignVersion has no complete permanent image provenance yet."""


class DesignImageDeliveryUnavailable(DesignImageError):
    """Signed browser delivery is not currently possible.

    Raised for the filesystem backend (deliberately no delivery path in
    Phase 11 — no backend image proxy exists) and for storage outages while
    confirming the private objects before signing."""
