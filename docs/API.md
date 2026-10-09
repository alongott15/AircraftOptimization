# API Contract — `/api/v1`

FastAPI generates OpenAPI at `/api/v1/openapi.json`; the frontend generates TS types from it (`pnpm gen:api`).
This document is the human contract; if code and doc disagree, fix one of them in the same PR.

## 1. Conventions
- JSON, UTF-8, **snake_case** keys everywhere (no aliasing).
- Datetimes ISO-8601 **UTC with `Z`** (`2026-10-08T19:00:00Z`). Frontend converts to `Asia/Jerusalem`.
- Decimals as JSON numbers rounded to 2 places. Counters keep their unit code alongside (`{"value": 1.5, "unit": "FH"}`).
- **No UI copy in responses.** Reasons/warnings/hints are `{ "code": "...", "params": {...} }`. Params reference
  codes (`inspection_code`, `permit_type_code`, `qualification_code`, `unit`); the UI resolves Hebrew names via
  `GET /reference`. Data fields from the DB (`name_he`, MA `title`) are passed through as-is.
- Pagination: `?page=1&page_size=50` → `{ "items": [...], "total": 123, "page": 1, "page_size": 50 }` (max 200).
- Auth: session JWT in `HttpOnly; Secure; SameSite=Strict` cookie; state-changing requests require `X-CSRF-Token`
  header (double-submit). See SECURITY.md.
- Every response carries `X-Request-ID`.
- Roles: `VIEWER` < `PLANNER` / `MAINTENANCE_CONTROLLER` < `ADMIN` (matrix in SECURITY.md §3).

## 2. Errors — RFC 9457 `application/problem+json`
```json
{
  "type": "https://aircraft-opt/errors/validation",
  "title": "Validation failed",
  "status": 422,
  "code": "VALIDATION_ERROR",
  "detail": "Mission request is invalid",
  "errors": [ { "field": "start_at", "code": "START_IN_PAST", "params": {} } ],
  "request_id": "7c1e…"
}
```
`title`/`detail` are English and developer-facing only; the UI renders Hebrew from `code`/`errors[].code`.

| HTTP | `code` | When |
|------|--------|------|
| 400 | `BAD_REQUEST` | malformed body |
| 401 | `UNAUTHENTICATED` | no/expired session |
| 403 | `FORBIDDEN` | role not allowed |
| 404 | `NOT_FOUND` | unknown tail / mission / run |
| 409 | `RUN_STALE` | assign with a run older than 30 min |
| 409 | `ELIGIBILITY_CHANGED` | re-validation at assign found a newly ineligible aircraft (body lists reasons) |
| 409 | `MISSION_NOT_EDITABLE` | edit non-draft mission |
| 409 | `PLAN_CONFLICT` | horizon plan no longer valid at apply time (body `violations[]`: `NOT_ELIGIBLE`, `OVERLAP`, `CUMULATIVE_LIMIT`, `OVER_REQUIRED`, `DUPLICATE`) |
| 422 | `VALIDATION_ERROR` | field errors (`FIELD_REQUIRED`, `VALUE_TOO_SMALL`, `VALUE_TOO_LARGE`, `START_IN_PAST`, `UNKNOWN_QUALIFICATION`, `UNKNOWN_BASE`, `UNKNOWN_TAIL`, `TAIL_FORCED_BOTH_WAYS`, `INVALID_VALUE`) |
| 422 | `FORCE_INCLUDE_INELIGIBLE` | forced tail is ineligible (`params.tail_number`, `reasons[]`; horizon: `params.mission_id`, `reasons[mission_id][tail]`) |
| 429 | `RATE_LIMITED` | login / optimize throttling |
| 503 | `DATA_SOURCE_UNAVAILABLE` | Vertica/SQLite unreachable |
| 500 | `INTERNAL_ERROR` | never leaks stack traces |

## 3. Endpoints

### 3.1 Health & auth
| Method | Path | Role | Notes |
|--------|------|------|-------|
| GET | `/health` | public | liveness `{status:"ok"}` |
| GET | `/ready` | public | `{db, data_source, models: {defect_risk: "ACTIVE"|"MISSING"}}` |
| POST | `/auth/login` | public | local mode only: `{username, password}` → sets cookie; rate-limited |
| POST | `/auth/logout` | any | |
| GET | `/auth/me` | any | `{user_id, username, display_name, role}` |
| GET | `/auth/config` | public | `{mode: "local" | "oidc"}` — the login page shows the SSO button in `oidc` mode |
| GET | `/auth/oidc/login`, `/auth/oidc/callback` | public | SSO (authorization code + PKCE). Callback redirects to `/` or `/?sso_error=<CODE>` |

