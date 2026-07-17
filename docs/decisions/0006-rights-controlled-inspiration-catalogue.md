# ADR 0006 — Rights-controlled inspiration catalogue (Phase 5B)

- **Status:** Accepted (2026-07-17, after the Phase 5B test suite passed)
- **Deciders:** Sitara project
- **Related:** ADR 0002 (application foundation), ADR 0005 (versioned
  questionnaire schema)

## Context

The questionnaire wizard (Phase 7) will let users browse a small set of
inspiration images. Those images will later also feed AI generation, so
every image in the catalogue must carry documented, verified usage rights
covering display, AI input, derivative generation and commercial use —
scraped or unverified images can never enter the system. Uploaded
photographs also carry dangerous metadata (EXIF, GPS) and dangerous
container formats, so what users receive must never be what staff
uploaded.

## Decision

### Staff-managed only; no user uploads; no URL fetching

The catalogue is populated exclusively through Django admin by staff.
There is no user upload feature, and the backend never fetches an image
from an external URL — ingestion accepts uploaded bytes only.

### Sanitise everything; discard the original

`ingest_inspiration_image` accepts only bytes that DECODE as single-frame
JPEG, PNG or WebP (the decoded format decides — never the extension or
claimed content type), within strict byte (`INSPIRATION_MAX_UPLOAD_BYTES`)
and pixel (`INSPIRATION_MAX_IMAGE_PIXELS`) bounds checked before full
decode (decompression-bomb guard). Pillow is the only image dependency —
no ImageMagick, no libmagic, no external processing services. The
pipeline applies EXIF orientation, strips ALL metadata (EXIF, GPS, XMP,
ICC, comments), composites transparency onto a neutral background,
converts to RGB, resizes to at most `INSPIRATION_OUTPUT_MAX_EDGE` (2048)
pixels without upscaling, produces a `INSPIRATION_THUMBNAIL_EDGE` (512)
thumbnail, re-encodes both as WebP and verifies the results by reopening
them. **Only the two sanitised WebP derivatives are retained; the raw
original upload is discarded and never stored.** Storage keys are
server-generated (`catalogue/inspiration/<asset-uuid>/<random>/…`) and
contain no original filename and no identity data.

### Two tables: rights are records, assets are images

`UsageRights` documents WHY one image may be used: basis (owned /
commissioned / licensed / public_domain / permission_granted), holder,
evidence reference, optional source/licence details, expiry, the four
usage permissions (public display, AI input, derivative generation,
commercial use) and attribution. Verification is a separate, service-only
action (`verify_usage_rights`): pending → verified requires evidence, a
named holder, unexpired terms, all four permissions and complete
attribution when required. A rejected record can never become verified —
corrections are new records. `InspirationAsset` links to its rights via
`PROTECT` and has its own draft → approved → retired lifecycle;
**approval and verification are deliberately separate actions**, and
`approve_inspiration_asset` re-checks every rights condition under lock.

### Private storage remains authoritative; delivery through Django

The existing private S3 configuration is unchanged: `default_acl=None`,
signed querystring auth, no public bucket policy, no public-read object
ACLs. Public image delivery streams the sanitised WebP through
eligibility-checked Django endpoints (`Content-Type: image/webp`,
`Content-Disposition: inline`, `X-Content-Type-Options: nosniff`,
`Cache-Control: no-store`) — no raw S3 URLs, no redirects to MinIO.
Signed URLs are deferred.

### One eligibility definition; immediate disappearance

`InspirationAsset.objects.publicly_eligible()` is the single queryset
used by the catalogue list and both image endpoints: status approved AND
rights verified AND expiry absent-or-future AND all four permissions.
Because every public response is `no-store` and eligibility is evaluated
per request, an expired, revoked or retired asset disappears immediately;
anything ineligible is an indistinguishable 404.

### Testing without real images

No real third-party image is committed to the repository — every test
image is generated in memory with Pillow. Tests run against an isolated
in-memory storage backend (CI has no MinIO).

## Consequences

- Every public catalogue image is provably sanitised, rights-verified and
  revocable at request granularity.
- Replacing an approved image means retiring the asset and creating a new
  one — history is never rewritten, and rights linked to an approved
  asset freeze in admin.
- Deferred: signed URLs, design-to-inspiration linking, any user-facing
  upload, search/ranking, caption or moderation AI.
