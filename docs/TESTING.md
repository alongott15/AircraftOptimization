# Testing Strategy

The compliance engine is safety-relevant: it gets the heaviest testing (unit + property-based + golden scenarios).
Everything runs offline and deterministically (`FrozenClock`, fixed seeds, single-worker solver).

## 1. Pyramid & targets

| Layer | Tooling | Scope | Coverage target |
|-------|---------|-------|-----------------|
| Domain unit | pytest | `domain/compliance`, `domain/optimization` | ≥ 95 % lines **and** branches |
| Property-based | hypothesis | safety invariants (§3.2) | all invariants |
| Golden scenarios | pytest + seeded DB | SC-01…SC-16 × M-A/M-B/M-C (§4) | 100 % of table rows |
| ML | pytest | features, leakage, metrics, fallback | — |
| API | pytest + httpx `AsyncClient` | contract, validation, authz matrix, errors | every endpoint |
| Backend overall | pytest-cov | | ≥ 85 % |
| Frontend unit | Vitest + React Testing Library | components, i18n, formatting | ≥ 75 % |
| E2E | Playwright (+ axe) | demo flows, RTL, a11y, 3D smoke | demo script (PRD §11) |
| Performance | pytest marker `perf` | 200-aircraft optimization < 2 s | manual / nightly |

Commands: `make test` (backend + frontend unit), `make e2e`, `pytest -m perf`.

## 2. Test data & determinism
- Test anchor: `T0 = 2026-01-15T08:00:00Z`. Fixture `seeded_db` runs the generator with `--anchor T0 --seed 42` into a temp
  SQLite file (session-scoped) and sets `FrozenClock(T0)`.
- Unit tests use builders (`tests/builders.py`): `an_aircraft().of_type("TP-200").with_counter("FH", 1000)
  .with_inspection_due("I100H", "FH", due_value=1001.5).build()`.
- Solver: `num_search_workers=1`, `random_seed=42` in tests.
- ML in golden optimization tests uses a **stub risk provider** with fixed probabilities (e.g. `4X-S02 = 0.45`, others `0.05`)
  so rankings are deterministic regardless of training.

## 3. Backend tests

### 3.1 Compliance unit tests (`tests/unit/compliance/`)
Must include, at minimum:
- Consumption per unit (OPT §2.1): FH buffer pct vs floor (`D=1 → 1.3`, `D=4 → 4.6`), LDG with `tg_weight` 1.0 / 0.5 / 0,
  CYC, ENG_START × engine count.
- Usage limit: remaining_after `> 0` OK, `== 0` OK (boundary), `< 0` due-before-end, remaining_now `< 0` expired.
- Calendar: `due_at == deadline` OK; `due_at` 1 s before deadline ⇒ expires-before-end; `due_at <= now` ⇒ expired.
- "Expires during flight": item valid at start, invalid at `start + D` ⇒ ineligible.
- Committed consumption from earlier assignments is added; ML forecast is not.
- Effective qualifications: revoked by MA, revoked by permit restriction, expired, expires before mission end, missing;
  most-specific reason chosen.
- Idle rule with `EXCLUDE` vs `WARN` policy; missing `last_flight_at` ⇒ `DATA_MISSING`.
- Data staleness ⇒ `DATA_STALE`.
- Tolerance ⇒ warning only, never eligible (S6).
- All reasons collected (aircraft with 3 failures returns 3 reasons).
- Unblock hints numeric correctness: `UNBLOCK_MAX_DURATION` for remaining 1.5 FH ⇒ 1.2; for remaining 4.4 ⇒ 3.82
  (floor to 2 decimals); `UNBLOCK_LATEST_END` only emitted when `latest_end > start_at`.

### 3.2 Property-based invariants (`tests/property/`) — hypothesis
| ID | Invariant |
|----|-----------|
| P1 | **Monotonic consumption:** increasing duration, landings or touch-and-goes never turns an ineligible aircraft eligible |
| P2 | **Fail-safe:** removing any data row never turns an ineligible aircraft eligible, and makes an eligible one ineligible if the row was applicable |
| P3 | **ML independence:** for any risk probabilities, weights or preset, the eligible set is identical |
| P4 | **Soundness:** for every eligible aircraft, every usage limit has `projected ≤ due` and every calendar item `due_at ≥ deadline` |
| P5 | **Completeness:** reasons of the full evaluation = union of reasons from evaluating each rule alone |
| P6 | **Determinism:** shuffling input order yields identical results |
| P7 | **Selector:** chosen set ⊆ eligible, size = `min(N, |eligible|)`, includes all eligible force-includes, same-type respected, objective(set 0) ≥ objective(alternatives) |
| P8 | `tg_weight = 0` ⇒ touch-and-go count does not affect LDG/CYC limits |
| P9 | Time monotonicity: moving `now` forward never turns an expired item un-expired |

