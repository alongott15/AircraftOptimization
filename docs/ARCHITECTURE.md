# Architecture

## 1. Overview

```
┌──────────────────────────── Browser (Hebrew, RTL) ─────────────────────────────┐
│ React + TS + Vite · Mantine (RTL) · TanStack Query · react-three-fiber (3D)    │
│ Pages: לוח בקרה · צי המטוסים · מטוס (3D) · תכנון משימה · משימות · הגדרות         │
└───────────────────────────────▲────────────────────────────────────────────────┘
                                │ HTTPS · JSON · /api/v1 (OpenAPI → generated TS types)
┌───────────────────────────────┴────────────────────────────────────────────────┐
│ FastAPI backend (Python 3.12)                                                   │
│  api/v1 routers → services → domain (pure) ← ml (scoring inputs)                │
│                        │                                                         │
│                 repositories (SQLAlchemy 2.x)                                    │
│          ┌─────────────┴──────────────┐                                         │
│   FleetDataSource (read)        AppStore (read/write)                            │
│   ├─ PocSqliteSource            ├─ SQLite (POC, same file)                       │
│   └─ VerticaSource (read-only)  └─ PostgreSQL (production)                       │
└──────────────────────────────────────────────────────────────────────────────────┘
        ▲                                   ▲
        │ canonical views (ao_v_*)          │ Alembic migrations
  Vertica data lake (MRO, flight logs)   App DB: missions, runs, assignments, policy, audit, ML registry
```

### Key decisions (ADR summary)
| # | Decision | Why |
|---|----------|-----|
| ADR-1 | Monorepo: `backend/` + `frontend/` | Single PR for API + UI changes; shared reason-code contract |
| ADR-2 | Python + FastAPI | Best ML/OR ecosystem (scikit-learn, OR-Tools); OpenAPI generation for typed client |
| ADR-3 | Compliance logic in Python domain layer, not SQL | Identical behaviour on SQLite/Vertica; unit/property-testable; explainable |
| ADR-4 | Vertica read-only; separate app store | Vertica is columnar/analytic and poor at small OLTP writes; least privilege on the lake |
| ADR-5 | SQLite for POC (one file, zero setup) | Demo anywhere; SQLAlchemy keeps Postgres/Vertica swap cheap |
| ADR-6 | OR-Tools CP-SAT for selection | Handles set constraints (same type, hangar capacity, forced tails) and alternatives cleanly |
| ADR-7 | Backend returns codes, frontend renders Hebrew | Language-neutral API, all copy reviewed in one file |
| ADR-8 | Mantine UI with `DirectionProvider` | First-class RTL, date pickers with `dayjs/locale/he`, accessible components |
| ADR-9 | Procedural 3D models (three.js primitives) | No licensing/asset dependency; engine count & zones are data-driven; glTF optional later |
| ADR-10 | Injectable `Clock` | Deterministic tests & demos (`APP_FROZEN_NOW`) |

## 2. Repository layout

