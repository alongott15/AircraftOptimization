# Security & Safety

This app influences airworthiness-related decisions. Security protects the data and the integrity of
recommendations; **safety** principles make sure the software can never make an unsafe aircraft look eligible.

## 1. Safety principles (apply to every change)
| # | Principle | Enforcement |
|---|-----------|-------------|
| S1 | **Fail-safe:** unknown, missing, stale or unparseable data ⇒ not eligible (`DATA_MISSING` / `DATA_STALE`) | Property tests (TESTING.md §3.2); reviewer agent |
| S2 | **ML cannot change eligibility** — only ranking among eligible aircraft | Architecture import contract (`domain.compliance` has no ML import); test that toggling models never changes eligible set |
| S3 | **Projection to mission end + buffer** for every limit, including items expiring mid-flight | Golden scenarios SC-03/04/08/13 |
| S4 | **Re-validate at commit time** — assignment re-runs the compliance engine; stale runs (> 30 min) rejected | API 409 `RUN_STALE` / `ELIGIBILITY_CHANGED` tests |
| S5 | **Advisory only** — disclaimer on every recommendation screen; no "release to service" function exists | UI test |
| S6 | **Tolerances never used for eligibility** in POC (warning only) | Unit test |
| S7 | **Full traceability** — every run stores clock, policy, weights, model versions, data snapshot time | DB schema + test |
| S8 | **Determinism** — same inputs ⇒ same output | Golden snapshot tests |
| S9 | **Force-include cannot bypass compliance** | API test (422 `FORCE_INCLUDE_INELIGIBLE`) |

## 2. Threat model (summary, STRIDE)
| Asset | Threat | Mitigation |
|-------|--------|------------|
| Vertica lake | Credential leak → data exfiltration / writes | Read-only service account, network allow-list, TLS, secrets in vault/env, never in repo |
| Recommendations | Tampering with policy/weights to push unsafe choices | Weights cannot affect eligibility (S2); policy changes ADMIN-only, audited with before/after |
| Sessions | Hijacking, CSRF | HttpOnly+Secure+SameSite=Strict cookie, CSRF double-submit token, short TTL (8 h), rotation on login |
| API | Injection | SQLAlchemy bound parameters only; no string-built SQL; Pydantic validation on every input |
| UI | XSS via MRO free text (MA titles/descriptions) | React escaping only; `dangerouslySetInnerHTML` banned (ESLint); CSP without `unsafe-inline` scripts |
| Audit log | Repudiation | Append-only table, no update/delete grants, includes user, request id, IP |
| Availability | Expensive optimization abuse | Per-user rate limit on `/optimize` (10/min), solver time limit 2 s, request body size limit |
| Supply chain | Malicious dependency | Lockfiles committed, `pip-audit` + `pnpm audit` in CI, pinned versions, no runtime CDN |

## 3. Authentication & authorization

### 3.1 Modes
- **POC (`APP_AUTH_MODE=local`)**: users in `app_user`, argon2id hashes, login rate limit 5/min/IP + lockout after
  10 failures (15 min). Demo passwords come from `APP_DEMO_PASSWORD` in `.env` (template `.env.example`), never committed.
  Personal accounts: `make user` (= `python -m app.manage create-user`, default role ADMIN) and `make password`. The
  password is read from a hidden prompt (never from arguments/shell history), must have ≥ 12 characters, is stored
  as argon2id and never logged; creation/reset is audited (`USER_CREATE`, `USER_PASSWORD_SET`). Disabled in
  `APP_AUTH_MODE=oidc`. `make demo` recreates the POC database, so accounts must be re-created after reseeding.
- **Production (`APP_AUTH_MODE=oidc`)**: OIDC (Azure AD / Keycloak / ADFS) authorization-code + PKCE; roles mapped from
  IdP groups (`AO-Viewers`, `AO-Planners`, `AO-MaintControl`, `AO-Admins`). Local login disabled.
- Session: signed JWT (HS256 with ≥ 32-byte secret from env) in cookie; `typ=session`, `sub`, `role`, `exp`, `jti` —
  issued after local or SSO login. Logout puts the `jti` on an in-process denylist until the token's `exp`.
  The OIDC state cookie carries `typ=oidc_state`, so neither token can be replayed as the other.
