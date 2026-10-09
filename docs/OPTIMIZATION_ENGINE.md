# Optimization Engine — Compliance, ML Scoring & CP-SAT Selection

This is the algorithmic heart of the app. It has three strictly separated stages:

```
            ┌──────────────────────┐    ┌───────────────────────┐    ┌────────────────────────┐
 mission ──►│ 1. Compliance engine │──► │ 2. Scoring (ML + OR   │──► │ 3. Set selection       │──► recommendation
 fleet   ──►│  deterministic, hard │    │  features), eligible  │    │  CP-SAT, fleet-level   │    + alternatives
            │  constraints, reasons│    │  aircraft only        │    │  constraints           │    + explanations
            └──────────────────────┘    └───────────────────────┘    └────────────────────────┘
```

**Invariant (BR-14):** stage 1 alone decides eligibility. Stages 2–3 can only *order/select among* eligible aircraft.
Code location: `backend/app/domain/compliance/`, `backend/app/domain/optimization/`, `backend/app/ml/`.
Domain code is pure (no I/O, no clock calls) — inputs are immutable dataclasses, the `Clock` value is passed in.

---

## 1. Inputs

### 1.1 Mission request (from planner)
| Field | Type | Notes |
|-------|------|-------|
| `required_aircraft_count` | int ≥ 1 | |
| `start_at` | UTC datetime | ≥ now (validation) |
| `est_duration_hours` | decimal > 0 | |
| `est_landings` | int ≥ 1 | full-stop landings |
| `est_touch_and_goes` | int ≥ 0 | |
| `est_engine_starts` | int ≥ 1 | per aircraft; multiplied by engine count |
| `departure_base_code` | str | |
| `required_qualifications` | set[str] | e.g. `{"NIGHT","IFR"}` |
| `engine_requirement` | `ANY` / `SINGLE` / `TWIN` / `MIN_2` | |
| `same_type_required` | bool | |
| `mission_type` | `TRAINING` / `TRANSPORT` / `FERRY` / `OTHER` | |
| `force_include` / `force_exclude` | set[tail] | |
| `overrides` | buffers (optional) | otherwise from planning policy |

### 1.2 Planning policy (admin, `planning_policy` table) — defaults
| Key | Default | Meaning |
|-----|---------|---------|
| `duration_buffer_pct` | 15 | FH consumption = duration × 1.15 |
| `min_duration_buffer_hours` | 0.3 | floor of the FH buffer |
| `calendar_buffer_hours` | 2 | calendar items must be valid ≥ 2 h after mission end |
| `landing_buffer` | 1 | extra landing counted (go-around/diversion) |
| `turnaround_hours` | 1 | gap required between missions of the same tail |
| `idle_policy` | `EXCLUDE` | or `WARN` |
| `tolerance_policy` | `WARN_ONLY` | tolerances never make an aircraft eligible in POC |
| `expiring_soon_days` | 7 | |
| `expiring_soon_pct` | 10 | % of interval |
| `expiring_soon_fh` | 5 | |
| `expiring_soon_ldg` | 10 | |
| `low_margin_pct` | 5 | post-mission remaining < 5 % of interval ⇒ warning |
| `max_staleness_hours` | 24 | data older ⇒ `DATA_STALE` |
| `risk_high_threshold` | 0.30 | |
| `weights_preset` | `BALANCED` | see §3.3 |

---

## 2. Stage 1 — Compliance engine

### 2.1 Mission consumption per activity unit
Let `D = est_duration_hours`, `L = est_landings`, `T = est_touch_and_goes`, `S = est_engine_starts`,
`E = aircraft_type.engine_count`.

| Unit | Consumption `c_u` (per limit ℓ) |
|------|----------------------------------|
| `FH` | `D + max(D × duration_buffer_pct/100, min_duration_buffer_hours)` |
| `LDG` | `L + landing_buffer + tg_weight(ℓ) × T` |
| `TG` | `T` |
| `CYC` | `L + landing_buffer + tg_weight(ℓ) × T` (one cycle per takeoff) |
| `ENG_START` | `S × E` |
| `CAL_DAY` | n/a — compared as datetimes: `deadline = start_at + D h + calendar_buffer_hours` |
| `IDLE_DAY` | `idle_days = (start_at − last_flight_landing_at)` in days |

`tg_weight(ℓ)` comes from `inspection_limit.tg_weight` (default 1.0; 0 means touch-and-go not counted).

