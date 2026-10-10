# Database — Canonical Schema, Example DB & Vertica Mapping

## 1. Principles
- **Two logical stores.**
  - *Fleet data* (read-only for the app): aircraft, counters, inspections, permits, MAs, qualifications, flight logs.
    Production = Vertica data lake via canonical views. POC = tables in `data/poc.sqlite`.
  - *App store* (read/write): missions, optimization runs, assignments, policy, users, audit, ML registry.
    Production = PostgreSQL. POC = same SQLite file.
- **SQLAlchemy ORM models (`backend/app/db/orm.py`) are the source of truth.** The DDL below is the reference;
  keep them in sync. Fleet tables use `FleetBase`, app-store tables `AppBase`; Alembic manages **only** `AppBase`
  (`make migrate`, `make migration m="…"`, drift-tested). App-store tables have **no foreign keys into fleet tables**
  (in production they live in different databases) — `aircraft_id`, `base_code`, `qualification_code` are plain columns.
- **Compliance is computed in Python, not SQL** — the DB stores raw facts only (no "status" columns that could drift).
- All timestamps `TIMESTAMP WITH TIME ZONE` in UTC (SQLite: ISO-8601 text `YYYY-MM-DDTHH:MM:SSZ`).
- Counters are `NUMERIC(12,2)` (FH has decimals; landings/cycles are integral but share the type). The SQLite POC
  stores them as `FLOAT`.
- **Counter semantics:** `LDG` and `CYC` count full-stop events only; touch-and-goes are counted in `TG`. For an LDG/CYC
  limit the current value is `counter + tg_weight × TG`, and its `due_value` is expressed in that same weighted space.
- Every fleet table has `source_updated_at` for staleness checks (FR-71).
- Do not assume referential integrity from Vertica (constraints usually not enforced) — orphans/missing rows ⇒
  `DATA_MISSING` at evaluation time, never a crash.

## 2. Entity overview

```
aircraft_type 1─* aircraft *─1 base
aircraft 1─* usage_counter (per activity_unit)
aircraft 1─* aircraft_inspection_due (per inspection_type × unit)   inspection_type 1─* inspection_limit (per aircraft_type × unit)
aircraft 1─* aircraft_permit *─1 permit_type
aircraft 1─* maintenance_action *─1 zone ; maintenance_action *─0..1 qualification (affects)
aircraft 1─* aircraft_qualification *─1 qualification
aircraft 1─* flight_log
mission 1─* mission_qualification ; mission 1─* mission_force ; mission 1─* optimization_run 1─* optimization_candidate
optimization_run 1─* optimization_set ; mission 1─* mission_assignment *─1 aircraft
```

## 3. Fleet data tables (canonical; POC physical tables, prod Vertica views `ao_v_<name>`)