- OIDC settings: `APP_OIDC_ISSUER`, `APP_OIDC_CLIENT_ID`, `APP_OIDC_CLIENT_SECRET`, `APP_OIDC_REDIRECT_URI`, `APP_OIDC_SCOPES`,
  `APP_OIDC_ROLE_CLAIM` (default `groups`), `APP_OIDC_ROLE_MAP` (`AO-Admins=ADMIN,…`), `APP_OIDC_USERNAME_CLAIM`. State, nonce and PKCE
  verifier live in a 10-minute signed `ao_oidc` cookie (SameSite=Lax, path `/api/v1/auth/oidc`); the ID token is verified
  against the IdP JWKS (RS256/ES256, issuer, audience, expiry, nonce; `azp` when there are several audiences). Users
  without a mapped group are rejected. In prod the issuer and all discovered endpoints must be `https://`.
- SSO identity linking: SSO users are keyed by `oidc_subject = "<iss>|<sub>"`, **never** by username. An IdP user whose
  username is already used by another account (e.g. the local `admin`) is rejected; usernames > 50 chars are rejected
  (no truncation collisions); a disabled user is rejected **before** the role is refreshed. Local password login never
  authenticates an SSO-linked row.
- Login rate limit: `APP_LOGIN_RATE_PER_MIN` (default 5 per IP; raised only for the E2E suite). Limiters, lockouts, the
  `jti` denylist and ML jobs are in-process ⇒ the backend runs **one** uvicorn worker. Behind nginx the client IP comes
  from `X-Forwarded-For`, which nginx **overwrites** with `$remote_addr`; uvicorn trusts it only via
  `FORWARDED_ALLOW_IPS` (compose: the backend has no published port). Assignment writes (single-mission assign and
  horizon apply) re-validate and commit under a process lock so concurrent assigns on the same tail cannot both pass;
  a multi-instance production deployment must add a database lock (e.g. PostgreSQL advisory lock per tail).

### 3.2 Roles
`VIEWER` · `PLANNER` · `MAINTENANCE_CONTROLLER` · `ADMIN`

### 3.3 Permission matrix
| Capability | VIEWER | PLANNER | MAINT. CTRL | ADMIN |
|------------|:-----:|:------:|:----------:|:----:|
| Dashboard, fleet, aircraft, 3D | ✓ | ✓ | ✓ | ✓ |
| View missions & runs | ✓ | ✓ | ✓ | ✓ |
| Create/edit missions, optimize, assign, cancel | | ✓ | | ✓ |
| What-if eligibility check | | ✓ | ✓ | ✓ |
| View audit log | | | ✓ | ✓ |
| View maintenance board & recommendations | ✓ | ✓ | ✓ | ✓ |
| Schedule maintenance tasks (`MAINTAIN`) — planning only, never changes eligibility | | | ✓ | ✓ |
| Edit planning policy, weights, hangar capacity | | | | ✓ |
| Retrain ML models | | | | ✓ |

Enforced server-side with a FastAPI dependency `require_role(...)` on every router; the UI hides controls but is never
the enforcement point. Each endpoint has an authorization test (TESTING.md §3.5).

## 4. Data protection
- Data classification: operational/internal. No passenger PII. User data limited to username, display name, role.
  Maintenance schedules (FR-91) add free text — `assignee` (≤ 80, a team or person) and `notes` (≤ 500) — which may
  name people and is copied into the append-only audit log; prefer team names. Control and bidi-formatting
  characters are rejected (NUL breaks PostgreSQL; overrides could disguise text in the RTL UI).
- TLS everywhere in production (nginx terminates TLS; backend ↔ Vertica TLS).
- SQLite POC file contains synthetic data only — never copy real lake data into it.
- Logs: no secrets, no tokens, no full payloads; tail numbers and IDs are fine. A structlog processor redacts keys
  matching `password|secret|token|dsn|cookie|authorization`. `X-Request-ID` is accepted only if it is a UUID.
- Backups: app store (PostgreSQL) daily; Vertica is owned by the data team.

## 5. Secrets & configuration
- All secrets via environment / secret store: `APP_JWT_SECRET`, `APP_VERTICA_DSN`, `APP_STORE_URL`, `APP_OIDC_CLIENT_SECRET`,
  `APP_DEMO_PASSWORD`.