### 3.2 Reference
| Method | Path | Role | Notes |
|--------|------|------|-------|
| GET | `/reference` | VIEWER | aircraft types, bases, qualifications, activity units, inspection types (+limits per type), permit types, zones, restriction codes, weight presets, mission types, engine requirements, and `codes` (all reason / warning / unblock codes). |

### 3.3 Fleet
| Method | Path | Role | Response |
|--------|------|------|----------|
| GET | `/fleet/summary` | VIEWER | KPIs (below) |
| GET | `/fleet/alerts?status=EXPIRED,EXPIRING_SOON,UNKNOWN&limit=100` | VIEWER | flat list of non-OK items across fleet, sorted by severity then due |
| GET | `/fleet/timeline?days=30` | VIEWER | predicted due items (uses utilization forecast; `predicted=true` flag) |
| GET | `/fleet/data-freshness` | VIEWER | `[{source_name, loaded_at, is_stale}]` |

`/fleet/summary`:
```json
{
  "as_of": "2026-10-08T19:00:00Z",
  "total_aircraft": 24,
  "available_now": 15,
  "availability_pct": 62.5,
  "aircraft_with_expired_items": 4,
  "items_expiring_7d": 9,
  "aircraft_unknown": 1,
  "open_grounding_mas": 1,
  "in_maintenance": 1,
  "by_type": [ { "type_code": "SE-100", "total": 8, "available": 5 } ]
}
```

### 3.4 Aircraft
| Method | Path | Role | Notes |
|--------|------|------|-------|
| GET | `/aircraft` | VIEWER | filters: `type_code, base_code, status, compliance_status, qualification_code, q` (tail search), `sort=tail_number|compliance_status|next_due`; paginated `AircraftSummary` |
| GET | `/aircraft/{tail_number}` | VIEWER | `AircraftDetail` |
| GET | `/aircraft/{tail_number}/flights?from=&to=` | VIEWER | paginated flight log |
| GET | `/aircraft/{tail_number}/risk` | VIEWER | `{probability, band, model_version, top_factors:[{feature_code, direction}]}` or `{band:"UNAVAILABLE"}` |

`AircraftSummary` (`derived_status` is `GROUNDED` when an open grounding MA exists, else `operational_status`):
```json
{
  "aircraft_id": 7, "tail_number": "4X-S03", "type_code": "SE-100", "engine_count": 1, "base_code": "NTH",
  "operational_status": "OPERATIONAL",
  "derived_status": "OPERATIONAL",
  "compliance_status": "EXPIRED",
  "expired_count": 1, "expiring_soon_count": 0, "open_ma_count": 0, "grounding_ma_count": 0,
  "next_due": { "kind": "INSPECTION", "code": "ANNUAL", "unit": "CAL_DAY", "due_at": "2026-10-07T19:00:00Z", "status": "EXPIRED" }
}
```

`AircraftDetail` = `AircraftSummary` +
```json
{
  "serial_number": "SE100-0003", "manufacture_date": "2016-04-12", "last_flight_at": "2026-10-06T14:20:00Z",
  "model": { "model_shape": "SINGLE_PROP_LOW_WING", "engine_count": 1,
             "zones": ["NOSE","COCKPIT","FUSELAGE","WING_L","WING_R","ENGINE_1","GEAR_NOSE","GEAR_MAIN","TAIL"] },
  "counters": [ { "unit": "FH", "value": 4211.4, "as_of": "2026-10-08T19:00:00Z" },
                { "unit": "LDG", "value": 6120, "as_of": "2026-10-08T19:00:00Z" } ],
  "inspections": [
    { "inspection_code": "ANNUAL", "zone_code": null, "status": "EXPIRED",
      "limits": [ { "unit": "CAL_DAY", "due_at": "2026-10-07T19:00:00Z", "remaining": -1.0, "interval": 365, "status": "EXPIRED" } ],
      "predicted_due_at": "2026-10-07T19:00:00Z" }
  ],
  "permits": [ { "permit_id": 31, "permit_type_code": "COA", "permit_number": "COA-S03",
                 "expires_at": "2027-03-01T00:00:00Z", "restrictions": [], "status": "OK" } ],
  "maintenance_actions": [ { "ma_number": "MA-2026-00412", "title": "דליפת שמן במנוע מס׳ 2", "ata_chapter": 79,
      "zone_code": "ENGINE_2", "severity": "GROUNDING", "mel_category": null, "opened_at": "2026-10-06T08:00:00Z",
      "deferral": null, "affects_qualification_code": null, "status": "OPEN" } ],
  "qualifications": [ { "qualification_code": "IFR", "valid_to": null, "effective": true, "revoked_by": null },
                      { "qualification_code": "NIGHT", "valid_to": null, "effective": true, "revoked_by": null } ],
  "idle": { "idle_days": 2.2, "limit_days": 30, "status": "OK" },
  "risk": { "probability": 0.07, "band": "LOW", "model_version": "2026.10.08-1" },
  "data_freshness": { "oldest_source_updated_at": "2026-10-08T19:00:00Z", "is_stale": false }
}
```
Item `status` ∈ `EXPIRED | EXPIRING_SOON | OK | UNKNOWN`. `revoked_by` = `{kind:"MA"|"PERMIT", ref:"MA-2026-00398"|"SPECIAL_FLIGHT", restriction?:"DAY_ONLY"}`.
(The MA in the example belongs to 4X-J04; shown here for shape only.)