### 2.2 Committed consumption
`committed_u(a)` = Σ over missions **already assigned** to aircraft `a` whose **assignment window ends after the
counter snapshot** (`as_of` of the counter(s) behind the limit — for LDG/CYC the older of the unit and TG counters —
clamped to `now`) and whose estimated end is by `start_at`, of each flight's own consumption `c_u(flight)` (§2.1):
- A flight flown between the nightly snapshot and `now` is counted (it is in neither the counters nor the future).
  The window end (buffered duration + turnaround) is used, so a late landing / late ETL is still counted; a flight
  overlapping the snapshot is counted in full (conservative). A future `as_of` never hides a flight.
- Buffers apply **per flight** (3 × 1 h = 3 × 1.3 FH, never 3 h + one buffer).
- Other flights are costed with the **larger** of the base and this mission's buffers, so a mission override
  (e.g. 0 % buffer) never shrinks the buffers of flights that are already committed.
- ML forecasts are **never** used here — only committed plans.
- Idle (R10) may be reset by a *future* committed flight. Because that credit disappears if the flight is cancelled,
  every future assignment is re-evaluated on each dashboard load; one that is no longer eligible (for any reason) is
  shown as an `EXPIRED` alert with `source = ASSIGNMENT`.

**Later commitments.** Flying this mission must not push an already-assigned *later* flight of the same tail over a
usage limit: after the item passes, the consumption of each later commitment is subtracted in start order; the
first one that goes below zero gives `JEOPARDIZES_LATER_ASSIGNMENT {mission_id, mission_name, source, ref, unit,
over_by}` (ineligible). The horizon planner applies the same rule through its cumulative usage constraint.

### 2.3 Per-limit evaluation (usage-based)
For each applicable limit (inspection limit, deferral limit, usage-limited permit):
```
current      = usage_counter[a, u].value               # missing ⇒ DATA_MISSING
projected    = current + committed_u(a) + c_u
remaining_now   = due_value − current
remaining_after = due_value − projected

if remaining_now < 0            → *_EXPIRED            (status EXPIRED, ineligible)
elif remaining_after < 0        → *_DUE_BEFORE_MISSION_END (ineligible)   # equality (=0) is allowed
elif remaining_after < low_margin → warning LOW_MARGIN_AFTER_MISSION
if tolerance and remaining_after < 0 ≤ remaining_after + tolerance → warning TOLERANCE_AVAILABLE (still ineligible)
```

### 2.4 Per-item evaluation (calendar-based)
```
if due_at <= now                              → *_EXPIRED
elif due_at < start_at + D + calendar_buffer  → *_EXPIRES_BEFORE_MISSION_END   (covers "expires during flight")
elif due_at < mission_end + expiring_soon_days → warning EXPIRES_SOON_AFTER_MISSION
```
Applies to: calendar inspection limits, permits (`expires_at`), deferrals (`deferral_expires_at`),
aircraft qualifications (`valid_to`).

### 2.4a Fail-safe data rules (S1) — each yields `DATA_MISSING {item_kind, item_code}`
| Situation | `item_kind` |
|-----------|-------------|
| Counter missing, NaN or ±inf | `COUNTER` (unit) |
| Due row missing / due value NaN | `INSPECTION` |
| Due row without a matching limit row; aircraft type with no (non-idle) inspection limits | `INSPECTION_LIMIT` |
| No `IDLE_DAY` limit row ⇒ the type's `idle_limit_days` applies; if that is ≤ 0 | `INSPECTION_LIMIT` |
| `DEFERRED` MA with neither expiry nor usage limit; deferral/permit limit value without a unit | `DEFERRAL` / `PERMIT` |
| Grounding or required permit with NULL expiry and no usage limit (never "valid forever"; an undated permit never supersedes a dated one of the same type) | `PERMIT` |
| Permit issued in the future (`issued_at > now`): not held — a pre-entered renewal never hides the gap after the current permit | (`PERMIT_*` on the current one, or `PERMIT` if none) |
| `max_endurance_hours` NaN/NULL; aircraft type row missing | `AIRCRAFT_TYPE` |
| Adapter could not read the aircraft status / an MA / a permit row | `AIRCRAFT_STATUS` / `MAINTENANCE_ACTION` / `PERMIT` |

A failing permit blocks if its type is grounding **or required**. A failing permit that is neither does not block but
always yields warning `NON_GROUNDING_PERMIT_INVALID {permit_type_code, outcome}` — it is never silently dropped.

Timestamps more than 15 min in the future (feed load, aircraft row, counter `as_of`, last flight) are untrusted:
`DATA_STALE` with a negative `age_hours`. Adapter data issues make the fleet status `UNKNOWN`, appear as UNKNOWN items,
and make the aircraft unavailable.

### 2.5 Rule list (all evaluated — BR-15: collect every failure)