```sql
CREATE TABLE aircraft_type (
  type_code            VARCHAR(16) PRIMARY KEY,         -- 'SE-100'
  name_he              VARCHAR(100) NOT NULL,
  engine_count         SMALLINT NOT NULL CHECK (engine_count IN (1, 2, 3, 4)),
  max_endurance_hours  NUMERIC(5,2) NOT NULL,
  idle_limit_days      SMALLINT NOT NULL,               -- days without flight before idle check required
  model_shape          VARCHAR(32) NOT NULL             -- 3D generator: SINGLE_PROP_LOW_WING | TWIN_TURBOPROP_HIGH_WING |
                                                        --   TWIN_JET_LOW_WING | FIGHTER_SINGLE | FIGHTER_TWIN
);

CREATE TABLE base (
  base_code  VARCHAR(8) PRIMARY KEY,
  name_he    VARCHAR(100) NOT NULL
);

CREATE TABLE squadron (                                -- טייסת (FR-35); view ao_v_squadron
  squadron_code      VARCHAR(8) PRIMARY KEY,            -- 'SQ-11'
  name_he            VARCHAR(60) NOT NULL,
  base_code          VARCHAR(8) NOT NULL REFERENCES base
);

CREATE TABLE aircraft (
  aircraft_id        INTEGER PRIMARY KEY,
  tail_number        VARCHAR(16) NOT NULL UNIQUE,       -- '4X-P07'
  serial_number      VARCHAR(32) NOT NULL,
  type_code          VARCHAR(16) NOT NULL REFERENCES aircraft_type,
  base_code          VARCHAR(8)  NOT NULL REFERENCES base,
  squadron_code      VARCHAR(8) REFERENCES squadron,    -- NULL ⇒ excluded when a mission chooses squadrons
  status             VARCHAR(16) NOT NULL CHECK (status IN ('OPERATIONAL','IN_MAINTENANCE','GROUNDED','STORED')),
  manufacture_date   DATE NOT NULL,
  last_flight_at     TIMESTAMP WITH TIME ZONE,          -- landing time of last flight; NULL ⇒ DATA_MISSING for idle rule
  source_updated_at  TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE activity_unit (
  unit_code  VARCHAR(16) PRIMARY KEY,                   -- FH, LDG, TG, CYC, ENG_START, CAL_DAY, IDLE_DAY
  name_he    VARCHAR(50) NOT NULL,                      -- 'שעות טיסה'
  short_he   VARCHAR(16) NOT NULL,                      -- 'ש״ט'
  kind       VARCHAR(16) NOT NULL CHECK (kind IN ('COUNTER','CALENDAR','IDLE'))
);

CREATE TABLE usage_counter (
  aircraft_id        INTEGER NOT NULL REFERENCES aircraft,
  unit_code          VARCHAR(16) NOT NULL REFERENCES activity_unit,
  value              NUMERIC(12,2) NOT NULL,            -- lifetime total (TSN)
  as_of              TIMESTAMP WITH TIME ZONE NOT NULL,     -- time the value was confirmed by the source (not
                                                            -- last change); flights after it are added from plans
  source_updated_at  TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (aircraft_id, unit_code)
);

CREATE TABLE flight_log (
  flight_id               INTEGER PRIMARY KEY,
  aircraft_id             INTEGER NOT NULL REFERENCES aircraft,
  mission_type            VARCHAR(16) NOT NULL,         -- TRAINING | TRANSPORT | FERRY | OTHER
  planned_duration_hours  NUMERIC(5,2),
  takeoff_at              TIMESTAMP WITH TIME ZONE NOT NULL,
  landing_at              TIMESTAMP WITH TIME ZONE NOT NULL,
  flight_hours            NUMERIC(5,2) NOT NULL,
  landings                SMALLINT NOT NULL,
  touch_and_goes          SMALLINT NOT NULL DEFAULT 0,
  engine_starts           SMALLINT NOT NULL DEFAULT 1,
  source_updated_at       TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE INDEX ix_flight_log_aircraft_time ON flight_log (aircraft_id, takeoff_at);

CREATE TABLE qualification (
  qualification_code  VARCHAR(16) PRIMARY KEY,         -- IFR, NIGHT, PAX, CARGO, OVERWATER, MEDEVAC, ICING
  name_he             VARCHAR(100) NOT NULL
);

CREATE TABLE aircraft_qualification (
  aircraft_id         INTEGER NOT NULL REFERENCES aircraft,
  qualification_code  VARCHAR(16) NOT NULL REFERENCES qualification,
  valid_from          TIMESTAMP WITH TIME ZONE NOT NULL,
  valid_to            TIMESTAMP WITH TIME ZONE,          -- NULL = no expiry
  source_updated_at   TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (aircraft_id, qualification_code)
);

CREATE TABLE inspection_type (
  inspection_type_code     VARCHAR(24) PRIMARY KEY,    -- I50H, I100H, A_CHECK, C_CHECK, ANNUAL, LG_INSP, ENG_BORESCOPE, IDLE_CHECK
  name_he                  VARCHAR(100) NOT NULL,
  zone_code                VARCHAR(16) REFERENCES zone, -- optional 3D anchor (e.g. LG_INSP → GEAR_MAIN)
  is_grounding_if_overdue  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE inspection_limit (                          -- per aircraft type; several rows = "whichever first"
  inspection_type_code  VARCHAR(24) NOT NULL REFERENCES inspection_type,
  type_code             VARCHAR(16) NOT NULL REFERENCES aircraft_type,
  unit_code             VARCHAR(16) NOT NULL REFERENCES activity_unit,
  interval_value        NUMERIC(12,2) NOT NULL,         -- 100 (FH), 365 (CAL_DAY), 300 (LDG), 30 (IDLE_DAY)
  tolerance_value       NUMERIC(12,2) NOT NULL DEFAULT 0,
  tg_weight             NUMERIC(4,2)  NOT NULL DEFAULT 1.0,  -- LDG/CYC only: how a touch-and-go counts
  PRIMARY KEY (inspection_type_code, type_code, unit_code)
);

CREATE TABLE aircraft_inspection_due (                   -- current due state per aircraft × inspection × unit
  aircraft_id           INTEGER NOT NULL REFERENCES aircraft,
  inspection_type_code  VARCHAR(24) NOT NULL REFERENCES inspection_type,
  unit_code             VARCHAR(16) NOT NULL REFERENCES activity_unit,
  last_done_at          TIMESTAMP WITH TIME ZONE,
  last_done_value       NUMERIC(12,2),                   -- counter value when last done (COUNTER units)
  due_value             NUMERIC(12,2),                   -- COUNTER units
  due_at                TIMESTAMP WITH TIME ZONE,        -- CALENDAR units
  source_updated_at     TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (aircraft_id, inspection_type_code, unit_code),
  CHECK (due_value IS NOT NULL OR due_at IS NOT NULL)
);
-- IDLE_DAY limits have no due row: evaluated from aircraft.last_flight_at and inspection_limit.interval_value.

CREATE TABLE permit_type (
  permit_type_code  VARCHAR(24) PRIMARY KEY,             -- COA, REG, RADIO, INSURANCE, SPECIAL_FLIGHT
  name_he           VARCHAR(100) NOT NULL,
  is_grounding      BOOLEAN NOT NULL DEFAULT TRUE,
  is_required       BOOLEAN NOT NULL DEFAULT TRUE         -- every aircraft must hold one (missing ⇒ DATA_MISSING)
);

CREATE TABLE inspection_event (                          -- maintenance history (ML feature "FH since last major")
  event_id              INTEGER PRIMARY KEY,
  aircraft_id           INTEGER NOT NULL REFERENCES aircraft,
  inspection_type_code  VARCHAR(24) NOT NULL REFERENCES inspection_type,
  performed_at          TIMESTAMP WITH TIME ZONE NOT NULL,
  fh_at                 NUMERIC(12,2) NOT NULL,
  source_updated_at     TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE aircraft_permit (
  permit_id          INTEGER PRIMARY KEY,
  aircraft_id        INTEGER NOT NULL REFERENCES aircraft,
  permit_type_code   VARCHAR(24) NOT NULL REFERENCES permit_type,
  permit_number      VARCHAR(32) NOT NULL,
  issued_at          TIMESTAMP WITH TIME ZONE NOT NULL,
  expires_at         TIMESTAMP WITH TIME ZONE,           -- calendar expiry
  limit_unit_code    VARCHAR(16) REFERENCES activity_unit,  -- optional usage limit (e.g. special permit valid until FH counter X)
  limit_value        NUMERIC(12,2),
  restrictions       VARCHAR(200),                       -- CSV of DAY_ONLY, VFR_ONLY, NO_PAX, NO_OVERWATER, FERRY_ONLY
  status             VARCHAR(16) NOT NULL CHECK (status IN ('ACTIVE','REVOKED')),
  source_updated_at  TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE zone (
  zone_code  VARCHAR(16) PRIMARY KEY,                    -- NOSE, COCKPIT, FUSELAGE, WING_L, WING_R, ENGINE_1, ENGINE_2, GEAR_NOSE, GEAR_MAIN, TAIL
  name_he    VARCHAR(50) NOT NULL
);

CREATE TABLE ata_zone_map (                              -- fallback when MA has no zone
  ata_from   SMALLINT NOT NULL,
  ata_to     SMALLINT NOT NULL,
  zone_code  VARCHAR(16) NOT NULL REFERENCES zone,
  PRIMARY KEY (ata_from, ata_to)
);

CREATE TABLE maintenance_action (
  ma_id                       INTEGER PRIMARY KEY,
  ma_number                   VARCHAR(24) NOT NULL UNIQUE,   -- 'MA-2026-00412'
  aircraft_id                 INTEGER NOT NULL REFERENCES aircraft,
  title                       VARCHAR(200) NOT NULL,         -- free text from MRO (Hebrew in seed)
  description                 VARCHAR(2000),
  ata_chapter                 SMALLINT,
  zone_code                   VARCHAR(16) REFERENCES zone,
  severity                    VARCHAR(16) NOT NULL CHECK (severity IN ('GROUNDING','DEFERRED','MONITOR')),
  mel_category                CHAR(1) CHECK (mel_category IN ('A','B','C','D')),
  status                      VARCHAR(16) NOT NULL CHECK (status IN ('OPEN','CLOSED')),
  opened_at                   TIMESTAMP WITH TIME ZONE NOT NULL,
  closed_at                   TIMESTAMP WITH TIME ZONE,
  deferral_expires_at         TIMESTAMP WITH TIME ZONE,
  deferral_limit_unit         VARCHAR(16) REFERENCES activity_unit,
  deferral_limit_value        NUMERIC(12,2),                 -- counter value at which deferral expires
  affects_qualification_code  VARCHAR(16) REFERENCES qualification,
  source_updated_at           TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE INDEX ix_ma_aircraft_status ON maintenance_action (aircraft_id, status);

CREATE TABLE data_load_status (                          -- freshness per source feed (FR-71)
  source_name  VARCHAR(50) PRIMARY KEY,                  -- 'mro', 'flight_ops', 'permits'
  loaded_at    TIMESTAMP WITH TIME ZONE NOT NULL
);
```

