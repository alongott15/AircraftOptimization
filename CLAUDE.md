# AircraftOptimization — Fleet Usability Optimizer (POC)

Decision-support web app for an airline / flight operator. Given a mission request (how many aircraft, which
qualifications, estimated flight time, landings, touch-and-goes, start time …) it recommends the best set of
aircraft, taking into account inspections, permits, open maintenance actions, aircraft qualifications, availability,
items that would expire **during** the flight, and activity-unit consumption (flight hours, landings, touch-and-go,
cycles, engine starts, calendar days, days without flight).

- **UI language: Hebrew only, RTL.** No English strings visible to end users (tail numbers / ATA codes excepted).
- **Production data source: Vertica data lake (read-only).** POC runs on a generated SQLite example DB.
- **3D aircraft viewer** shows tail number, open maintenance actions, permits, upcoming inspections, expired items.
- **ML/AI**: defect-risk model, utilization forecast, flight-duration buffer model, constraint optimizer (OR-Tools CP-SAT).

## Read before you work

| Doc | Read when |
|-----|-----------|
| [docs/PRD.md](docs/PRD.md) | Always first — scope, requirements (FR-xx), business rules (BR-xx), demo script |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Any structural change, new module, new dependency |
| [docs/OPTIMIZATION_ENGINE.md](docs/OPTIMIZATION_ENGINE.md) | Compliance engine, scoring, CP-SAT model, ML models |
| [docs/DATABASE.md](docs/DATABASE.md) | Schema, seed data, demo scenarios, Vertica mapping |
| [docs/API.md](docs/API.md) | Any endpoint, DTO, reason code, error format |
| [docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) | Any UI work — tokens, RTL rules, components, Hebrew copy, 3D viewer |
| [docs/SECURITY.md](docs/SECURITY.md) | Auth, RBAC, secrets, audit, safety principles |
| [docs/CODE_STYLE.md](docs/CODE_STYLE.md) | Before writing code |
| [docs/TESTING.md](docs/TESTING.md) | Before writing/finishing any feature — golden scenarios live here |
| [docs/AGENTS.md](docs/AGENTS.md) | Build plan, phase order, which subagent owns what, hooks |

## Golden rules (non-negotiable)

1. **Fail-safe compliance.** Missing, stale or unparseable data ⇒ aircraft is **not eligible** (`DATA_MISSING`).
   Never default an unknown to "OK".
2. **ML never overrides hard constraints.** ML outputs only influence *ranking* among already-eligible aircraft.
   Eligibility is decided solely by the deterministic compliance engine (`backend/app/domain/compliance`).
3. **Expiring "during the flight" counts.** Every inspection, permit, deferral and qualification is projected to
   *mission end + safety buffer*, using projected activity-unit consumption (incl. touch-and-go weighting).
4. **Backend is language-neutral.** The API returns reason/warning **codes + params**; the frontend renders Hebrew
   from `frontend/src/i18n/he.ts`. The backend never generates UI copy (data fields such as `name_he` or MA titles
   coming from the DB are fine). Never hard-code Hebrew in components — always via `he.ts`.
5. **RTL everywhere.** `<html lang="he" dir="rtl">`, CSS logical properties only (`margin-inline-start`, never
   `margin-left`). Wrap Latin/numeric fragments (tail numbers, ATA codes) in `<bdi>`.
6. **Expired items are always highlighted** (red + icon + text) on dashboard, fleet list, aircraft page and 3D model.
7. **Vertica is read-only.** App-owned writes (missions, runs, assignments, audit) go to the app store
   (SQLite in POC, PostgreSQL in production). See ARCHITECTURE §4.
8. **Injectable clock.** Never call `datetime.now()` directly in domain code — use `Clock` (`APP_FROZEN_NOW` for demos/tests).
9. All timestamps stored in UTC; displayed in `Asia/Jerusalem`, format `dd/MM/yyyy HH:mm`.
10. Decision support only: every recommendation screen shows the disclaimer that final release-to-service is by
    authorized personnel.

## Repository layout (target)

