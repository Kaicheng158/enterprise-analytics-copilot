# PV2-03a — Local Authentication & Session Foundation

Status: local implementation and synthetic acceptance only. Default off. Not a public registration service and not production authentication.

## Scope and boundaries

PV2-03a creates a product-user identity boundary that is independent from both the Workspace Demo visitor and the fixed internal Agent principal. A browser cannot submit `user_id`, principal, role or scope. The server derives an `AuthPrincipal` from an opaque session cookie after resolving its digest in PostgreSQL.

The normal API, `/chat`, `/rag/chat`, the Workspace Agent bridge and the internal Agent API do not mount `/auth-api`. The separate `backend.local_auth_app:app` surface accepts only a configured loopback Host, loopback network peer and same-origin browser requests. `LOCAL_AUTH_ENABLED` defaults to false. The existing Demo path remains a separate preview and cannot create a session or access private resources.

This phase does not create Projects, ProjectOwner, AssetGrant, collaboration roles, MFA, email verification, password recovery, mail delivery, public registration or customer upload. The only protected resource is an identity-only `my-workspace` boundary that confirms the server-created principal; `projects_available=false` is explicit.

## Contracts

Migration 009 adds a new `product_auth` schema without changing migrations 007 or 008:

| Contract | Purpose | Sensitive-data rule |
|---|---|---|
| `users` | immutable UUID identity, normalized email account, display name and active/disabled state | no password/session value |
| `credentials` | Argon2id encoded password hash and policy version | plaintext is never stored or logged |
| `sessions` | user-bound opaque session digest, CSRF digest, state and expiry/revocation timestamps | raw session and CSRF tokens are never stored |
| `login_rate_limits` | server-HMAC subject key, bounded failure window and block time | account and client address are not stored in the limiter key |
| `audit_events` | content-free register/login/session/logout result metadata | no account, password, cookie, CSRF token or request body |

The public request models use strict Pydantic validation and reject extra fields. Account format is an email address normalized with Unicode NFKC, trim and case-folding. Passwords are accepted as `SecretStr`, limited to 12–128 characters for registration, hashed with per-password random salt using Argon2id and discarded after the request. Login failures use the same generic response for unknown accounts and wrong passwords. Duplicate registration returns a generic registration conflict rather than confirming the existing account value.

## Session and browser security

- Session identifiers use `secrets.token_urlsafe(32)` and PostgreSQL stores only SHA-256 digests.
- Cookies are `HttpOnly`, `SameSite=Strict`, path `/`, bounded by the server session TTL and inaccessible to Workspace JavaScript.
- Register/login writes require an exact same-origin `Origin`; all auth requests reject cross-site fetch metadata, non-loopback peers and DNS-rebinding Host values.
- Logout requires the session cookie and a per-session `X-CSRF-Token`. `GET /auth-api/session` rotates the CSRF token so a browser refresh can resume safely without persisting it in local/session storage.
- Expired and revoked sessions cannot resolve a principal. Concurrent logins create separate revocable sessions.
- Failed logins are limited by a server-HMAC of normalized account plus client address. The HMAC secret is required only when the feature is enabled and is never committed.
- Validation and service errors are locally defined and do not echo passwords, cookies, database bodies or unexpected input values.

Local HTTP acceptance permits `Secure=false` only with an HTTP loopback origin. The configuration contract rejects a production mode unless the origin is HTTPS and `cookie_secure=true`. This validates the future cookie policy but does not claim that the localhost service is a production deployment. Production session secret management, TLS termination, proxy trust, centralized rate limits, device/session management, MFA and security monitoring remain Hold.

## Local API contract

| Method/path | Result | Notes |
|---|---|---|
| `GET /auth-api/config` | enabled mode and supported account/password contract | safe while disabled |
| `POST /auth-api/register` | `local-auth-session-v1`, HttpOnly cookie, next `/#overview` | localhost synthetic acceptance only |
| `POST /auth-api/login` | same session response | generic invalid-credential response |
| `GET /auth-api/session` | current user/session plus rotated CSRF token | browser refresh/restart continuity |
| `POST /auth-api/logout` | 204 and cookie deletion | requires matching CSRF token; session revoked server-side |
| `GET /auth-api/my-workspace` | server-created principal, `projects_available=false` | unauthenticated requests return 401 |

