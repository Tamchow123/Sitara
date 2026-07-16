# ADR 0003 — Session authentication (Phase 3B)

- **Status:** Accepted (2026-07-16, after the Phase 3B test suite passed)
- **Deciders:** Sitara project
- **Related:** ADR 0002 (application foundation)

## Context

Sitara's MVP proposal originally listed user accounts as a non-goal. Phase 3B
revises that: bridal concepts are private, and privacy needs an identity to be
private *to*. This ADR records the authentication mechanism added in Phase 3B
as an **optional account capability** — whether future design creation
*requires* an account is deliberately not decided here.

The Phase 3A foundation already provides a custom user model (UUID primary
key, canonical case-insensitive email as the login identifier), Django 5.2,
DRF with authenticated-by-default permissions, PostgreSQL, Redis, and a
Next.js 15 App Router frontend.

## Decision

**Django server-side database sessions** are the only authentication
mechanism:

- `django.contrib.auth` — `authenticate()`, `login()`, `logout()`;
- database-backed sessions delivered via an **HttpOnly, SameSite=Lax**
  cookie (`Secure` in production);
- standard **Django CSRF protection** on every unsafe request;
- no second user model, no third-party auth package.

### Why sessions (and why not JWT / localStorage)

- Server-side sessions are revocable instantly (logout flushes the session
  row); JWTs are bearer artefacts that stay valid until expiry unless a
  denylist re-introduces server state anyway.
- HttpOnly cookies are invisible to JavaScript, so XSS cannot exfiltrate the
  credential. Tokens in localStorage/sessionStorage/IndexedDB are readable by
  any injected script — an unacceptable trade for a same-origin browser app.
- Django's session + CSRF machinery is mature, audited and already in the
  dependency tree; refresh-token rotation, clock skew and re-implementation
  bugs disappear from the threat model.
- The app is one browser frontend talking to one backend. JWT's advantages
  (statelessness across many services, non-browser clients) do not apply.

### Same-origin Next.js rewrite

The browser talks **only** to the web origin (`http://localhost:3001`
locally). `next.config.ts` rewrites `/api/:path*` to the server-only
`API_INTERNAL_BASE_URL` (`http://api:8000` in Docker, `http://localhost:8000`
natively). Consequences:

- cookies are first-party and `SameSite=Lax` "just works";
- no backend host is exposed through any `NEXT_PUBLIC_*` variable
  (`NEXT_PUBLIC_API_BASE_URL` was removed);
- CORS is unnecessary for the browser path.

### CSRF flow

1. The client bootstraps `GET /api/v1/auth/csrf/` and keeps the returned
   token **in memory only**.
2. Every unsafe request sends `X-CSRFToken`; the `sitara_csrftoken` cookie is
   the other half of the double-submit check.
3. Login/registration views are plain Django JSON views under
   `@csrf_protect` — anonymous requests get full CSRF enforcement rather
   than relying on DRF `SessionAuthentication` (which skips CSRF for
   anonymous requests). `csrf_exempt` is forbidden.
4. Django rotates the token on login; register/login/logout responses return
   the fresh token, which replaces the cached one.
5. CSRF failures return JSON (`csrf_failed`, HTTP 403) via
   `CSRF_FAILURE_VIEW` — never Django's HTML page or internal reason. The
   client retries exactly once with a fresh token.

### Project-specific cookie names

`sitara_sessionid` and `sitara_csrftoken`. Cookies are host-scoped, not
port-scoped; distinctive names prevent collisions with other apps on
`localhost`. No cookie domain is set; path stays `/`.

### Password policy

Django's standard validators: `UserAttributeSimilarityValidator`,
`MinimumLengthValidator` (**minimum 12**), `CommonPasswordValidator`,
`NumericPasswordValidator`; `validate_password(password, user=pending_user)`
runs at registration; accepted password input is capped at 128 characters.

### Redis authentication rate limits

Fixed-window counters on Django's built-in Redis cache backend
(`REDIS_CACHE_URL`, logical DB 1 — separate from the Celery broker):

| Limiter              | Default        | Env variables                                        |
| -------------------- | -------------- | ---------------------------------------------------- |
| Login per IP         | 20 / 5 minutes | `AUTH_LOGIN_IP_LIMIT` / `AUTH_LOGIN_IP_WINDOW_SECONDS` |
| Login per IP+email   | 5 / 5 minutes  | `AUTH_LOGIN_EMAIL_LIMIT` / `AUTH_LOGIN_EMAIL_WINDOW_SECONDS` |
| Registration per IP  | 5 / hour       | `AUTH_REGISTER_IP_LIMIT` / `AUTH_REGISTER_IP_WINDOW_SECONDS` |

- `REMOTE_ADDR` only; `X-Forwarded-For` is not trusted yet.
- Identifiers are HMAC-SHA256-hashed (keyed by `SECRET_KEY`) before entering
  cache keys — no raw email or IP is ever stored.
- Limit hits return HTTP 429 + `Retry-After` + stable code
  `auth_rate_limited`, without revealing which limiter fired.
- Successful login clears the email-specific counter.
- **Fail closed:** if the cache is unreachable, auth endpoints return 503
  (`auth_unavailable`) rather than proceeding unprotected.

### Backend is the authorization boundary

DRF defaults stay `IsAuthenticated` + `SessionAuthentication`. Only
health/live, health/ready, config/public, auth/csrf, auth/register,
auth/login and auth/me allow anonymous access (logout is anonymous-callable
but CSRF-protected). The Next.js `middleware.ts` cookie-presence redirect on
`/account` is a navigation optimisation **only**; every future design API
must enforce ownership server-side.

All auth responses carry `Cache-Control: no-store`. Responses and logs never
contain passwords, hashes, session IDs, raw identifiers or tokens (outside
the intended `csrf_token` field).

## Non-goals (Phase 3B)

Email verification, password reset/change, email change, account deletion,
OAuth/social login, MFA, magic links, JWT, refresh tokens, user profiles,
roles beyond Django's staff machinery, questionnaire/design models,
guest-to-user design claiming, uploads, provider calls, email delivery,
production deployment.

**Public production registration is NOT feature-complete** until email
verification and password recovery are separately designed — accounts are
currently unrecoverable if the password is lost.

## Scope reconciliation

- Accounts were an MVP non-goal in the original proposal; Phase 3B adds them
  as an *optional* capability and the proposal is updated accordingly.
- Whether design creation will require authentication is undecided.
- Guest design claiming/migration is not implemented — and nothing needs
  migrating, because no anonymous design records exist yet.
- Private design ownership rules arrive with the design models.
- Demo/status endpoints remain public.