```
backend/            FastAPI app, domain logic, ML, seed generator (Python 3.12)
frontend/           React + TypeScript + Vite, Mantine (RTL), react-three-fiber
data/               Generated poc.sqlite + ML artifacts (git-ignored)
docs/               Specs (this folder is the source of truth for requirements)
.claude/agents/     Subagent definitions used during the build
```

## Commands

```
make setup      # .env with generated secrets (scripts/init_env.py), backend (uv) + frontend (pnpm) deps, Playwright
make demo       # = make seed train: data/poc.sqlite anchored to now + trained models (re-run before a demo)
make migrate    # apply Alembic migrations to the app store (existing data/poc.sqlite after a pull; fleet-table
                #   changes such as the FR-35 squadron table need `make demo` — it recreates the demo DB)
make user       # create a personal local login (prompts; default role ADMIN) — re-run after make demo
make password   # set a new password for a local login
make dev        # backend :8000 + frontend :5173 (Vite proxies /api)
make test       # backend pytest (golden, property, API, ML) + frontend vitest
make e2e        # Playwright: own backend :8001 on frozen clock + golden seed, Vite :5174
make lint       # ruff + mypy --strict + import-linter + eslint + tsc
make gen-api    # regenerate frontend/src/api/schema.d.ts after DTO changes
docker compose up --build   # offline POC on 127.0.0.1:8080 (needs .env from make setup; verified 2026-10-09)
```
Demo users: `admin`, `planner`, `maint`, `ops` — password = `APP_DEMO_PASSWORD` in `.env`.

## Implementation status (2026-10-10)

Done: compliance engine (R1–R13 + later-assignment protection, fail-safe data rules OPT §2.4a, commitments counted
since the counter snapshot with per-flight buffers, unblock hints), scoring + CP-SAT selector with alternatives &
hangar term, multi-mission horizon planner (FR-46), ML (defect risk AUC≈0.72 with fighters, utilization EWMA, duration-buffer P90)
with SHA-256 registry, seed generator with SC-01…SC-17 (34 aircraft incl. F-16I/F-15I fighter squadrons at EST), FastAPI API (local + OIDC SSO keyed by issuer|subject, CSRF,
RBAC, jti revocation, append-only audit, problem+json), Alembic migrations for the app store (`0001`–`0004`),
Vertica adapter over `ao_v_*` views (defensive parsing, verified TLS in prod, contract-tested on SQLite), Hebrew RTL
React app (dashboard, fleet + 3D apron, aircraft + realistic procedural 3D models — trainer, turboprop, business jet,
F-16-/F-15-like fighters — + 2D fallback, mission planner/edit/draft/results/assign,
horizon planning, missions, settings incl. editable score weights / tolerance policy / touch-and-go weights,
audit; dashboard alerts for assigned missions that lost eligibility; 14/30/90-day timeline; expiration bands
"תוך … ועד …" per activity unit — mission ranking preference FR-34 replacing the buffer fields, fleet filters FR-14; mission scope by
squadron and aircraft type FR-35), `make user` CLI, CI
workflow, Docker compose verified. Security review and two compliance-safety review passes (2026-10-09) addressed.
Tests: 416 backend (+1 perf, +1 real-Vertica skip), 31 vitest, 13 Playwright E2E. PRD §6 FR-01…FR-82 audited
2026-10-09: all implemented.

Not yet: native-speaker review of `he.ts` (self-reviewed only); integration against a real Vertica and a real IdP
(both covered only by contract/mocked tests); production deployment (TLS termination, PostgreSQL app store, secrets
store). CI runs on GitHub (private repo alongott15/AircraftOptimization) on every push to `main`.

## Workflow

- Work phase by phase per [docs/AGENTS.md](docs/AGENTS.md); delegate to the matching subagent in `.claude/agents/`.
- Every change touching eligibility logic must keep the golden scenario suite green (docs/TESTING.md §4).
- Run `compliance-safety-reviewer` after any change in `domain/compliance` or `domain/optimization`.
- If a requirement is ambiguous, check PRD §12 "Open questions" — do not invent aviation rules; make them configurable.
