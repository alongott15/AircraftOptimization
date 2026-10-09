# Code Style & Conventions

Goal: code that a reviewer can verify for safety quickly. Prefer explicit, typed, small, pure functions.

## 1. General
- English for all identifiers, comments, commit messages, logs. Hebrew only in `frontend/src/i18n/he.ts` and seed data.
- Domain vocabulary is fixed — use these names everywhere (DB, Python, TS, API):

| Concept | Name |
|---------|------|
| מספר זנב | `tail_number` / `tailNumber` |
| בדיקה | `inspection` (type: `inspection_type`, limit: `inspection_limit`) |
| היתר | `permit` |
| פעולת תחזוקה | `maintenance_action` (abbr. `ma` only in local variables) |
| דחייה | `deferral` |
| כשירות | `qualification` |
| יחידת פעילות | `activity_unit` / `unit` |
| גע-וסע | `touch_and_go` (`tg` only in `tg_weight`) |
| משימה | `mission` |
| כשיר למשימה | `eligible` / `EligibilityResult` |
| סיבת פסילה | `reason` (`ReasonCode`) |

- Units are explicit in names when ambiguous: `duration_hours`, `buffer_pct`, `idle_days`, `window_end`.
- No magic numbers in domain code — every threshold comes from `PlanningPolicy` or reference data.
- Comments explain *why* (rule source, aviation reasoning), not *what*. Reference rule IDs: `# BR-03, OPT §2.1`.
- Max function length ~40 lines; max file ~400 lines; split by responsibility.
- Commits: Conventional Commits (`feat(compliance): project LDG limits with tg_weight`). One logical change per commit.

## 2. Python (backend)

### 2.1 Tooling
- Python 3.12, managed with `uv`. `ruff format` (line length 110) + `ruff check` with rule sets
  `E,F,W,I,N,UP,B,SIM,C4,RUF,S,PT,DTZ,TID,ARG,PL` (`DTZ` forbids naive datetimes).
- `mypy --strict` on `app/` (tests: `--strict` minus `disallow-untyped-defs`).
- `import-linter` contracts (see §2.4).

### 2.2 Conventions
- Type hints everywhere; `from __future__ import annotations`.
- Domain models: `@dataclass(frozen=True, slots=True)`; collections as `tuple[...]` / `Mapping` — immutable.
- Enums: `class ReasonCode(StrEnum)`.
- Counters are `float` in the domain (rounded to 2 decimals at the API boundary). Compare only via the epsilon helpers
  in `domain/numeric.py` (`lt`, `le`, `ge`, eps = 1e-6) — never `==` on floats.
- Datetimes: always timezone-aware UTC (`datetime.now(tz=UTC)` only inside `SystemClock`). Domain functions receive `now`.
- Errors: domain raises exceptions only for programmer errors; data problems become `Reason(DATA_MISSING)` —
  never an exception that aborts the whole fleet evaluation.
- No business logic in routers or repositories. Routers: parse → call service → map to DTO.
- Pydantic v2 for DTOs (`model_config = ConfigDict(extra="forbid", frozen=True)`), separate from domain dataclasses.
- Dependency injection via FastAPI `Depends` for services, clock, current user.
- Logging: `structlog` JSON; `log.info("optimization.completed", run_id=..., n_eligible=...)` — event names dotted.
- Iteration order: sort by `aircraft_id` / codes before any loop that affects output (determinism).

### 2.3 Example
```python
def evaluate_usage_limit(
    limit: UsageLimit, counter: Counter | None, consumption: float, committed: float, policy: PlanningPolicy
) -> LimitEvaluation:
    """OPT §2.3 — projection of a counter-based limit to mission end (BR-02)."""
    if counter is None:
        return LimitEvaluation.data_missing(limit)          # S1 fail-safe
    projected = counter.value + committed + consumption
    remaining_now = limit.due_value - counter.value
    remaining_after = limit.due_value - projected
    if lt(remaining_now, 0):
        return LimitEvaluation.expired(limit, over_by=-remaining_now)
    if lt(remaining_after, 0):
        return LimitEvaluation.due_before_mission_end(limit, remaining=remaining_now, required=consumption + committed)
    return LimitEvaluation.ok(limit, remaining_now=remaining_now, remaining_after=remaining_after)
```