### 3.5 Missions & optimization
| Method | Path | Role | Notes |
|--------|------|------|-------|
| POST | `/missions` | PLANNER | create `MissionRequest` → `Mission` (status `DRAFT`) |
| GET | `/missions?status=&from=&to=` | VIEWER | paginated |
| GET | `/missions/{mission_id}` | VIEWER | `Mission` + latest run summary + assignments |
| PATCH | `/missions/{mission_id}` | PLANNER | only `DRAFT`/`OPTIMIZED` (resets to `DRAFT`) |
| POST | `/missions/{mission_id}/duplicate` | PLANNER | |
| POST | `/missions/{mission_id}/cancel` | PLANNER | cancels assignments too; audit |
| POST | `/missions/{mission_id}/optimize` | PLANNER | body `{ "weights_preset": "BALANCED" }` (optional) → `OptimizationRun` |
| GET | `/missions/{mission_id}/runs` | VIEWER | run history |
| GET | `/optimization-runs/{run_id}` | VIEWER | full `OptimizationRun` |
| POST | `/missions/{mission_id}/assign` | PLANNER | `{ "run_id": "…", "set_index": 0 }` → assignments; re-validates (409 `RUN_STALE` / `ELIGIBILITY_CHANGED`) |
| POST | `/missions/{mission_id}/unassign` | PLANNER | |
| POST | `/planning/horizon` | PLANNER | multi-mission plan (FR-46): `{mission_ids?, date_from?, date_to?, weights_preset?}` → `HorizonPlanOut` (per-mission assigned tails, shortfall, overlaps). Default scope: open (DRAFT/OPTIMIZED) missions in the next 14 days |
| POST | `/planning/horizon/apply` | PLANNER | `{assignments: [{mission_id, aircraft_ids}]}` — re-validates eligibility, overlaps and cumulative usage, then assigns all atomically (409 `PLAN_CONFLICT`) |
| POST | `/eligibility/check` | PLANNER, MAINTENANCE_CONTROLLER | stateless what-if: `{ mission: MissionRequest, tail_numbers?: [] }` → `[EligibilityResult]` (not persisted) |
| GET | `/ml/duration-buffer?mission_type=&type_code=&duration_hours=&landings=&touch_and_goes=` | PLANNER | `{suggested_buffer_pct, p50_ratio, p90_ratio, model_version}` or 404 if model missing |

`MissionRequest`:
```json
{
  "name": "אימון לילה",
  "mission_type": "TRAINING",
  "required_aircraft_count": 2,
  "start_at": "2026-10-08T21:00:00Z",
  "est_duration_hours": 2.5,
  "est_landings": 1,
  "est_touch_and_goes": 6,
  "est_engine_starts": 1,
  "departure_base_code": "NTH",
  "required_qualifications": ["NIGHT"],
  "engine_requirement": "SINGLE",
  "same_type_required": false,
  "priority": 3,
  "notes": null,
  "force_include": [],
  "force_exclude": [],
  "overrides": { "duration_buffer_pct": null, "calendar_buffer_hours": null, "landing_buffer": null, "weights_preset": null }
}
```
Validation: `required_aircraft_count` 1–20 · `est_duration_hours` 0.1–24 · `est_landings` 1–50 ·
`est_touch_and_goes` 0–100 · `est_engine_starts` 1–20 · `start_at` ≥ now − 5 min · `name` 1–120 chars ·
codes must exist in reference data · a tail cannot be both forced in and out.