| # | Rule | Reason code(s) |
|---|------|----------------|
| R1 | Status must be `OPERATIONAL` | `AIRCRAFT_NOT_OPERATIONAL` |
| R2 | No overlapping assignment (incl. turnaround) | `ALREADY_ASSIGNED` |
| R3 | Engine requirement | `ENGINE_REQUIREMENT_NOT_MET` |
| R4 | Endurance `D × (1+buffer) ≤ max_endurance_hours` | `ENDURANCE_EXCEEDED` |
| R5 | No open GROUNDING MA | `GROUNDING_MA_OPEN` |
| R6 | Every inspection limit (2.3 / 2.4) | `INSPECTION_EXPIRED`, `INSPECTION_DUE_BEFORE_MISSION_END` |
| R7 | Every grounding permit (2.4, usage-limited permits via 2.3) | `PERMIT_EXPIRED`, `PERMIT_EXPIRES_BEFORE_MISSION_END` |
| R8 | Every open deferral (2.3 / 2.4) | `DEFERRAL_EXPIRED`, `DEFERRAL_EXPIRES_BEFORE_MISSION_END` |
| R9 | Required qualifications ⊆ effective qualifications (§2.6) | `QUALIFICATION_MISSING`, `QUALIFICATION_EXPIRED`, `QUALIFICATION_EXPIRES_BEFORE_MISSION_END`, `QUALIFICATION_REVOKED_BY_MA`, `QUALIFICATION_REVOKED_BY_PERMIT` |
| R10 | Idle rule | `IDLE_LIMIT_EXCEEDED` (or warning `IDLE_CHECK_REQUIRED` if `idle_policy=WARN`) |
| R11 | Data present & fresh for every applicable limit (§2.4a); every expected feed (`flight_ops`, `mro`, `permits`) loaded, and every counter `as_of`, within `max_staleness_hours` | `DATA_MISSING`, `DATA_STALE` |
| R11b | Does not push a later assigned flight over a usage limit (§2.2) | `JEOPARDIZES_LATER_ASSIGNMENT` |
| R12 | Not force-excluded | `MANUALLY_EXCLUDED` |
| R13 | Permit `FERRY_ONLY` restriction vs mission type | `PERMIT_RESTRICTION_CONFLICT` |

Output per aircraft:
```python
@dataclass(frozen=True)
class EligibilityResult:
    aircraft_id: int
    eligible: bool
    reasons: tuple[Reason, ...]        # Reason(code: ReasonCode, params: Mapping[str, str | float | int])
    warnings: tuple[Reason, ...]
    limit_projections: tuple[LimitProjection, ...]   # every limit with remaining_now / remaining_after / unit
    unblock_hints: tuple[Reason, ...]  # §2.7
```

### 2.6 Effective qualifications
```
base   = { q in aircraft_qualification(a) | valid_to is null or valid_to ≥ mission_deadline }
revMA  = { ma.affects_qualification_code | ma open, ma.affects_qualification_code not null }
revPRM = ∪ restriction_map[r] for r in restrictions of every currently valid permit
           restriction_map = { DAY_ONLY: NIGHT, VFR_ONLY: IFR, NO_PAX: PAX, NO_OVERWATER: OVERWATER }
effective = base − revMA − revPRM
```
For each required `q ∉ effective`, emit the *most specific* reason (revoked by MA > revoked by permit > expired >
expires before mission end > missing).

### 2.7 Unblock hints (FR-43)
For ineligible aircraft, compute what would make them eligible (codes, rendered in Hebrew by the UI):

| Failure | Hint |
|---------|------|
| FH limit | `UNBLOCK_MAX_DURATION {max_hours}` = largest `D` with `D + max(D·pct, floor) ≤ due − current − committed` (only if > 0) |
| LDG/CYC limit | `UNBLOCK_MAX_LANDINGS {max_landings_plus_tg}` (weighted landings + touch-and-goes that still fit) |
| Calendar item expiring during mission | `UNBLOCK_LATEST_END {latest_end}` |
| Inspection expired / due | `UNBLOCK_PERFORM_INSPECTION {inspection_code}` |
| Permit | `UNBLOCK_RENEW_PERMIT {permit_type_code}` |
| Qualification expired | `UNBLOCK_RENEW_QUALIFICATION {qualification_code}` |
| Grounding MA / deferral | `UNBLOCK_CLOSE_MA {ma_number}` |
| Idle | `UNBLOCK_IDLE_CHECK` |

Parameter names and the full code list are defined in API.md §5 (single source of truth).