### 3.3 Optimization tests
- Scoring term formulas (OPT §3) with hand-computed examples; weights normalize; presets load.
- CP-SAT: hangar overflow penalty changes choice in a crafted case; alternatives are distinct (no-good cuts) and ≥ 85 % of best;
  shortfall path returns partial set + `nearest_to_eligible`; force-include of ineligible ⇒ `FORCE_INCLUDE_INELIGIBLE`.
- Performance (`perf` marker): 200 aircraft, N = 6, same-type on ⇒ < 2 s.

### 3.4 ML tests (`tests/ml/`)
- Feature builder uses only data with timestamp `< t` (leakage test: inject future rows, features unchanged).
- Time-based split (no shuffle) asserted.
- On seeded data: defect-risk ROC-AUC ≥ 0.70, Brier ≤ 0.20; 4X-S02 risk > fleet median.
- Utilization forecast: EWMA + shrinkage on a hand-made series; aircraft with no flights falls back to type mean.
- Registry: missing artifact ⇒ neutral fallback + `ML_MODEL_UNAVAILABLE`; SHA-256 mismatch ⇒ refuse to load.

### 3.5 API tests (`tests/api/`)
- Contract: response shapes match API.md examples (Pydantic models + snapshot of OpenAPI schema committed in
  `tests/api/openapi.snapshot.json`; a diff requires a deliberate update).
- Validation errors produce problem+json with field codes (`START_IN_PAST`, `VALUE_TOO_SMALL`, `TAIL_FORCED_BOTH_WAYS`).
- **Authorization matrix:** parametrized over every route × role per SECURITY.md §3.3 (401 anonymous, 403 wrong role).
- Assign flow: run → assign OK; advance `FrozenClock` 31 min ⇒ 409 `RUN_STALE`; mutate DB to expire a permit between run and
  assign ⇒ 409 `ELIGIBILITY_CHANGED`.
- Audit: each state-changing endpoint writes the expected audit action.
- Reason-code contract: set of `ReasonCode`/`WarningCode`/`UnblockCode` in backend equals the enum exported in OpenAPI.

## 4. Golden scenarios (seeded DB, `tests/scenarios/test_golden.py`)

Missions M-A, M-B, M-C from DATABASE.md §6.5; scenarios SC-01…SC-16 from §6.2. Assert eligibility, **exact reason codes
present** (other reasons may also be present unless stated), and key params.

### 4.1 Fleet status at T0 (no mission)
| Tail | compliance_status | Notes |
|------|-------------------|-------|
| 4X-S03, 4X-P05, 4X-S07, 4X-J02 | EXPIRED | dashboard `aircraft_with_expired_items == 4`; banner visible |
| 4X-P04 | UNKNOWN | |
| 4X-P02, 4X-S05, 4X-P01, 4X-P03, 4X-J06, 4X-P06, 4X-P08 | EXPIRING_SOON | |
| 4X-J04 | derived_status GROUNDED | `open_grounding_mas == 1` |
| 4X-J03 | operational_status IN_MAINTENANCE | |
| 4X-S01, 4X-S02, 4X-S04, 4X-S06, 4X-S08, 4X-P07, 4X-P09, 4X-P10, 4X-J01, 4X-J05 | OK | healthy defaults must not trip "expiring soon" thresholds |

### 4.2 M-A — אימון לילה (2 × SINGLE, NIGHT, 2.5 h, 1 LDG + 6 T&G, T0 + 2 h)
| Tail | Eligible | Must contain |
|------|----------|--------------|
| 4X-S01, 4X-S04, 4X-S06 | ✓ | — |
| 4X-S02 | ✓ | with stub risk 0.45: `HIGH_DEFECT_RISK` warning and not in recommended set |
| 4X-S03 | ✗ | `INSPECTION_EXPIRED {inspection_code: ANNUAL}` |
| 4X-S05 | ✗ | `INSPECTION_DUE_BEFORE_MISSION_END {inspection_code: LG_INSP, unit: LDG, remaining: 5, required: 8}`; hint `UNBLOCK_MAX_LANDINGS {max_landings_plus_tg: 4}` |
| 4X-S07 | ✗ | `IDLE_LIMIT_EXCEEDED {limit_days: 30}` |
| 4X-S08 | ✗ | `ALREADY_ASSIGNED` ("אימון בוקר — משימה קיימת") |
| all 4X-P*, 4X-J* | ✗ | `ENGINE_REQUIREMENT_NOT_MET` |