**ATA → zone defaults (seed):** 21–26 → FUSELAGE · 27 → WING_L (flight controls; refine by MA zone) · 28 → WING_R ·
30 → WING_L · 31, 34 → NOSE · 32 → GEAR_MAIN · 33 → FUSELAGE · 35–38 → FUSELAGE · 52–53 → FUSELAGE · 55 → TAIL ·
57 → WING_L · 61 → ENGINE_1 · 71–80 → ENGINE_1 (use MA zone for ENGINE_2).

## 4. App store tables

```sql
CREATE TABLE app_user (
  user_id          INTEGER PRIMARY KEY,
  username         VARCHAR(50) NOT NULL UNIQUE,
  display_name     VARCHAR(100) NOT NULL,
  password_hash    VARCHAR(200),                         -- argon2; NULL when SSO
  role             VARCHAR(24) NOT NULL CHECK (role IN ('VIEWER','PLANNER','MAINTENANCE_CONTROLLER','ADMIN')),
  is_active        BOOLEAN NOT NULL DEFAULT TRUE,
  created_at       TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE planning_policy (
  policy_key   VARCHAR(50) PRIMARY KEY,                  -- keys in OPTIMIZATION_ENGINE.md §1.2
  value_json   TEXT NOT NULL,
  updated_by   INTEGER REFERENCES app_user,
  updated_at   TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE weight_preset (                           -- admin-editable (FR-45); normalized to sum 1
  preset_code   VARCHAR(24) PRIMARY KEY,                 -- BALANCED, MAX_SAFETY, MAX_AVAILABILITY
  weights_json  TEXT NOT NULL
);

CREATE TABLE tg_weight_override (                      -- Alembic 0003; FR-60. Lake reference stays read-only;
  type_code             VARCHAR(16) NOT NULL,           -- the app applies these when it loads the fleet
  inspection_type_code  VARCHAR(24) NOT NULL,
  unit_code             VARCHAR(16) NOT NULL,           -- LDG | CYC
  tg_weight             FLOAT NOT NULL CHECK (tg_weight >= 0 AND tg_weight <= 5),  -- planned use only, ≥ lake weight
  updated_by            INTEGER REFERENCES app_user,
  updated_at            TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (type_code, inspection_type_code, unit_code)
);

CREATE TABLE hangar_capacity (
  base_code  VARCHAR(8) NOT NULL REFERENCES base,
  iso_week   CHAR(8) NOT NULL,                           -- '2026-W41'
  slots      SMALLINT NOT NULL,
  PRIMARY KEY (base_code, iso_week)
);

CREATE TABLE mission (
  mission_id               INTEGER PRIMARY KEY,
  name                     VARCHAR(120) NOT NULL,
  mission_type             VARCHAR(16) NOT NULL CHECK (mission_type IN ('TRAINING','TRANSPORT','FERRY','OTHER')),
  status                   VARCHAR(16) NOT NULL CHECK (status IN ('DRAFT','OPTIMIZED','ASSIGNED','CANCELLED','COMPLETED')),
  required_aircraft_count  SMALLINT NOT NULL CHECK (required_aircraft_count >= 1),
  start_at                 TIMESTAMP WITH TIME ZONE NOT NULL,
  est_duration_hours       NUMERIC(5,2) NOT NULL CHECK (est_duration_hours > 0),
  est_landings             SMALLINT NOT NULL CHECK (est_landings >= 1),
  est_touch_and_goes       SMALLINT NOT NULL DEFAULT 0 CHECK (est_touch_and_goes >= 0),
  est_engine_starts        SMALLINT NOT NULL DEFAULT 1 CHECK (est_engine_starts >= 1),
  departure_base_code      VARCHAR(8) NOT NULL REFERENCES base,
  engine_requirement       VARCHAR(8) NOT NULL CHECK (engine_requirement IN ('ANY','SINGLE','TWIN','MIN_2')),
  same_type_required       BOOLEAN NOT NULL DEFAULT FALSE,
  priority                 SMALLINT NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
  notes                    VARCHAR(1000),
  overrides_json           TEXT,                         -- buffers, weights preset
  created_by               INTEGER NOT NULL REFERENCES app_user,
  created_at               TIMESTAMP WITH TIME ZONE NOT NULL,
  updated_at               TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE mission_scope (                           -- Alembic 0004; FR-35 (no rows of a kind = any)
  mission_id  INTEGER NOT NULL REFERENCES mission ON DELETE CASCADE,
  kind        VARCHAR(16) NOT NULL,                     -- SQUADRON | AIRCRAFT_TYPE
  code        VARCHAR(16) NOT NULL,
  PRIMARY KEY (mission_id, kind, code)
);

CREATE TABLE mission_qualification (
  mission_id          INTEGER NOT NULL REFERENCES mission ON DELETE CASCADE,
  qualification_code  VARCHAR(16) NOT NULL REFERENCES qualification,
  PRIMARY KEY (mission_id, qualification_code)
);

CREATE TABLE mission_force (
  mission_id   INTEGER NOT NULL REFERENCES mission ON DELETE CASCADE,
  aircraft_id  INTEGER NOT NULL REFERENCES aircraft,
  mode         VARCHAR(8) NOT NULL CHECK (mode IN ('INCLUDE','EXCLUDE')),
  PRIMARY KEY (mission_id, aircraft_id)
);

CREATE TABLE optimization_run (
  run_id               CHAR(36) PRIMARY KEY,             -- UUID
  mission_id           INTEGER NOT NULL REFERENCES mission,
  requested_by         INTEGER NOT NULL REFERENCES app_user,
  requested_at         TIMESTAMP WITH TIME ZONE NOT NULL,
  clock_now            TIMESTAMP WITH TIME ZONE NOT NULL, -- the "now" used for evaluation
  data_snapshot_at     TIMESTAMP WITH TIME ZONE NOT NULL, -- oldest source_updated_at used
  policy_json          TEXT NOT NULL,
  weights_json         TEXT NOT NULL,
  model_versions_json  TEXT NOT NULL,
  solver_status        VARCHAR(16) NOT NULL,             -- OPTIMAL | FEASIBLE | INFEASIBLE | SKIPPED
  objective_value      NUMERIC(14,2),
  n_aircraft           SMALLINT NOT NULL,
  n_eligible           SMALLINT NOT NULL,
  shortfall            SMALLINT NOT NULL DEFAULT 0,
  duration_ms          INTEGER NOT NULL,
  result_json          TEXT NOT NULL                     -- full OptimizationRun DTO for exact replay
);

CREATE TABLE optimization_set (                          -- set_index 0 = recommended, 1..3 = alternatives
  run_id           CHAR(36) NOT NULL REFERENCES optimization_run ON DELETE CASCADE,
  set_index        SMALLINT NOT NULL,
  objective_value  NUMERIC(14,2) NOT NULL,
  aircraft_ids     TEXT NOT NULL,                        -- JSON array, sorted
  PRIMARY KEY (run_id, set_index)
);

CREATE TABLE optimization_candidate (
  run_id                CHAR(36) NOT NULL REFERENCES optimization_run ON DELETE CASCADE,
  aircraft_id           INTEGER NOT NULL REFERENCES aircraft,
  eligible              BOOLEAN NOT NULL,
  score                 NUMERIC(6,2),                    -- NULL when ineligible
  rank                  SMALLINT,
  score_breakdown_json  TEXT,                            -- {term: {value, weight, contribution}}
  reasons_json          TEXT NOT NULL,                   -- [{code, params}]
  warnings_json         TEXT NOT NULL,
  unblock_json          TEXT NOT NULL,
  projections_json      TEXT NOT NULL,                   -- per-limit remaining_now / remaining_after
  PRIMARY KEY (run_id, aircraft_id)
);

CREATE TABLE mission_assignment (
  assignment_id  INTEGER PRIMARY KEY,
  mission_id     INTEGER NOT NULL REFERENCES mission,
  aircraft_id    INTEGER NOT NULL REFERENCES aircraft,
  run_id         CHAR(36) REFERENCES optimization_run,  -- NULL for seeded/manual assignments
  set_index      SMALLINT,
  window_start   TIMESTAMP WITH TIME ZONE NOT NULL,
  window_end     TIMESTAMP WITH TIME ZONE NOT NULL,      -- start + duration (buffered) + turnaround
  status         VARCHAR(16) NOT NULL CHECK (status IN ('ACTIVE','CANCELLED')),
  assigned_by    INTEGER NOT NULL REFERENCES app_user,
  assigned_at    TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE INDEX ix_assignment_aircraft_window ON mission_assignment (aircraft_id, window_start, window_end);

CREATE TABLE audit_log (
  audit_id      INTEGER PRIMARY KEY,
  at            TIMESTAMP WITH TIME ZONE NOT NULL,
  user_id       INTEGER REFERENCES app_user,
  action        VARCHAR(50) NOT NULL,                    -- LOGIN, MISSION_CREATE, OPTIMIZE, ASSIGN, ASSIGN_CANCEL, POLICY_UPDATE, MODEL_RETRAIN
  entity_type   VARCHAR(30),
  entity_id     VARCHAR(50),
  details_json  TEXT,
  request_id    VARCHAR(36),
  client_ip     VARCHAR(45)
);
-- append-only: no UPDATE/DELETE grants on audit_log in production

CREATE TABLE ml_model (
  model_name     VARCHAR(50) NOT NULL,                   -- defect_risk | utilization | duration_buffer
  version        VARCHAR(20) NOT NULL,                   -- '2026.10.08-1'
  status         VARCHAR(16) NOT NULL CHECK (status IN ('ACTIVE','ARCHIVED','FAILED')),
  trained_at     TIMESTAMP WITH TIME ZONE NOT NULL,
  metrics_json   TEXT NOT NULL,
  artifact_path  VARCHAR(300) NOT NULL,
  data_hash      CHAR(64) NOT NULL,
  artifact_sha256 CHAR(64) NOT NULL,                     -- verified before loading (SECURITY.md §8)
  PRIMARY KEY (model_name, version)
);

CREATE TABLE maintenance_plan (                        -- Alembic 0005; FR-91 manual schedule (planning only)
  plan_id        INTEGER PRIMARY KEY,                    -- one row per (task, cycle); earlier cycles stay as history
  item_key       VARCHAR(120) NOT NULL,                  -- '<aircraft_id>:<action>:<ref>' (API.md §3.8), indexed
  aircraft_id    INTEGER NOT NULL,                       -- fleet id (no FK: fleet data may live in Vertica)
  tail_number    VARCHAR(16) NOT NULL,
  action         VARCHAR(24) NOT NULL,                   -- PERFORM_INSPECTION | RENEW_PERMIT | CLOSE_MA | …
  ref            VARCHAR(80) NOT NULL,
  fingerprint    VARCHAR(200) NOT NULL,                  -- due cycle planned for; a new cycle starts unplanned (BR-16)
  status         VARCHAR(16) NOT NULL CHECK (status IN ('REQUIRED','SCHEDULED','IN_PROGRESS','DONE')),
  planned_start  TIMESTAMP WITH TIME ZONE,
  planned_end    TIMESTAMP WITH TIME ZONE,
  assignee       VARCHAR(80),
  notes          VARCHAR(500),
  updated_by     INTEGER REFERENCES app_user,
  updated_at     TIMESTAMP WITH TIME ZONE NOT NULL,
  UNIQUE (item_key, fingerprint)                         -- written with INSERT … ON CONFLICT DO NOTHING (race-safe)
);
-- "VERIFIED" is never stored: it is derived from the fleet data (task_done) — a row never changes eligibility.
```