- `.env` git-ignored; `.env.example` lists keys with **blank** secrets. `make setup` runs `scripts/init_env.py`, which
  creates `.env` (mode 0600) with a random `APP_DEMO_PASSWORD` and `APP_JWT_SECRET`.
- Outside prod, an empty or previously published `APP_JWT_SECRET` is replaced by a random per-process secret (sessions
  then end on restart) — a public secret is never used for signing.
- Startup check: in `APP_ENV=prod`, refuse to start if `APP_JWT_SECRET` is default/short/published, if
  `APP_AUTH_MODE=local`, if cookies are not `Secure`, if CORS allows `*`, or if OIDC settings are incomplete.
- The POC compose stack runs `APP_ENV=dev` over http and is bound to `127.0.0.1:8080` only.
- Claude Code hook blocks edits to `.env*` files except `.env.example` (AGENTS.md §5).

## 6. HTTP hardening
- Security headers (nginx + FastAPI middleware): `Content-Security-Policy: default-src 'self'; img-src 'self' data: blob:;
  style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'`
  (Mantine needs inline styles; three.js/troika needs `blob:` workers), `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: same-origin`, `Permissions-Policy` minimal.
- nginx repeats the security headers inside `location /assets/` (an `add_header` there would otherwise drop them).
- CORS: explicit origins only; credentials allowed only for those origins.
- Request size limit 1 MB; JSON only on API routes.
- Errors: RFC 9457 without stack traces; correlation via `request_id`.
- `/ready` is unauthenticated, so its result (DB + data-source probe) is cached for 10 s.

## 7. Vertica access
- Dedicated read-only user `aircraft_opt_ro` with `USAGE` on schema `aircraft_opt` and `SELECT` on `ao_v_*` views only.
- Resource pool with memory/concurrency caps; statement timeout 30 s.
- Connection from backend hosts only (firewall allow-list).
- Contract test validates expected views/columns before deployment.
- TLS: in prod the DSN must use `tlsmode=verify-ca` or `verify-full` (optionally `tls_cafile=`); startup fails otherwise.
- The schema name is validated (`^[A-Za-z_]\w{0,62}$`) and view names are whitelisted before they reach SQL.

## 8. ML security
- Models loaded only from `APP_MODELS_DIR` with a registry entry; verify SHA-256 of artifact against `ml_model` row
  before `joblib.load` (pickle is code execution — never load untrusted artifacts). The bytes are read once, hashed and
  deserialized from memory, so the file cannot be swapped between check and load.
- Retraining ADMIN-only, audited; training data hash recorded.
- Model outputs clipped to [0, 1]; NaN ⇒ neutral fallback + warning.

## 9. Audit events (minimum)
`LOGIN`, `LOGIN_FAILED`, `LOGOUT`, `MISSION_CREATE`, `MISSION_UPDATE`, `MISSION_CANCEL`, `OPTIMIZE`, `ASSIGN`,
`ASSIGN_CANCEL`, `POLICY_UPDATE` (before/after), `HANGAR_CAPACITY_UPDATE`, `MODEL_RETRAIN`, `FORCE_INCLUDE_REJECTED`,
`HORIZON_PLAN`, `HORIZON_ASSIGN`, `WEIGHTS_UPDATE` (before/after), `TG_WEIGHTS_UPDATE` (before/after),
`USER_CREATE`, `USER_PASSWORD_SET`, `MAINT_TASK_UPDATE` (before/after).

`audit_log` is append-only in the database as well: UPDATE/DELETE triggers (SQLite + PostgreSQL, Alembic `0002` and the
POC `create_all` path) abort any change. In production also grant the app role `INSERT, SELECT` only on that table.

## 10. Security checklist for every PR
- [ ] New endpoint has `require_role` + authz test
- [ ] Inputs validated by Pydantic with ranges; no raw SQL strings
- [ ] No secrets/PII in logs or responses
- [ ] No new runtime CDN/external call
- [ ] Changes to compliance/optimization reviewed by `compliance-safety-reviewer`
- [ ] `pip-audit` / `pnpm audit` clean (or documented exception)