`OptimizationRun`:
```json
{
  "run_id": "3f0c…", "mission_id": 12, "requested_at": "2026-10-08T19:02:11Z", "clock_now": "2026-10-08T19:02:11Z",
  "solver_status": "OPTIMAL", "duration_ms": 184, "weights_preset": "BALANCED",
  "model_versions": { "defect_risk": "2026.10.08-1", "utilization": "2026.10.08-1" },
  "required": 2, "n_aircraft": 24, "n_eligible": 4, "shortfall": 0,
  "sets": [
    { "set_index": 0, "objective": 172.4, "aircraft_ids": [1, 4] },
    { "set_index": 1, "objective": 168.9, "aircraft_ids": [1, 6] }
  ],
  "candidates": [
    {
      "aircraft_id": 1, "tail_number": "4X-S01", "eligible": true, "score": 86.2, "rank": 1,
      "score_breakdown": {
        "risk":       { "value": 0.94, "weight": 0.30, "contribution": 28.2 },
        "best_fit":   { "value": 0.71, "weight": 0.25, "contribution": 17.8 },
        "balance":    { "value": 0.66, "weight": 0.15, "contribution": 9.9 },
        "capacity":   { "value": 1.00, "weight": 0.15, "contribution": 15.0 },
        "base_match": { "value": 1.00, "weight": 0.10, "contribution": 10.0 },
        "warnings":   { "value": 1.00, "weight": 0.05, "contribution": 5.0 }
      },
      "reasons": [], "warnings": [], "unblock_hints": [],
      "projections": [ { "source": "INSPECTION", "ref": "LG_INSP", "unit": "LDG",
                         "remaining_now": 112, "consumption": 8, "remaining_after": 104, "interval": 300 } ]
    },
    {
      "aircraft_id": 5, "tail_number": "4X-S05", "eligible": false, "score": null, "rank": null,
      "reasons": [ { "code": "INSPECTION_DUE_BEFORE_MISSION_END",
                     "params": { "inspection_code": "LG_INSP", "unit": "LDG", "remaining": 5, "required": 8 } } ],
      "warnings": [],
      "unblock_hints": [ { "code": "UNBLOCK_MAX_LANDINGS", "params": { "max_landings_plus_tg": 4 } },
                         { "code": "UNBLOCK_PERFORM_INSPECTION", "params": { "inspection_code": "LG_INSP" } } ],
      "projections": []
    }
  ],
  "nearest_to_eligible": [5],
  "run_warnings": [ { "code": "ML_MODEL_UNAVAILABLE", "params": { "model_name": "defect_risk" } } ],
  "policy": { "duration_buffer_pct": 15.0, "...": "effective policy used for this run" }
}
```
Each candidate also carries `risk_probability`, `week_before`, `week_after` (hangar-capacity inputs); each set carries `tail_numbers`.
Excluded candidates are ordered nearest-to-eligible first (aircraft that can never fly the mission — engine/endurance/
qualification mismatch — last).

### 3.6 ML admin
| Method | Path | Role | Notes |
|--------|------|------|-------|
| GET | `/ml/models` | VIEWER | registry rows (name, version, status, trained_at, metrics) |
| POST | `/ml/models/{model_name}/retrain` | ADMIN | 202 `{job_id}`; POC runs in a background task; audit |
| GET | `/ml/jobs/{job_id}` | ADMIN | status |

### 3.7 Settings & audit
| Method | Path | Role | Notes |
|--------|------|------|-------|
| GET | `/settings/policy` | VIEWER | effective policy (OPT §1.2) |
| PUT | `/settings/policy` | ADMIN | partial update; validated ranges; audit with before/after |
| GET | `/settings/hangar-capacity?base_code=` | VIEWER | |
| PUT | `/settings/hangar-capacity` | ADMIN | `[{base_code, iso_week, slots}]` |
| GET | `/audit?from=&to=&user_id=&action=` | ADMIN, MAINTENANCE_CONTROLLER | paginated |