## 5. Reference data (seed)

### 5.1 Aircraft types
| type_code | name_he | engines | endurance h | idle limit d | model_shape |
|-----------|---------|---------|-------------|--------------|-------------|
| SE-100 | מטוס אימון חד-מנועי | 1 | 4.5 | 30 | SINGLE_PROP_LOW_WING |
| TP-200 | מטוס טורבו-פרופ דו-מנועי | 2 | 6.0 | 45 | TWIN_TURBOPROP_HIGH_WING |
| JT-300 | מטוס סילון דו-מנועי | 2 | 9.0 | 45 | TWIN_JET_LOW_WING |
| F-16I | מטוס קרב סופה (F-16I) | 1 | 2.4 | 21 | FIGHTER_SINGLE |
| F-15I | מטוס קרב רעם (F-15I) | 2 | 3.2 | 21 | FIGHTER_TWIN |

### 5.2 Bases
`NTH` בסיס צפון · `CEN` בסיס מרכז · `STH` בסיס דרום · `EST` בסיס מזרח. Hangar capacity: 2 slots/week/base for the next
8 weeks.

Squadrons (FR-35): `SQ-11` אימון צפון (NTH, SE-100) · `SQ-12` אימון מרכז (CEN, SE-100) · `SQ-21` תובלה מרכז
(CEN, TP-200) · `SQ-22` תובלה דרום (STH, TP-200) · `SQ-31` סילון (CEN, JT-300) · `SQ-41` קרב — סופה (EST, F-16I) ·
`SQ-42` קרב — רעם (EST, F-15I).