### 2.8 Fleet compliance status (no mission)
Same engine with a "null mission" (D = 0, deadline = now): item status = `EXPIRED` if remaining_now < 0 / due ≤ now;
`EXPIRING_SOON` if within thresholds (§1.2); `UNKNOWN` if data missing/stale (incl. a feed that never loaded, a
stale counter, or an adapter data issue); else `OK`.
Aircraft overall status = worst item (`EXPIRED` > `UNKNOWN` > `EXPIRING_SOON` > `OK`).

---

## 3. Stage 2 — Scoring (eligible aircraft only)

Each term `s_i ∈ [0, 1]`, higher is better. Final `score = 100 × Σ wᵢ sᵢ / Σ wᵢ`.

| Term | Formula | Source |
|------|---------|--------|
| `risk` | `1 − p_defect(a)` (calibrated probability, §4.1). Model unavailable ⇒ constant 0.5 for all | ML |
| `best_fit` | Binding usage limit = the one with smallest `remaining_after / interval`. `s = 1 − min(remaining_after, H_u) / H_u` with horizon `H_FH = 50`, `H_LDG = 100`, `H_CYC = 100`, `H_ENG_START = 100`. Tight fit preferred (classic best-fit bin packing: keep long-interval aircraft for long missions) | deterministic |
| `balance` | `σ((μ_type − fh30(a)) / sd_type)` where `fh30` = flight hours in last 30 days, σ = logistic | deterministic |
| `capacity` | Computed in CP-SAT (§5) as hangar-overflow penalty; per-aircraft preview value `= cap_w / max(cap_w, load_w + 1)` for display | ML forecast |
| `base_match` | 1 if `aircraft.base_code == departure_base_code` else 0 | deterministic |
| `warnings` | `1 − min(1, 0.25 × len(warnings))` | stage 1 |

### 3.3 Weight presets
| Preset (HE) | risk | best_fit | balance | capacity | base_match | warnings |
|-------------|------|----------|---------|----------|------------|----------|
| `BALANCED` — מאוזן | 0.30 | 0.25 | 0.15 | 0.15 | 0.10 | 0.05 |
| `MAX_SAFETY` — בטיחות מקסימלית | 0.55 | 0.10 | 0.10 | 0.10 | 0.05 | 0.10 |
| `MAX_AVAILABILITY` — זמינות מקסימלית | 0.15 | 0.35 | 0.20 | 0.20 | 0.05 | 0.05 |

Score breakdown (each term value and weighted contribution) is persisted in `optimization_candidate.score_breakdown`
and returned to the UI.

---

## 4. ML models (`backend/app/ml/`)

All models: scikit-learn, deterministic `random_state=42`, artifacts saved with `joblib` to `data/models/<name>/<version>/`
alongside `model_card.json` (features, training window, metrics, data hash). Registry table `ml_model`.
**Fallback:** model missing/corrupt ⇒ neutral constants + warning `ML_MODEL_UNAVAILABLE`; optimization still runs.

### 4.1 Defect-risk model (`defect_risk`)
- **Target:** grounding MA opened within the next 50 FH flown after snapshot `t` (or within 30 days if the aircraft
  flies < 50 FH in 30 days).
- **Snapshots:** weekly per aircraft over history. **Split by time** (train: first 14 months, validate: last 4) — no
  random split (leakage).
- **Features (computed as of `t`, no future data):** aircraft type (one-hot), age years, total FH, FH and days since
  last A/C check, open deferred MA count, defects in last 90 days, grounding defects in last 365 days, FH last 30 days,
  landings last 30 days, touch-and-go ratio last 30 days, idle days, mean sortie length last 30 days, base (one-hot).
- **Model:** `HistGradientBoostingClassifier` + `CalibratedClassifierCV(method="isotonic")`.
- **Acceptance (synthetic data):** ROC-AUC ≥ 0.70, Brier ≤ 0.20 on validation window.
- **Explainability:** top-3 local contributions via `shap.TreeExplainer` when installed, else global permutation
  importance. Returned as feature codes (UI renders Hebrew names).
- **Bands:** low < 0.15 ≤ medium < 0.30 ≤ high (`HIGH_DEFECT_RISK` warning). Thresholds are set relative to the
  fleet base rate of the target (~20 % on the synthetic data); re-calibrate on real data.

### 4.2 Utilization forecast (`utilization`)
- Per aircraft and unit (FH, LDG, TG, CYC): daily rate = EWMA (half-life 14 days) over last 90 days, shrunk to type
  mean with weight `n / (n + 10)` (n = flying days). P10/P90 via bootstrap of daily values.
- Used for: predicted due **dates** of usage limits (timeline FR-13, hangar-capacity term). Never for eligibility.