```
backend/
  pyproject.toml                # uv-managed; ruff, mypy, pytest config
  app/
    main.py                     # FastAPI app factory, middleware, routers
    config.py                   # pydantic-settings (env: APP_*)
    clock.py                    # Clock protocol, SystemClock, FrozenClock
    api/v1/
      aircraft.py fleet.py missions.py optimization.py reference.py ml.py settings.py audit.py auth.py health.py
      (maintenance board / tasks / recommendations — API.md §3.8)
    schemas/                    # Pydantic DTOs (request/response) — API.md is the contract
    domain/
      models.py                 # frozen dataclasses: Aircraft, Limit, Permit, MaintenanceAction, Mission, ...
      reason_codes.py           # ReasonCode / WarningCode / UnblockCode enums (mirrored in frontend)
      compliance/
        consumption.py          # mission consumption per unit (OPT §2.1)
        evaluator.py            # evaluate_eligibility(), fleet_status() (OPT §2)
        qualifications.py       # effective qualifications (OPT §2.6)
        unblock.py              # unblock hints (OPT §2.7)
      optimization/
        scoring.py              # score terms & presets (OPT §3)
        cpsat_selector.py       # CP-SAT model + alternatives (OPT §5)
        horizon.py              # multi-mission planner + independent plan checker (FR-46)
        result.py
      maintenance.py            # tasks from fleet status, due cycles, verified recommendations (OPT §2.9, FR-90…92)
    ml/
      features.py risk_model.py utilization.py duration_buffer.py registry.py train.py
    services/                   # orchestration: FleetService, MissionService, OptimizationService, AuditService
    repositories/               # SQLAlchemy queries → domain models (no business logic)
    datasources/
      base.py                   # FleetDataSource protocol
      poc_sqlite.py             # reads example DB
      sql_views.py              # generic reader of canonical views ao_v_* (contract-tested on SQLite)
      vertica.py                # vertica-python runner (read-only session) → sql_views (uv sync --extra vertica)
    db/
      orm.py                    # SQLAlchemy models (source of truth for schema)
      session.py
      migrations/               # Alembic — app-store tables only (AppBase); make migrate / make migration
    seed/
      generator.py              # synthetic history + current state
      scenarios.py              # demo scenarios SC-01..SC-15 (DATABASE.md §6)
    security/
      auth.py rbac.py ratelimit.py oidc.py
  tests/                        # see TESTING.md
frontend/
  package.json vite.config.ts tsconfig.json
  public/fonts/                 # self-hosted Heebo (no CDN)
  src/
    main.tsx                    # DirectionProvider(rtl), MantineProvider(theme), QueryClient, Router
    app/routes.tsx
    theme/tokens.ts theme.ts    # DESIGN_SYSTEM.md tokens
    i18n/he.ts                  # ALL Hebrew strings
    i18n/reasonCodes.ts         # code → Hebrew template renderer
    i18n/format.ts              # dates (Asia/Jerusalem), numbers, units
    api/client.ts api/schema.d.ts (generated by openapi-typescript)
    components/                 # design-system components (StatusBadge, AlertBanner, ...)
    pages/ Dashboard/ Fleet/ Aircraft/ MissionPlanner/ Missions/ Settings/ Login/
    features/aircraft3d/        # AircraftModel.tsx, Hotspot.tsx, zones.ts, TailDecal.tsx, Fallback2D.tsx
  e2e/                          # Playwright
data/                           # poc.sqlite, models/ (git-ignored)
docker-compose.yml              # backend + frontend (nginx) [+ postgres profile]
Makefile
```

### Layering rules
- `api` → `services` → `domain` / `repositories`. `domain` imports nothing from `api`, `services`, `repositories`,
  `db`, or `ml` (it receives ML outputs as plain values).
- `repositories` contain queries only; they return domain dataclasses.
- `datasources` hide Vertica vs SQLite; services never know which one is active.
- Enforce with `import-linter` contracts (CODE_STYLE.md §2.4).

## 3. Request flows

### 3.1 Optimize a mission
```
UI MissionPlanner ──POST /api/v1/missions──► MissionService.create (validate, persist draft)
UI ──POST /api/v1/missions/{id}/optimize──► OptimizationService.run
     1. FleetDataSource.load_snapshot(now)              (aircraft, counters, limits, permits, MAs, quals)
     2. AppStore: assignments, policy, hangar capacity
     3. domain.compliance.evaluate_eligibility × N      (pure)
     4. ml.score_inputs (p_defect, utilization → weeks) (fallback neutral)
     5. domain.optimization.scoring + cpsat_selector    (pure)
     6. persist optimization_run + candidates; audit
◄── OptimizationRunDTO (recommended set, alternatives, eligible, excluded with codes)
UI ──POST /api/v1/missions/{id}/assign {run_id, set_index}──► assignment + audit (re-validates eligibility at accept time)
```
Re-validation on accept is mandatory: data may have changed between run and accept (stale-run protection; runs
older than 30 min must be recomputed).

### 3.2 Aircraft page
`GET /api/v1/aircraft/{tail}` returns aircraft + type geometry hints (engine count, zone list) + compliance items
with status + open MAs with zone + permits + qualifications + counters. The 3D viewer is purely presentational.

## 4. Data sources

### 4.1 POC
Single SQLite file `data/poc.sqlite` contains both the "lake" tables (DATABASE.md §3) and app-store tables (§4).
`APP_DATA_SOURCE=poc`.

### 4.2 Production
- `APP_DATA_SOURCE=vertica`: `VerticaSource` reads **canonical views** `ao_v_aircraft`, `ao_v_usage_counter`,
  `ao_v_inspection_due`, `ao_v_permit`, `ao_v_maintenance_action`, `ao_v_aircraft_qualification`, `ao_v_flight_log`
  (DATABASE.md §7). Views are owned by the data team and map lake tables to the canonical schema.