Recommended set: 2 of {S01, S04, S06}; `solver_status = OPTIMAL`; ≥ 1 alternative.

### 4.3 M-B — הובלת נוסעים (3 × MIN_2, PAX + IFR, 4.0 h, 2 LDG, T0 + 1 d)
| Tail | Eligible | Must contain |
|------|----------|--------------|
| 4X-P01 | ✓ | warning `LOW_MARGIN_AFTER_MISSION {ref: A_CHECK, unit: FH, remaining_after: 0.4}`; its `best_fit` term is the highest among eligible |
| 4X-P06 | ✓ | warning `EXPIRES_SOON_AFTER_MISSION {ref: ANNUAL}` |
| 4X-P07, 4X-P10, 4X-J01, 4X-J05 | ✓ | — |
| 4X-J02 | ✓ | (OVERWATER expired but not required for this mission) |
| 4X-P02 | ✗ | `INSPECTION_DUE_BEFORE_MISSION_END {inspection_code: I100H, unit: FH, remaining: 1.5, required: 4.6}`; hint `UNBLOCK_MAX_DURATION {max_hours: 1.2}` |
| 4X-P03 | ✗ | `INSPECTION_DUE_BEFORE_MISSION_END {inspection_code: A_CHECK, unit: FH, remaining: 4.4, required: 4.6}`; hint `UNBLOCK_MAX_DURATION {max_hours: 3.82}` |
| 4X-P04 | ✗ | `DATA_MISSING {item_kind: COUNTER, item_code: LDG}` |
| 4X-P05 | ✗ | `PERMIT_EXPIRED {permit_type_code: COA}` |
| 4X-P08 | ✗ | `QUALIFICATION_REVOKED_BY_MA {qualification_code: IFR, ma_number: MA-2026-00398}` |
| 4X-P09 | ✗ | `QUALIFICATION_REVOKED_BY_PERMIT {qualification_code: PAX, permit_type_code: SPECIAL_FLIGHT, restriction: NO_PAX}` |
| 4X-J03 | ✗ | `AIRCRAFT_NOT_OPERATIONAL {status: IN_MAINTENANCE}` |
| 4X-J04 | ✗ | `GROUNDING_MA_OPEN {ma_number: MA-2026-00412, zone_code: ENGINE_2}` |
| 4X-J06 | ✗ | `PERMIT_EXPIRES_BEFORE_MISSION_END {permit_type_code: INSURANCE}` |
| all 4X-S* | ✗ | `ENGINE_REQUIREMENT_NOT_MET` |

`n_eligible == 7`, recommended set size 3.

### 4.4 M-C — טיסת מעבר ארוכה (1 × MIN_2, OVERWATER, 7.0 h, T0 + 3 h, FERRY)
| Tail | Eligible | Must contain |
|------|----------|--------------|
| 4X-J01, 4X-J05 | ✓ | — |
| all 4X-P* | ✗ | `ENDURANCE_EXCEEDED {required_hours: 8.05, max_hours: 6.0}` |
| 4X-J02 | ✗ | `QUALIFICATION_EXPIRED {qualification_code: OVERWATER}` |
| 4X-J03 | ✗ | `AIRCRAFT_NOT_OPERATIONAL` |
| 4X-J04 | ✗ | `GROUNDING_MA_OPEN` |
| 4X-J06 | ✗ | `PERMIT_EXPIRES_BEFORE_MISSION_END {permit_type_code: INSURANCE}`; hint `UNBLOCK_RENEW_PERMIT`; **no** `UNBLOCK_LATEST_END` (latest end ≤ start) |
| all 4X-S* | ✗ | `ENGINE_REQUIREMENT_NOT_MET`, `ENDURANCE_EXCEEDED`, `QUALIFICATION_MISSING {qualification_code: OVERWATER}` |

### 4.5 Snapshot
Full `OptimizationRun` JSON for M-A/M-B/M-C (stub risk) is snapshotted in `tests/scenarios/snapshots/`. Any diff must be
reviewed by `compliance-safety-reviewer` before updating the snapshot.

## 5. Frontend tests

### 5.1 Unit (Vitest + RTL)
- **i18n contract:** every `ReasonCode | WarningCode | UnblockCode` in the generated OpenAPI types has a template in
  `reasonCodes.ts`; every `t()` key used exists in `he.ts` (type-level + runtime test).