### 5.3 Activity units
| code | name_he | short_he | kind |
|------|---------|----------|------|
| FH | שעות טיסה | ש״ט | COUNTER |
| LDG | נחיתות | נח׳ | COUNTER |
| TG | גע-וסע | ג״ו | COUNTER |
| CYC | מחזורים | מחז׳ | COUNTER |
| ENG_START | התנעות מנוע | התנעות | COUNTER |
| CAL_DAY | ימים קלנדריים | ימים | CALENDAR |
| IDLE_DAY | ימים ללא טיסה | ימים | IDLE |

### 5.4 Qualifications
`IFR` טיסת מכשירים · `NIGHT` טיסת לילה · `PAX` הסעת נוסעים · `CARGO` הובלת מטען · `OVERWATER` טיסה מעל מים ·
`MEDEVAC` פינוי רפואי · `ICING` טיסה בתנאי קרח.
Defaults per type — SE-100: IFR, NIGHT · TP-200: IFR, NIGHT, PAX, CARGO, OVERWATER, MEDEVAC, ICING ·
JT-300: IFR, NIGHT, PAX, CARGO, OVERWATER, ICING · F-16I / F-15I: IFR, NIGHT, OVERWATER.

### 5.5 Inspection types and limits
| code | name_he | SE-100 | TP-200 | JT-300 | zone |
|------|---------|--------|--------|--------|------|
| I50H | בדיקת 50 שעות | FH 50 (tol 5) | — | — | — |
| I100H | בדיקת 100 שעות | FH 100 (tol 10) | FH 100 (tol 10) | — | — |
| A_CHECK | בדיקת A | — | FH 500 / CAL 120 | FH 500 / CAL 120 | — |
| C_CHECK | בדיקת C | — | FH 4000 / CAL 730 | FH 4000 / CAL 730 | — |
| ANNUAL | בדיקה שנתית | CAL 365 | CAL 365 | CAL 365 | — |
| LG_INSP | בדיקת כני נסע | LDG 300 (tg 1.0) | LDG 600 (tg 1.0) | LDG 800 (tg 0.5) | GEAR_MAIN |
| ENG_BORESCOPE | בדיקת בורוסקופ למנוע | — | FH 300 / ENG_START 600 | FH 300 / ENG_START 600 | ENGINE_1 |
| IDLE_CHECK | בדיקה לאחר השבתה | IDLE_DAY 30 | IDLE_DAY 45 | IDLE_DAY 45 | — |

