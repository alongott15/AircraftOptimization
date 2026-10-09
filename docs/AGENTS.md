# Agents, Build Plan & Automation

How Claude Code should build this project: phase order, which subagent owns each piece, hand-offs, quality gates,
and the hooks to enable once the scaffold exists. Subagent definitions live in `.claude/agents/`.

## 1. Agent roster

| Agent | File | Owns | Tools |
|-------|------|------|-------|
| `data-engineer` | `.claude/agents/data-engineer.md` | ORM, Alembic, seed generator & scenarios, datasource adapters (SQLite, Vertica), repositories | read/write/bash |
| `backend-engineer` | `.claude/agents/backend-engineer.md` | FastAPI app, routers, DTOs, services, auth/RBAC, audit, config | read/write/bash |
| `optimization-ml-engineer` | `.claude/agents/optimization-ml-engineer.md` | `domain/compliance`, `domain/optimization` (CP-SAT), `ml/` | read/write/bash |
| `frontend-hebrew-engineer` | `.claude/agents/frontend-hebrew-engineer.md` | React app, design system, `he.ts`, pages, RTL | read/write/bash |
| `aircraft-3d-engineer` | `.claude/agents/aircraft-3d-engineer.md` | `features/aircraft3d/` (procedural models, hotspots, fallback) | read/write/bash |
| `test-engineer` | `.claude/agents/test-engineer.md` | golden scenarios, property tests, API authz matrix, Playwright E2E | read/write/bash |
| `compliance-safety-reviewer` | `.claude/agents/compliance-safety-reviewer.md` | **Read-only** review of eligibility/optimization changes against BR-xx and S1–S9 | read/grep/bash (tests only) |
| `security-reviewer` | `.claude/agents/security-reviewer.md` | **Read-only** review against SECURITY.md | read/grep/bash (audits only) |

Rules for the main session (orchestrator):
- Give each agent a **single, scoped task** with the doc sections it must follow and the acceptance criteria below.
- Run independent work in parallel (e.g. phase 3 frontend shell ‖ backend API), sequential where there is a contract
  dependency (API.md implemented before frontend hooks are wired to it).
- After any change under `backend/app/domain/**` → run `compliance-safety-reviewer`. After auth/API/config changes →
  `security-reviewer`. Fix all blocking findings before moving on.
- Never let two agents edit the same file concurrently.

## 2. Build phases

### Phase 0 — Scaffold (orchestrator)
- Monorepo layout per ARCHITECTURE.md §2; `Makefile` targets from CLAUDE.md; `backend/pyproject.toml` (uv, ruff, mypy,
  pytest, import-linter); `frontend` (Vite React TS, Mantine, ESLint, Stylelint, Prettier, Vitest, Playwright);
  `.env.example`; `.gitignore` (`data/`, `.env`); `docker-compose.yml`; `git init`.
- **Done when:** `make setup lint test` passes on empty skeletons; `/api/v1/health` returns ok; frontend renders an RTL Hebrew
  placeholder page.

### Phase 1 — Data layer (`data-engineer`)
- ORM for all tables in DATABASE.md §3–4, Alembic initial migration, reference data, seed generator with history (§6.3),
  scenarios SC-01…SC-16, `FleetDataSource` protocol + `PocSqliteSource` + `VerticaSource` skeleton + `vertica_views.sql`.
- **Done when:** `make seed` builds `data/poc.sqlite` deterministically; a test asserts every scenario row's exact values
  at a fixed anchor; snapshot load returns domain objects for all 24 aircraft.

### Phase 2 — Compliance engine & optimizer (`optimization-ml-engineer`)
- Domain models, `Clock`, consumption, evaluator (R1–R13), effective qualifications, unblock hints, fleet status,
  scoring, CP-SAT selector with alternatives, hangar term, shortfall.
- **Done when:** unit + property tests (TESTING.md §3.1–3.3) green, domain coverage ≥ 95 %, `compliance-safety-reviewer` approves.