- App store on PostgreSQL (`APP_STORE_URL`), migrated by Alembic.
- Snapshot caching: fleet snapshot cached in-process for `APP_SNAPSHOT_TTL_SECONDS` (default 60); `source_updated_at`
  propagated for staleness checks.
- Connection: `vertica-python` with TLS, read-only service account, connection pool, statement timeout 30 s.

## 5. Frontend architecture
- **State:** server state via TanStack Query (keys per resource; `staleTime` 30 s; refetch on focus). UI state local
  or URL search params (filters are shareable links). No global store needed for POC.
- **Routing:** `/` dashboard · `/fleet` · `/aircraft/:tail` · `/missions/new` · `/missions/:id` · `/missions` ·
  `/settings` · `/audit` · `/login`.
- **3D:** `features/aircraft3d` lazy-loaded (`React.lazy`) so the dashboard bundle stays small. Canvas uses
  `frameloop="demand"`. Hotspot labels via drei `<Html>` (real DOM ⇒ correct Hebrew bidi). Tail number via drei
  `<Text>` (Latin only). WebGL detection → `Fallback2D` SVG.
- **Typed API:** `pnpm gen:api` runs `openapi-typescript` against `/openapi.json` → `src/api/schema.d.ts`.
- **i18n:** `he.ts` is a typed object; `t('fleet.title')` helper with type-safe keys. Reason codes rendered via
  `renderReason(code, params)`; a unit test asserts every backend code has a Hebrew template.

## 6. Tech stack (pin exact versions in lockfiles at scaffold time)

| Layer | Choice |
|-------|--------|
| Backend runtime | Python 3.12, `uv` |
| API | FastAPI, Pydantic v2, uvicorn |
| DB | SQLAlchemy 2.x, Alembic, SQLite (POC), PostgreSQL 16 (prod app store), `vertica-python` |
| Optimization | `ortools` (CP-SAT) |
| ML | scikit-learn, numpy, pandas, joblib, (optional) shap |
| Auth | `authlib` (OIDC in prod), `pyjwt`, argon2 password hashing (POC users) |
| Frontend | React 18+, TypeScript (strict), Vite, Mantine 7+ (`@mantine/core`, `dates`, `notifications`, `charts`), `@tabler/icons-react`, TanStack Query, React Router, dayjs (`he` locale), `@fontsource/heebo` |
| 3D | three, @react-three/fiber, @react-three/drei |
| Testing | pytest, hypothesis, pytest-cov, httpx; Vitest, React Testing Library, Playwright, @axe-core/playwright |
| Quality | ruff, mypy (strict), import-linter, ESLint, Prettier, tsc |
| Packaging | Docker Compose; frontend served by nginx; backend uvicorn workers |

## 7. Configuration (env, `APP_` prefix)
| Var | Default | Notes |
|-----|---------|-------|
| `APP_ENV` | `dev` | `dev` / `test` / `prod` |
| `APP_DATA_SOURCE` | `poc` | `poc` / `vertica` |
| `APP_SQLITE_PATH` | `data/poc.sqlite` | |
| `APP_STORE_URL` | sqlite path | Postgres URL in prod |
| `APP_VERTICA_DSN` | — | via secret store, never committed |
| `APP_FROZEN_NOW` | — | ISO-8601 UTC; enables FrozenClock |
| `APP_AUTH_MODE` | `local` | `local` / `oidc` |
| `APP_CORS_ORIGINS` | `http://localhost:5173` | |
| `APP_MODELS_DIR` | `data/models` | |
| `APP_LOG_LEVEL` | `INFO` | JSON logs |

## 8. Deployment (POC)
`docker compose up` → `frontend` (nginx serving built SPA, proxy `/api` → backend) and `backend` (uvicorn).
Seed + train run as a one-shot `init` service. Everything works offline (no CDN, fonts bundled).

## 9. Observability
- JSON logs with `request_id`, `user`, `route`, `duration_ms`; optimization run event (OPT §6).
- `/api/v1/health` (liveness), `/api/v1/ready` (DB + data-source reachable + models loaded status).
- Optional Prometheus `/metrics` (request latency, optimization latency, eligible counts).