### 4.3 Duration-buffer model (`duration_buffer`) — Should
- Target: `actual_duration / planned_duration` from `flight_log`.
- `GradientBoostingRegressor(loss="quantile", alpha=0.9)`; features: mission type, aircraft type, planned duration,
  landings, touch-and-goes, month.
- Output: suggested buffer % = `max(policy default, (P90 ratio − 1) × 100)`. Shown as a suggestion in the mission
  form; used only if the planner accepts it.

### 4.4 Training
`make train` → `python -m app.ml.train --all` reads the example DB history, writes artifacts + model cards, updates
registry. Synthetic history is generated with planted signal (DATABASE.md §6.3) so the models learn something real.

---

## 5. Stage 3 — Set selection with OR-Tools CP-SAT

### 5.1 Model
```
Sets:     A = eligible aircraft, W = ISO weeks in planning horizon (8 weeks)
Vars:     x_a ∈ {0,1}                     aircraft a selected
          y_t ∈ {0,1}                     type t chosen (only if same_type_required)
          o_w ∈ ℤ≥0                       hangar overflow in week w
Params:   score_a ∈ ℤ (score × 1000)
          week_before(a), week_after(a)   predicted week of a's next inspection without / with this mission (§4.2)
          base_load_w, cap_w              predicted inspections & hangar capacity per week (base of the aircraft)

Constraints:
  Σ_a x_a = N                                       (if |A| < N: Σ x_a = |A|, flag SHORTFALL)
  x_a = 1               ∀ a ∈ force_include ∩ A
  Σ_t y_t = 1 ;  x_a ≤ y_type(a)                    (same type only)
  o_w ≥ base_load_w + Σ_{a: week_after(a)=w} x_a − Σ_{a: week_before(a)=w} x_a − cap_w   ∀ w

Objective:
  maximize  Σ_a score_a · x_a  −  λ · Σ_w o_w        (λ = 15 000 ≈ 15 score points per overflow aircraft-week)
```
Solver params: `max_time_in_seconds=2.0`, `num_search_workers=1` in tests (deterministic), `8` in prod with
`random_seed=42`. Persist solver status (`OPTIMAL`/`FEASIBLE`/`INFEASIBLE`), objective, wall time.

### 5.2 Alternatives
After the optimal set `S₁`, add the no-good cut `Σ_{a∈S₁} x_a ≤ N − 1` and re-solve; repeat for up to 3 alternatives.
Keep only alternatives with objective ≥ 85 % of the best. Each alternative carries its own score breakdown.

### 5.3 Infeasibility handling
- Force-include of an ineligible tail ⇒ 422 with that tail's reasons (never silently ignore). The horizon planner
  does the same per mission (`params.mission_id`, `reasons[mission_id][tail]`).
- Same-type with no type having ≥ N eligible ⇒ return best partial per type + `SHORTFALL` + unblock hints.
- Shortfall ⇒ include the "nearest to eligible" aircraft (fewest reasons, smallest numeric gaps) with hints.

### 5.4 Complexity
Single mission: ≤ 300 boolean vars, solves in milliseconds. Multi-mission horizon (FR-46, later): `x_{a,m}` with
no-overlap per aircraft and cumulative counters per limit — same module, separate `HorizonPlanner` class.

---

## 6. Determinism, performance, logging
- Same inputs + same `Clock` + same model version ⇒ identical output (golden tests rely on this).
- Sort aircraft by `aircraft_id` before building the model; never iterate over unordered sets.
- Budget: stage 1 < 200 ms for 200 aircraft; stage 2 < 300 ms; stage 3 < 2 s.
- Log one structured event per run: run_id, mission_id, n_aircraft, n_eligible, solver status, timings per stage,
  model versions. Never log full aircraft data.

## 7. Pseudocode (service level)
```python
def optimize(mission: MissionRequest, clock: Clock) -> OptimizationResult:
    now = clock.now()
    policy = policy_repo.get_effective(mission.overrides)
    snapshot = fleet_repo.load_snapshot(now)                 # aircraft, counters, inspections, permits, MAs, quals, assignments
    results = [evaluate_eligibility(a, mission, snapshot, policy, now) for a in snapshot.aircraft_sorted()]
    eligible = [r for r in results if r.eligible]
    features = ml.score_inputs(eligible, snapshot, now)      # p_defect, utilization, weeks — neutral if model missing
    scored = [score(r, features[r.aircraft_id], policy.weights) for r in eligible]
    selection = cpsat.select(scored, mission, snapshot.hangar, policy)
    return OptimizationResult(selection, alternatives=selection.alternatives,
                              eligible=scored, excluded=[r for r in results if not r.eligible])
```