### Phase 3 — API (`backend-engineer`) ‖ Frontend shell (`frontend-hebrew-engineer`)
- Backend: all endpoints in API.md, problem+json, auth (local mode), RBAC, audit, rate limits, OpenAPI.
- Frontend: theme/tokens, `he.ts`, `format.ts`, AppShell RTL, login, generated API types, components from DESIGN_SYSTEM §3.
- **Done when:** API tests incl. authz matrix green; golden scenarios (TESTING.md §4) green through the API;
  `security-reviewer` approves; frontend components unit-tested.

### Phase 4 — Pages (`frontend-hebrew-engineer`)
- Dashboard, fleet, aircraft page (3D slot placeholder), mission planner with results, missions list, settings, audit.
- **Done when:** E2E flows 1, 2, 5, 6 (TESTING.md §5.2) work against the seeded backend; i18n contract test green;
  no Latin text outside `<bdi>`.

### Phase 5 — 3D viewer (`aircraft-3d-engineer`)
- Procedural models for 3 shapes, zones, hotspots, tail number decal, status tint & expired pulse, toolbar, Fallback2D.
- **Done when:** E2E steps 3–4 pass; < 30k triangles; lazy-loaded chunk; reduced-motion respected.

### Phase 6 — ML (`optimization-ml-engineer`)
- Feature builder, defect-risk model, utilization forecast, duration-buffer model, registry with SHA-256, `make train`,
  `/ml/*` endpoints wiring, risk in aircraft page and scoring.
- **Done when:** ML tests (TESTING.md §3.4) green; app works with models deleted (fallback).

### Phase 7 — Hardening & demo (`test-engineer` + reviewers)
- Full E2E suite, axe, visual snapshots, perf test, Docker Compose offline run, PRD §11 demo rehearsal with `APP_FROZEN_NOW`.
- **Done when:** `make lint test e2e` green; both reviewers report no blocking findings; demo script runs end-to-end.

## 3. Hand-off template (orchestrator → agent)
```
Task: <one sentence>
Read first: <doc §refs>
Files you own: <paths>          Do not touch: <paths>
Acceptance: <bullet list from phase "Done when" + specific tests>
Report back: files changed, tests run (command + result), open questions.
```

## 4. Reviewer output format
Both reviewers return findings as: `severity (BLOCKING|MAJOR|MINOR) · file:line · rule (e.g. S1, BR-04, SEC §6) · problem · fix`.
"No findings" must be stated explicitly with the list of files reviewed.

## 5. Claude Code automation (enable after Phase 0)

Add to `.claude/settings.json` once the toolchain exists (hooks that call missing tools would fail before that).
Hooks receive the tool call as JSON on stdin; read it once into a variable.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "f=$(jq -r '.tool_input.file_path // empty'); case \"$f\" in *.env.example) exit 0 ;; *.env|*.env.*) echo 'Editing .env files is blocked; edit .env.example instead' >&2; exit 2 ;; esac; exit 0"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "f=$(jq -r '.tool_input.file_path // empty'); case \"$f\" in *.py) cd \"$CLAUDE_PROJECT_DIR/backend\" && uv run ruff format \"$f\" && uv run ruff check --fix \"$f\" ;; *.ts|*.tsx) cd \"$CLAUDE_PROJECT_DIR/frontend\" && pnpm exec prettier --write \"$f\" && pnpm exec eslint --fix \"$f\" ;; esac; exit 0"
          }
        ]
      }
    ]
  }
}
```

Recommended additions:
- **MCP:** `context7` for up-to-date docs of FastAPI, OR-Tools, Mantine, react-three-fiber; Playwright MCP for visual
  checks of the RTL UI and 3D page during development.
- **Permissions allow-list** (`.claude/settings.json` → `permissions.allow`): `Bash(make *)`, `Bash(uv run *)`,
  `Bash(pnpm *)`, `Bash(pytest *)`.
- **Golden-suite gate:** a CI job (or `Stop` hook) that runs `pytest tests/scenarios -q` whenever files under
  `backend/app/domain/` changed.