Fighters (illustrative values for the demo — real limits come from the operator's maintenance programme):

| code | name_he | F-16I | F-15I | zone |
|------|---------|-------|-------|------|
| PHASE | בדיקת פאזה | FH 300 (tol 15) | FH 400 (tol 20) | — |
| ANNUAL | בדיקה שנתית | CAL 365 | CAL 365 | — |
| LG_INSP | בדיקת כני נסע | LDG 500 (tg 1.0) | LDG 600 (tg 1.0) | GEAR_MAIN |
| ENG_BORESCOPE | בדיקת בורוסקופ למנוע | FH 200 / ENG_START 400 | FH 200 / ENG_START 500 | ENGINE_1 |
| IDLE_CHECK | בדיקה לאחר השבתה | IDLE_DAY 21 | IDLE_DAY 21 | — |

### 5.6 Permit types
`COA` תעודת כושר טיסה · `REG` תעודת רישום · `RADIO` רישיון תחנת רדיו · `INSURANCE` פוליסת ביטוח ·
`SPECIAL_FLIGHT` היתר טיסה מיוחד. All `is_grounding = TRUE` in seed; all `is_required = TRUE` except `SPECIAL_FLIGHT`.

### 5.7 Zones
`NOSE` חרטום · `COCKPIT` תא טייס · `FUSELAGE` גוף המטוס · `WING_L` כנף שמאל · `WING_R` כנף ימין ·
`ENGINE_1` מנוע 1 · `ENGINE_2` מנוע 2 · `GEAR_NOSE` כן נסע קדמי · `GEAR_MAIN` כן נסע ראשי · `TAIL` זנב.

## 6. Example DB (POC seed)

### 6.1 Generator
`make seed` → `python -m app.seed.generator --anchor now --seed 42 --out data/poc.sqlite`
- `--anchor` (`T0`): all scenario values are relative to it, so the demo is always "live". Rounded down to the hour.
- Deterministic for a given `--seed` and `--anchor`.
- Steps: (1) reference data → (2) fleet of 24 aircraft → (3) simulate 18 months of flights and defects (§6.3) →
  (4) derive counters = initial TSN + Σ flight_log → (5) set healthy due state (§6.4) → (6) apply scenarios (§6.2) →
  (7) app-store: demo users, policy defaults, weight presets, hangar capacity, seeded mission `"אימון בוקר — משימה קיימת"` →
  (8) `data_load_status` = T0 for all sources → (9) fighter squadrons (10 aircraft, SC-17), simulated last from their
  own random stream (`seed ^ 0xF16`) so the 24 aircraft above and every golden value stay byte-identical.
- Demo users: one per role (`planner`, `maint`, `ops`, `admin`); password from `APP_DEMO_PASSWORD` in `.env`
  (template in `.env.example`); never hard-coded in source.

### 6.2 Fleet & demo scenarios (golden data — TESTING.md §4 depends on this)

Fleet (34): SE-100 `4X-S01`…`4X-S08` (base NTH/CEN), TP-200 `4X-P01`…`4X-P10` (CEN/STH), JT-300 `4X-J01`…`4X-J06`
(CEN), F-16I `4X-F01`…`4X-F06` (EST, SQ-41), F-15I `4X-R01`…`4X-R04` (EST, SQ-42).
Tail numbers contain a digit and are therefore fictional (real 4X registrations are letters only).

| ID | Tail | Scenario | Exact seed values (T0 = anchor) | Dashboard status at T0 |
|----|------|----------|--------------------------------|------------------------|
| SC-01 | 4X-S03 | Annual inspection expired | `ANNUAL.due_at = T0 − 1 day` | EXPIRED |
| SC-02 | 4X-P05 | COA expired | `COA.expires_at = T0 − 3 days` | EXPIRED |
| SC-03 | 4X-P02 | 100 h inspection nearly due | `I100H.due_value = FH + 1.5` | EXPIRING_SOON |
| SC-04 | 4X-S05 | Landing-gear limit — touch-and-go sensitive | `LG_INSP.due_value = LDG + 5` (tg 1.0) | EXPIRING_SOON |
| SC-05 | 4X-S07 | Idle aircraft | `last_flight_at = T0 − 40 days` (limit 30) | EXPIRED (idle check due) |
| SC-06 | 4X-J04 | Grounding MA on engine 2 | MA `MA-2026-00412` "דליפת שמן במנוע מס׳ 2", ATA 79, zone ENGINE_2, GROUNDING, opened T0 − 2 d | GROUNDED (status stays OPERATIONAL in MRO; MA grounds it) |
| SC-07 | 4X-P08 | Deferred weather radar revokes IFR | MA `MA-2026-00398` "מכ״ם מזג אוויר אינו תקין", ATA 34, zone NOSE, DEFERRED MEL C, `deferral_expires_at = T0 + 6 d`, affects IFR | EXPIRING_SOON |
| SC-08 | 4X-J06 | Insurance expires in 5 hours | `INSURANCE.expires_at = T0 + 5 h` | EXPIRING_SOON |
| SC-09 | 4X-P09 | Restricted special flight permit | `SPECIAL_FLIGHT` restrictions `DAY_ONLY,NO_PAX`, expires T0 + 20 d | OK (restrictions shown) |
| SC-10 | 4X-J02 | OVERWATER qualification expired | `OVERWATER.valid_to = T0 − 10 d` | EXPIRED |
| SC-11 | 4X-P04 | Missing landing counter | no `usage_counter` row for `LDG` | UNKNOWN |
| SC-12 | 4X-S02 | High defect risk | 3 open MONITOR/DEFERRED MAs (non-qualification, deferrals expire ≥ T0 + 30 d), 4 defects in last 90 d, low latent reliability | OK (risk high) |
| SC-13a | 4X-P01 | Best-fit candidate | `A_CHECK.FH due_value = FH + 5.0` | EXPIRING_SOON |
| SC-13b | 4X-P03 | Misses by 0.2 h | `A_CHECK.FH due_value = FH + 4.4` | EXPIRING_SOON |
| SC-14 | 4X-J03 | In hangar | `status = IN_MAINTENANCE` | IN_MAINTENANCE |
| SC-15 | 4X-S08 | Already assigned | assignment to `"אימון בוקר — משימה קיימת"` window T0 + 1 h → T0 + 4 h | OK |
| SC-16 | 4X-P06 | Annual due in 3 days | `ANNUAL.due_at = T0 + 3 d` | EXPIRING_SOON |
| SC-17 | 4X-F01…F06, 4X-R01…R04 | Fighter squadrons — healthy; short endurance (2.4 / 3.2 h) and no PAX keep them out of M-A/B/C; open MONITOR / MEL-C items on F02 (COCKPIT), F04 (WING_L), R02 (ENGINE_2), R03 (GEAR_MAIN) | §6.4 defaults + 4 open non-grounding MAs | OK |

All other tails (`S01, S04, S06, P07, P10, J01, J05`) are healthy (§6.4).
Expected dashboard KPIs at T0: aircraft with expired items = 4 (S03, P05, S07, J02); unknown = 1 (P04);
open grounding MAs = 1 (J04); in maintenance = 1 (J03).

### 6.3 Synthetic history (for ML)
- 18 months of flights. Daily fly probability: SE 0.55, TP 0.45, JT 0.35, F-16I 0.5, F-15I 0.45. Sorties/day 1–3.
- Mission type mix: SE = TRAINING 90 %; TP = TRANSPORT 60 % / TRAINING 30 % / FERRY 10 %; JT = TRANSPORT 80 % / FERRY 20 %;
  F-16I = TRAINING 85 % / OTHER 15 %; F-15I = TRAINING 80 % / OTHER 20 %.
- Duration ~ lognormal per type/mission (median SE 1.4 h, TP 2.2 h, JT 3.5 h, F-16I 1.3 h, F-15I 1.6 h); planned vs
  actual ratio ~ N(1.05, 0.08), FERRY ~ N(1.12, 0.1) (signal for duration-buffer model).
- Touch-and-goes: TRAINING ~ Poisson(5), else 0. Landings: 1 (+1 with p 0.15).
- **Planted defect signal:** hazard per FH = `base × reliability(a) × (1 + FH_since_A_check / 400) × (1 + 0.3 × open_deferred) × (1 + 0.02 × tg_per_hour)`;
  `reliability ~ LogNormal(0, 0.4)` per aircraft (4X-S02 forced to the 95th percentile). 20 % of defects are GROUNDING
  (closed after 1–5 days), the rest DEFERRED/MONITOR.
- Inspections performed in history when due (reset `last_done_*`).
- The 40-day gap for 4X-S07 and the scenario overrides are applied *after* simulation so they always hold.

### 6.4 Healthy defaults
Remaining interval uniformly in [25 %, 90 %] of interval for counters; calendar dues in [30, 300] days;
permits valid ≥ 60 days; `last_flight_at` within 1–6 days; qualifications without expiry unless scenario.

### 6.5 Standard test missions (used by golden tests and the demo)
| ID | Name | N | Start | Duration | LDG | T&G | Quals | Engines | Type | Base |
|----|------|---|-------|----------|-----|-----|-------|---------|------|------|
| M-A | אימון לילה | 2 | T0 + 2 h | 2.5 | 1 | 6 | NIGHT | SINGLE | TRAINING | NTH |
| M-B | הובלת נוסעים | 3 | T0 + 1 d | 4.0 | 2 | 0 | PAX, IFR | MIN_2 | TRANSPORT | CEN |
| M-C | טיסת מעבר ארוכה | 1 | T0 + 3 h | 7.0 | 1 | 0 | OVERWATER | MIN_2 | FERRY | CEN |
| M-F | גיחת אימון קרב | 4 | T0 + 2 h | 1.5 | 1 | 2 | NIGHT | ANY (scope SQ-41, SQ-42) | TRAINING | EST |

### 6.6 Extended demo profile (`--profile extended`, used by `make demo` and docker compose)
The golden data (§6.2, unchanged — a test compares every golden aircraft's status) plus, from its own random stream:

**20 more aircraft** in four new squadrons — `4X-S09…S14` trainers (SQ-13, בסיס דרום), `4X-P11…P16` turboprops
(SQ-23, בסיס צפון), `4X-J07…J09` jets (SQ-32, בסיס דרום), `4X-F07…F11` F-16I (SQ-43, בסיס מזרח) — 54 in all:

| # | Tail | Scenario | Board card |
|---|------|----------|------------|
| EX-01 | 4X-S09 | annual inspection expired 4 days ago | ביצוע בדיקה שנתית — expired, blocking |
| EX-02 | 4X-S10 | 50-h inspection due in 2 FH | expiring soon |
| EX-03 | 4X-S11 | grounding MA `MA-2026-00431` (low oil pressure, engine 1) | close MA — in progress |
| EX-04 | 4X-S12 | idle 35 days (limit 30) | idle check |
| EX-05 | 4X-S13 | COA expires in 6 days | expiring soon |
| EX-06 | 4X-S14 | annual expired, planned, then performed 2 days ago | **verified** |
| EX-07 | 4X-P11 | A-check due in 3 FH | expiring soon |
| EX-08 | 4X-P12 | insurance expired 2 days ago | renew — scheduled |
| EX-09 | 4X-P13 | deferred weather radar `MA-2026-00432` (revokes IFR), deferral ends in 2 days | expiring soon |
| EX-10 | 4X-P14 | in maintenance | return to service — in progress |
| EX-11 | 4X-P15 | landing-gear inspection due in 8 landings | expiring soon |
| EX-12 | 4X-P16 | usage counters 3 days old (stale feed) | refresh stale data — never recommended |
| EX-13 | 4X-J07 | C-check (calendar) due in 5 days | expiring soon |
| EX-14 | 4X-J08 | radio licence expired, planned, then renewed | **verified** |
| EX-15 | 4X-J09 | idle 50 days (limit 45) | idle check |
| EX-16 | 4X-F07 | phase inspection overflown by 3 FH | expired — scheduled |
| EX-17 | 4X-F08 | grounding MA `MA-2026-00433` (hydraulic leak, main gear) | close MA — done, awaiting verification |
| EX-18 | 4X-F09 | annual due in 6 days — before its assigned exercise | expiring soon + dashboard "assignment lost eligibility" |
| EX-19 | 4X-F10 | engine borescope due in 4 FH | expiring soon |
| EX-20 | 4X-F11 | grounding MA `MA-2026-00434`, planned, then closed | **verified** |

**Missions** (ids 2…10): open drafts short of aircraft so recommendations appear — "תרגיל אימון לילה — דרום"
(12 trainers, NIGHT, T0 + 58 h: 7 eligible, 7 verified options), "הטסת צוותים רפואיים" (12 × MIN_2, MEDEVAC,
T0 + 74 h), "גיחות אימון קרב — שבוע הבא" (15 fighters, F-16I/F-15I scope, T0 + 5 d); drafts with enough aircraft
("טיסת מעבר לבסיס דרום", "הובלת מטען לבסיס צפון"); assigned missions on extended aircraft only (4X-S14, 4X-J07,
4X-F09 + 4X-F11); one cancelled.

**Maintenance schedule** (`maintenance_plan`, keyed to each task's real cycle): 5 scheduled (incl. golden 4X-S03 and
4X-P06), 4 in progress (4X-J03, 4X-J04, 4X-S11, 4X-P14), 3 done awaiting verification (4X-P05 COA, 4X-S07 idle check
on the ground — the check flight is not recorded yet, 4X-F08) and 3 verified (EX-06, EX-14, EX-20). Golden aircraft
receive plans only — never data changes or assignments — so PRD §11 still holds (tested).

## 7. Vertica mapping (production)

- App reads **only** views `ao_v_<table>` in schema `aircraft_opt` with the same columns/types as §3.
  The data team maps lake tables → views (e.g. MRO work orders → `ao_v_maintenance_action`). Mapping doc lives with them;
  this repo provides `backend/app/datasources/vertica_views.sql` as the template/contract.
- Type mapping: `VARCHAR(n)` same · `NUMERIC(p,s)` same · `TIMESTAMP WITH TIME ZONE` → `TIMESTAMPTZ` ·
  `BOOLEAN` same · JSON is never read from Vertica.
- Session: read-only (`SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`), dedicated resource pool,
  statement timeout 30 s, label queries (`/*+label(aircraft_opt_snapshot)*/`) for DBA visibility.
- Performance: projections segmented by `aircraft_id`; snapshot query fetches all fleet tables in ≤ 7 queries
  (one per table, filtered to active aircraft) — no N+1.
- Freshness: `ao_v_data_load_status` exposes ETL load times; app applies `max_staleness_hours`. The feeds
  `flight_ops`, `mro` and `permits` must be present — a missing feed row is `DATA_STALE`, never "fine".
- **Do not trust lake constraints** — the adapter (`sql_views.py`) parses every row defensively:
  NULL/NaN/±inf numbers are unknown; NULL `is_grounding`/`is_required` ⇒ TRUE; every MA whose status is not
  `CLOSED` (incl. NULL/unknown) is open and an unknown/NULL severity is `GROUNDING`; an aircraft with an unknown type or
  status is kept (placeholder type, `GROUNDED`) with `DATA_MISSING`; unreadable permit/MA rows become `DATA_MISSING`
  on that aircraft. A limit row that cannot be read (unknown unit such as a new `APU_HR`, negative `tg_weight`,
  NULL/NaN interval) flags `INSPECTION_LIMIT` on every aircraft of that type; an unreadable due row flags
  `INSPECTION`; duplicate due or counter rows are flagged (never "last row wins"); duplicate qualification rows
  resolve conservatively (latest start, earliest end); a permit status other than `ACTIVE`/`REVOKED` is flagged;
  `engine_count ≤ 0` makes the type unknown. Counter rows in a unit no limit uses are ignored.
  A bad row never crashes the snapshot and never makes an aircraft disappear. The POC adapter runs the same parser
  over its tables (`PocSqliteSource.load_snapshot` → `SqlViewSource(view_prefix="")`).
- Contract test: `tests/integration/test_vertica_contract.py` (skipped unless `APP_VERTICA_DSN` set) verifies every view
  exists with expected columns/types.