- `renderReason` substitutes params and resolves names from reference data (e.g. `LG_INSP` → "בדיקת כני נסע", `LDG` → "נח׳").
- `format.ts`: `Asia/Jerusalem` conversion incl. DST boundary, `DD/MM/YYYY HH:mm`, hours `2:30`, relative times in Hebrew.
- `StatusBadge` renders icon + Hebrew text for each status; `FleetTable` default sort puts EXPIRED first;
  `AlertBanner` hidden when count = 0; `MissionForm` shows Hebrew validation messages and accepts `2:30` and `2.5`;
  `ExcludedList` shows all reasons + hints; `Ltr` wraps tail numbers in `<bdi dir="ltr">`.
- Token contrast test: every status fg/bg pair ≥ 4.5:1.
- 3D: `zones.ts` anchors exist for every zone of every shape; zone color mapping function (severity → token) unit-tested;
  `Aircraft3DViewer` renders `Fallback2D` when WebGL is unavailable (mock).

### 5.2 E2E (Playwright, seeded with `APP_FROZEN_NOW=T0`)
1. Login as planner → dashboard shows banner "4 מטוסים עם פריטים שפג תוקפם" and KPI values from §4.1.
2. Fleet filter "פג תוקף" lists exactly S03, P05, S07, J02.
3. Aircraft `4X-J04`: canvas present, hotspot for ENGINE_2 has expired styling, side panel MA list shows `MA-2026-00412`;
   clicking the MA highlights the zone (data attribute check).
4. Aircraft `4X-S03` with WebGL disabled (launch arg) → 2D fallback with same hotspots.
5. Mission M-A: fill form in the Hebrew UI → "חשב המלצה" → recommended 2 aircraft; excluded list contains 4X-S05 with text
   containing "בדיקת כני נסע" and hint "כשיר אם יבוצעו עד 4 נחיתות (כולל גע-וסע)".
6. Accept → confirm dialog with disclaimer → mission status "משובץ"; audit page (as admin) shows `ASSIGN`.
7. **RTL checks** on every page: `html[dir=rtl][lang=he]`; no horizontal scrollbar at 1280 px; visible text contains no
   Latin letters outside `<bdi>` elements (allow-list: tail numbers, MA numbers, ATA, VFR/IFR/MEL).
8. **Accessibility:** `@axe-core/playwright` — zero serious/critical violations on dashboard, fleet, aircraft, mission planner.
9. Viewer role cannot see "חשב המלצה" and gets 403 on direct API call.
10. Visual regression (`toHaveScreenshot`, threshold 0.2 %) for dashboard and aircraft page in light/dark; 3D with fixed camera preset.

## 5.3 Test inventory (implemented)
Backend (`backend/tests/`): `test_compliance_unit`, `test_property` (P1–P4, P6–P9), `test_optimization`, `test_golden`,
`test_api` (authz matrix, CSRF, validation, assignment safety, reason-code contract with `reasonCodes.ts`),
`test_ml`, `test_horizon`, `test_oidc` (mocked IdP), `test_vertica_contract` (views on SQLite + real Vertica when
`APP_VERTICA_DSN` is set; dirty-lake rows: NULL/NaN/unknown values must fail safe), `test_migrations` (no ORM/migration
drift, downgrade clean), `test_openapi_snapshot`, `test_manage` (user CLI: hidden password, validation, audit). FR-14/34: band evaluation (same unit), unchanged
scores without bands, eligibility invariance with bands (P3 property, optimize, horizon), unreadable stored bands,
fleet band/unit filters (overdue and unknown never hidden), E2E form → warning → fleet popover filter. `test_api` keeps a route-coverage check: every authenticated route must
appear in the authz matrix. Compliance regression tests cover the 2026-10-09 safety review (commitments since snapshot,
per-flight buffers, later assignments, NaN, programme gaps, undated permits, missing feeds, stale counters).
Frontend: Vitest (`i18n`, `components`, `zones`), Playwright `e2e/demo.spec.ts` (8 flows incl. edit, horizon, apron).

## 6. CI pipeline (order)
`.github/workflows/ci.yml`: `ruff` → `mypy` → `import-linter` → `eslint`/`tsc` → backend unit + property → golden → API → frontend unit →
build → e2e → `pip-audit`/`pnpm audit`. Failing golden or property tests block merge.

## 7. What not to do
- Don't mock the compliance engine in API tests that assert eligibility — use the seeded DB.
- Don't use `datetime.now()` or real time in tests.
- Don't update golden snapshots to make a test pass without understanding why eligibility changed.