Workspace Login/Register keeps the approved bilingual visual system. When `/auth-api/config` is disabled or absent it preserves the original non-reading Demo behavior. When the independent local app is explicitly enabled it submits only the account/display-name/password contract, keeps CSRF only in memory, redirects successful registration/login directly to My Workspace (`#overview`), protects Workspace routes client-side as a usability layer and uses the server route as the actual authorization boundary. Switching UI language never creates or refreshes an identity.

## Feature Value Gate v1

Decision: **Approve for local foundation; customer/public release remains Hold**.

- User workflow unlocked: one local user can register once, resume an authenticated browser session, enter My Workspace and sign out. This is required before Investigate/Simulate/Decide data can be bound to a private owner in PV2-03b.
- Baseline: Demo entry has no identity; every protected-data experiment needs an ad-hoc fixed scope and cannot distinguish an authenticated owner.
- Pre-registered work metrics: successful returning-user flow requires at most one login submission after browser restart; refresh requires zero credential re-entry while the session is valid; sign-out requires one action; an unauthenticated private-resource attempt must be blocked 100%; wrong-password/unknown-account responses must not differ in public message/status; session tokens must appear zero times in database plaintext/log evidence.
- Local result: registration returns one authenticated session and direct My Workspace target; refresh/restart reused the valid session with zero credential re-entry; logout was one request and revoked the session; protected-resource, cross-origin, missing-CSRF, expired and revoked access were blocked in synthetic acceptance; database checks found only password/session/CSRF derivatives. These engineering checks establish the foundation, not measured customer time savings.
- Duplicate/recovery benefit: the durable server session removes the need to re-enter credentials on each page refresh. Password recovery remains Hold; there is no fake success path.
- Duplication: does not replace the internal Agent API key, Demo visitor, execution Checkpoint/Receipt or Project State.
- Cost/operations: Argon2id CPU/memory cost, PostgreSQL session/rate-limit/audit rows, secret rotation, expiry cleanup and future account support. No model cost.
- Security/privacy cost: credential handling, account identifiers, session compromise, brute force and CSRF. Default-off localhost restriction and synthetic-only acceptance contain current exposure; production safeguards listed below remain blockers.

## Acceptance evidence and limitations

Dedicated tests cover strict contracts, Argon2id salt/verification, registration, duplicate account, login and generic failures, rate limiting, HttpOnly/SameSite cookie attributes, exact-origin checks, CSRF, session refresh, app restart, multiple sessions, expiry, revocation, protected-resource denial, validation redaction, migration isolation and Public privilege revocation. Migration 009 is applied only to a uniquely named disposable database and then dropped.

Final local evidence for this worktree: 459/459 Python offline tests, 148/148 isolated PostgreSQL tests and 20/20 Workspace JavaScript unit tests passed. A real loopback Uvicorn smoke served the approved bilingual Register UI and completed synthetic HTTP register (201) → logout (204) → login (200) → session refresh (200) → protected My Workspace identity (200, no Projects exposed) → logout (204); the explicitly named smoke database was then dropped. This is local engineering evidence, not a production penetration test or customer usability study.

Remaining production blockers:

- verified email ownership, password recovery, mail delivery and abuse controls;
- MFA/passkeys, password breach checks, account lock/recovery policy and session/device management;
- HTTPS deployment, reverse-proxy trust, production secret manager and key rotation;
- distributed/global rate limiting, anomaly detection, audit retention/monitoring and scheduled expired-session cleanup;
- formal User deletion/retention, regulatory/privacy review and support operations;
- ProjectOwner/ProjectMembership/AssetGrant authorization, asset/task authorization and revocation propagation (PV2-03b);
- production object storage/KMS/malware/backup blockers already recorded by PV2-02c.

## PV2-03b dependencies

PV2-03b should add a server-owned `ProjectOwner` relationship and authorization service that accepts `AuthPrincipal`, not a client-asserted identity. It must bind existing `dataset_store.projects` to a user-owned product Project without rewriting migration 007, verify every project/asset/task read and write, test cross-user negative cases and revoke queued/runtime access when authorization changes. Asset-level grant and collaboration roles remain a later independently versioned scope unless explicitly approved.