## 4. Status enums
- `compliance_status` / item `status`: `EXPIRED`, `EXPIRING_SOON`, `OK`, `UNKNOWN`
- `operational_status`: `OPERATIONAL`, `IN_MAINTENANCE`, `GROUNDED`, `STORED`; `derived_status` adds MA-grounding
- `mission.status`: `DRAFT`, `OPTIMIZED`, `ASSIGNED`, `CANCELLED`, `COMPLETED`
- `solver_status`: `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, `SKIPPED` (no eligible aircraft)
- risk `band`: `LOW`, `MEDIUM`, `HIGH`, `UNAVAILABLE`

## 5. Reason / warning / hint codes (single source of truth)

Backend: `backend/app/domain/reason_codes.py`. Frontend: `frontend/src/i18n/reasonCodes.ts` (Hebrew templates in
DESIGN_SYSTEM.md §8). A contract test fails if the two sets differ.

### 5.1 Ineligibility reasons
| Code | Params |
|------|--------|
| `AIRCRAFT_NOT_OPERATIONAL` | `status` |
| `ALREADY_ASSIGNED` | `mission_id`, `mission_name`, `window_start`, `window_end` |
| `ENGINE_REQUIREMENT_NOT_MET` | `required` (ANY/SINGLE/TWIN/MIN_2), `actual` (int) |
| `ENDURANCE_EXCEEDED` | `required_hours`, `max_hours` |
| `GROUNDING_MA_OPEN` | `ma_number`, `zone_code` |
| `INSPECTION_EXPIRED` | `inspection_code`, `unit`, `over_by` (counter) **or** `due_at` (calendar) |
| `INSPECTION_DUE_BEFORE_MISSION_END` | `inspection_code`, `unit`, `remaining`, `required` **or** `due_at`, `mission_end` |
| `PERMIT_EXPIRED` | `permit_type_code`, `expired_at` |
| `PERMIT_EXPIRES_BEFORE_MISSION_END` | `permit_type_code`, `expires_at`, `mission_end` (or `unit`, `remaining`, `required`) |
| `DEFERRAL_EXPIRED` | `ma_number`, `expired_at` or `unit`, `over_by` |
| `DEFERRAL_EXPIRES_BEFORE_MISSION_END` | `ma_number`, `expires_at`, `mission_end` or `unit`, `remaining`, `required` |
| `QUALIFICATION_MISSING` | `qualification_code` |
| `QUALIFICATION_EXPIRED` | `qualification_code`, `expired_at` |
| `QUALIFICATION_EXPIRES_BEFORE_MISSION_END` | `qualification_code`, `expires_at`, `mission_end` |
| `QUALIFICATION_REVOKED_BY_MA` | `qualification_code`, `ma_number` |
| `QUALIFICATION_REVOKED_BY_PERMIT` | `qualification_code`, `permit_type_code`, `restriction` |
| `PERMIT_RESTRICTION_CONFLICT` | `permit_type_code`, `restriction`, `mission_type` |
| `IDLE_LIMIT_EXCEEDED` | `idle_days`, `limit_days` |
| `DATA_MISSING` | `item_kind` (COUNTER/INSPECTION/INSPECTION_LIMIT/PERMIT/DEFERRAL/LAST_FLIGHT/AIRCRAFT_TYPE/AIRCRAFT_STATUS/MAINTENANCE_ACTION — OPT §2.4a), `item_code` |
| `DATA_STALE` | `source_name` (`flight_ops`/`mro`/`permits`/`aircraft`/`usage_counter`/`last_flight`), `age_hours` (`null` = feed never loaded; negative = timestamp in the future), `unit` (usage_counter only) |
| `MANUALLY_EXCLUDED` | — |
| `JEOPARDIZES_LATER_ASSIGNMENT` | `mission_id`, `mission_name`, `source`, `ref`, `unit`, `over_by` |

### 5.2 Warnings (eligible but noteworthy)
| Code | Params |
|------|--------|
| `LOW_MARGIN_AFTER_MISSION` | `source` (INSPECTION/PERMIT/DEFERRAL), `ref`, `unit`, `remaining_after` |
| `EXPIRES_SOON_AFTER_MISSION` | `source`, `ref`, `due_at` |
| `HIGH_DEFECT_RISK` | `probability` |
| `TOLERANCE_AVAILABLE` | `inspection_code`, `unit`, `tolerance` |
| `IDLE_CHECK_REQUIRED` | `idle_days`, `limit_days` |
| `ML_MODEL_UNAVAILABLE` | `model_name` |
| `NON_GROUNDING_PERMIT_INVALID` | `permit_type_code`, `outcome` (`EXPIRED`/`DUE_BEFORE_END`) |

### 5.3 Unblock hints
`UNBLOCK_MAX_DURATION {max_hours}` · `UNBLOCK_MAX_LANDINGS {max_landings_plus_tg}` · `UNBLOCK_LATEST_END {latest_end}` ·
`UNBLOCK_PERFORM_INSPECTION {inspection_code}` · `UNBLOCK_RENEW_PERMIT {permit_type_code}` ·
`UNBLOCK_RENEW_QUALIFICATION {qualification_code}` · `UNBLOCK_CLOSE_MA {ma_number}` · `UNBLOCK_IDLE_CHECK`.

## 6. Versioning
- Breaking changes ⇒ `/api/v2`. Additive fields allowed in v1. New reason codes are additive but require a Hebrew
  template in the same PR (contract test enforces).