### 2.4 Import contracts (`.importlinter`)
- `app.domain` must not import `app.api`, `app.services`, `app.repositories`, `app.db`, `app.datasources`, `app.ml`,
  `fastapi`, `sqlalchemy`.
- `app.domain.compliance` must not import `app.domain.optimization` (eligibility is upstream of scoring).
- `app.api` must not import `app.repositories` or `app.db` directly.

## 3. TypeScript / React (frontend)

### 3.1 Tooling
- TypeScript `strict: true`, `noUncheckedIndexedAccess: true`, `exactOptionalPropertyTypes: true`.
- ESLint (typescript-eslint strict, react, react-hooks, jsx-a11y, import) + Prettier (print width 110, single quotes).
- Stylelint with `stylelint-use-logical` (bans physical `left/right/margin-left/...`).
- pnpm; Node LTS.

### 3.2 Conventions
- Function components + hooks; no class components. Named exports; one component per file; file = component name.
- Server state only via TanStack Query hooks in `src/api/hooks/*.ts` (`useAircraft(tail)`, `useOptimizeMission()`).
  Components never call `fetch` directly.
- API types come from generated `schema.d.ts`; do not hand-write DTO types.
- Props typed with `type Props = {...}`; avoid `any` (lint error) and non-null `!`.
- Styling: Mantine props/theme + CSS modules for custom pieces; tokens only from `theme/tokens.ts` — no raw hex in components.
- Dates only through `i18n/format.ts` (`formatDateTime`, `formatRelative`, `formatHours`) — never `toLocaleString` ad hoc.
- 3D code isolated in `features/aircraft3d/`; no three.js imports elsewhere.
- Accessibility: every interactive element has a Hebrew accessible name; icon-only buttons need `aria-label` from `he.ts`.

### 3.3 Hebrew & RTL lint rules
- Custom ESLint rule `no-hebrew-literals`: Hebrew characters (`/[֐-׿]/`) allowed only under `src/i18n/`.
- `react/jsx-no-literals` (allow numbers/punctuation) so all copy goes through `t()`.
- `dangerouslySetInnerHTML` banned.
- Tail numbers, MA numbers, codes rendered through `<Ltr>`.

### 3.4 Example
```tsx
export function StatusBadge({ status, count }: Props) {
  const { t } = useI18n();
  const meta = STATUS_META[status];                       // color + icon from tokens
  return (
    <Badge color={meta.color} leftSection={<meta.Icon size={14} aria-hidden />} variant="light">
      {t(`status.${status}`)}
      {count !== undefined && <Ltr> ({count})</Ltr>}
    </Badge>
  );
}
```
(Mantine's `leftSection` renders at the inline-start under `DirectionProvider rtl`, i.e. on the right.)

## 4. SQL / data
- Snake_case table and column names; singular table names; `*_at` timestamps (UTC), `*_code` for reference keys.
- Never build SQL with f-strings; SQLAlchemy Core/ORM with bound params. Raw SQL only in `vertica_views.sql` contract.
- Every schema change in the app store = Alembic migration + ORM update + DATABASE.md update in the same PR.

## 5. Tests style
- pytest: `test_<unit>_<behaviour>()`; Arrange-Act-Assert; builders in `tests/builders.py` (`an_aircraft().with_inspection(...)`).
- No sleeping, no real clock — use `FrozenClock`.
- Frontend: test user-visible behaviour in Hebrew (`getByRole('button', { name: 'חשב המלצה' })`).

## 6. Definition of Done (every task)
- [ ] Lint, types, tests green (`make lint test`)
- [ ] New/changed behaviour covered by tests; golden scenarios still green
- [ ] Docs updated if contract/schema/rules changed (API.md, DATABASE.md, OPTIMIZATION_ENGINE.md)
- [ ] All new UI text in `he.ts`; RTL verified visually
- [ ] No TODOs without an issue reference
