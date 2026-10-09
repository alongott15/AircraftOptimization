# PRD — Fleet Usability Optimizer ("מערכת אופטימיזציה לזמינות הצי")

| Field | Value |
|-------|-------|
| Status | Draft v0.1 — POC scope |
| Owner | Product / Fleet Operations |
| Audience | Claude Code (build), engineering, maintenance control, operations planning |
| Related | ARCHITECTURE.md, OPTIMIZATION_ENGINE.md, DATABASE.md, API.md, DESIGN_SYSTEM.md, TESTING.md |

---

## 1. Summary

Operations planners must decide, many times a day, **which aircraft to assign to which flight/mission**. A wrong
choice either (a) violates airworthiness — an inspection, permit, deferred defect or qualification expires *during*
the flight — or (b) wastes fleet availability — e.g. burning the last hours before a heavy inspection on the wrong
tail, clustering inspections so the hangar overflows next week, or flying a high-risk aircraft when a healthier one
was available.

The Fleet Usability Optimizer is a **Hebrew, RTL decision-support web app** that:

1. Shows the live compliance state of every aircraft (inspections, permits, maintenance actions, qualifications,
   usage counters) and **highlights anything expired** or about to expire.
2. Lets a planner **define a mission** (number of aircraft, required qualifications, engine count, estimated flight
   time, landings, touch-and-goes, start time, base …).
3. Runs a **deterministic compliance engine** that projects every limit to mission end + safety buffer and excludes
   ineligible aircraft with clear Hebrew reasons.
4. **Ranks and selects** the best aircraft set using ML (defect-risk prediction, utilization forecast, duration
   buffer) and a constraint optimizer (OR-Tools CP-SAT) that maximizes fleet availability.
5. Visualizes each aircraft in **3D**, with tail number, hotspots for open maintenance actions by zone, permits and
   upcoming inspections, glowing red when anything is expired.

Production data lives in a **Vertica data lake**; the POC ships with a generated example database containing
scripted demo scenarios.

## 2. Problem statement

- Compliance data is spread across MRO tables in the data lake; planners check limits manually or in spreadsheets.
- Limits are multi-dimensional ("500 FH **or** 120 days, whichever first"; landing-gear limits counted in landings
  where touch-and-go may count too; idle-aircraft rules triggered by days without flight).
- "Valid now" is not enough — what matters is "valid **until the aircraft lands**, plus a buffer".
- Assignment decisions are local and greedy; nobody optimizes fleet-level availability (stagger of inspections,
  hangar capacity, utilization balance, defect risk).

## 3. Goals, non-goals, success metrics

### Goals
- G1 — Zero recommendations of aircraft that would be non-compliant at any point of the mission.
- G2 — Reduce planner time per mission assignment from minutes to < 30 seconds.
- G3 — Improve fleet availability by recommending assignments that stagger inspections and balance utilization.
- G4 — Make the fleet state visually obvious (dashboard + 3D) with expired items impossible to miss.
- G5 — Explain every recommendation and every exclusion in plain Hebrew.

### Non-goals (POC)
- Crew scheduling / crew qualifications (aircraft only).
- Writing back to the MRO system or Vertica (read-only integration).
- Route/fuel planning, weather, ATC slots.
- Releasing aircraft to service (the app is advisory; humans sign off).
- Mobile-native app (responsive web is enough; desktop-first).

### Success metrics
| Metric | Target (POC demo) | Target (pilot) |
|--------|------------------|----------------|
| Non-compliant aircraft recommended | 0 in golden suite | 0 incidents |
| Optimization latency (200 aircraft, 1 mission) | < 2 s p95 | < 3 s p95 |
| Exclusions with a human-readable Hebrew reason | 100 % | 100 % |
| Planner acceptance of top recommendation | — | ≥ 70 % |
| Inspection clustering (max aircraft in hangar same week) | measurable in report | −20 % vs baseline |

## 4. Users & personas

| Persona (HE) | Role | Needs |
|--------------|------|-------|
| מתכנן משימות — Mission planner | Creates missions, runs optimization, assigns aircraft | Fast, trustworthy recommendation with reasons |
| בקר תחזוקה — Maintenance controller | Owns compliance data, watches expirations | Alerts, upcoming inspections, per-aircraft detail |
| מנהל תפעול — Operations manager | Fleet KPIs, availability | Dashboard, trends, what-if |
| צופה — Viewer | Read-only stakeholders | Dashboard and aircraft view |
| מנהל מערכת — Admin | Configures weights, buffers, policies, users | Settings, audit log |

## 5. Glossary (EN ↔ HE) and activity units

| Term | Hebrew | Meaning |
|------|--------|---------|
| Tail number | מספר זנב | Aircraft registration, e.g. `4X-P07` (displayed LTR inside RTL text) |
| Inspection | בדיקה | Scheduled maintenance task with one or more limits (whichever first) |
| Permit | היתר | Certificate/permit with expiry: certificate of airworthiness (תעודת כושר טיסה), registration, radio licence, insurance, special flight permit |
| Maintenance action (MA) | פעולת תחזוקה | Open defect/work item; severity GROUNDING / DEFERRED (MEL) / MONITOR |
| Deferral | דחייה (MEL) | Permission to fly with a known defect until a limit (date/FH/landings) |
| Qualification | כשירות | Capability of an aircraft: IFR, NIGHT, PAX, CARGO, OVERWATER, MEDEVAC, ICING … (may expire, may be revoked by MA or permit restriction) |
| Engine count | מספר מנועים | 1 (חד-מנועי) / 2 (דו-מנועי) — attribute of aircraft type |
| Mission | משימה | A flight request requiring N aircraft for a time window |
| Eligible | כשיר למשימה | Passes all hard constraints through mission end + buffer |

**Activity units (יחידות פעילות)** — every limit is expressed in one of these:

| Code | Hebrew | Consumed by a mission as |
|------|--------|--------------------------|
| `FH` | שעות טיסה | estimated duration × (1 + duration buffer) |
| `LDG` | נחיתות | est. full-stop landings + `tg_weight` × est. touch-and-goes (per limit) |
| `TG` | גע-וסע | est. touch-and-goes |
| `CYC` | מחזורים | takeoffs (+ `tg_weight` × touch-and-goes per limit) |
| `ENG_START` | התנעות מנוע | est. engine starts × engine count |
| `CAL_DAY` | ימים קלנדריים | wall-clock until mission end + calendar buffer |
| `IDLE_DAY` | ימים ללא טיסה | days since last flight at mission start (idle rule) |

## 6. Functional requirements

Priority: **M** = Must (POC), **S** = Should (POC if time), **C** = Could (pilot).

### 6.1 Fleet status & compliance
- **FR-01 (M)** Fleet list (צי המטוסים) with tail number, type, engine count, base, operational status, overall
  compliance status (`EXPIRED` / `EXPIRING_SOON` / `OK` / `UNKNOWN`), counts of open MAs, next due item.
- **FR-02 (M)** Filters: type, base, status, compliance status, qualification; free-text search by tail number.
- **FR-03 (M)** Compliance status computed by the backend compliance engine (not SQL) using the injectable clock.
- **FR-04 (M)** "Expiring soon" thresholds configurable (defaults: ≤ 7 calendar days, ≤ 10 % of interval, ≤ 5 FH,
  ≤ 10 landings).
- **FR-05 (M)** Any `UNKNOWN` (missing data) is shown in grey with a "נתונים חסרים" label and treated as not eligible.

### 6.2 Alerts & highlighting
- **FR-10 (M)** Dashboard (לוח בקרה) KPIs: fleet availability %, aircraft eligible now, aircraft with expired items,
  items expiring in 7 days, open grounding MAs, aircraft in maintenance.
- **FR-11 (M)** Global red alert banner when *any* inspection/permit/deferral/qualification is expired; links to the list.
- **FR-12 (M)** Expired items highlighted with red + icon + text in every view (fleet list row, aircraft header,
  tables, 3D model glow and hotspot). Colour is never the only signal.
- **FR-13 (S)** Upcoming-expirations timeline (14/30/90 days) across the fleet, using forecast utilization to
  convert FH/landing limits into predicted dates.

### 6.3 Aircraft detail & 3D
- **FR-20 (M)** Aircraft page with 3D model (procedural by type; engine count reflected — single nose engine vs.
  two wing engines) rotating/zoomable; tail number rendered on the tail fin.
- **FR-21 (M)** Hotspots on the model per zone (nose, cockpit, fuselage, left/right wing, engine 1/2, nose/main
  landing gear, tail) showing open MAs mapped by ATA chapter → zone; colour by severity; click opens detail.
- **FR-22 (M)** Side panel tabs: סקירה (overview), בדיקות (inspections, with remaining per limit), היתרים (permits),
  פעולות תחזוקה (open MAs), כשירויות (qualifications incl. revoked/expired), שימוש (usage counters & recent flights).
- **FR-23 (M)** If anything is expired: model outline pulses red, header shows red badge "פג תוקף" with count.
- **FR-24 (S)** Fleet 3D apron view: all aircraft parked, coloured by status, click to open aircraft page.
- **FR-25 (M)** WebGL-unavailable fallback: 2D top-view SVG with the same hotspots; all 3D info also available in
  the side panel (accessibility).

### 6.4 Mission definition (הגדרת משימה)
- **FR-30 (M)** Planner defines: mission name, mission type (אימון/הובלה/מעבר/אחר), number of aircraft required,
  start date-time, estimated flight time (hours, decimal or HH:MM), estimated full-stop landings, estimated
  touch-and-goes, estimated engine starts (default 1 per sortie), departure base, required qualifications
  (multi-select), engine requirement (any / single / twin / min 2), "same type for all aircraft" toggle,
  priority, optional notes.
- **FR-31 (M)** Advanced: override duration buffer %, calendar buffer hours, landing buffer; force-include /
  force-exclude specific tails (force-include of an ineligible tail is rejected with reasons).
- **FR-32 (M)** Validation in Hebrew (e.g. start in the past, aircraft count < 1, duration ≤ 0).
- **FR-33 (S)** Save mission as draft; duplicate an existing mission.

### 6.5 Optimization & recommendation
- **FR-40 (M)** "חשב המלצה" runs the compliance engine for all aircraft, then the optimizer, and returns:
  the recommended set, up to 3 alternative sets, the full eligible list with scores, and the excluded list with
  reason codes.
- **FR-41 (M)** Each recommended aircraft shows a score breakdown (risk, best-fit of remaining interval,
  utilization balance, maintenance-capacity impact, base match, warnings penalty) and its post-mission margins.
- **FR-42 (M)** Each excluded aircraft shows **all** failing reasons (not just the first), in Hebrew, with numbers
  (e.g. "בדיקת 100 שעות תפוג במהלך המשימה: נותרו 1.5 ש״ט, נדרשות 4.6 ש״ט").
- **FR-43 (M)** If fewer eligible aircraft than required: show partial recommendation + "חסרים X מטוסים" +
  the nearest-to-eligible aircraft and what would unblock them.
- **FR-44 (M)** Planner accepts a set → assignment saved (שיבוץ), aircraft marked busy for the mission window,
  audit entry written.
- **FR-45 (S)** Weights for the scoring terms editable by admin; planner can pick a preset
  ("בטיחות מקסימלית", "זמינות מקסימלית", "מאוזן").
- **FR-46 (C)** Multi-mission horizon planning (14 days) — assign many missions at once.

### 6.6 ML / AI
- **FR-50 (M)** Defect-risk model: probability that an aircraft opens a grounding MA within the next 50 FH.
  Used as a ranking term and shown as "סיכון לתקלה" (low/medium/high + %).
- **FR-51 (M)** Utilization forecast per aircraft (FH/LDG/TG per day) used to predict calendar dates of
  usage-based limits (timeline, hangar-capacity term).
- **FR-52 (S)** Duration-buffer model: learns actual/planned duration ratio by mission type; suggests buffer
  (P90) shown next to the planner's estimate.
- **FR-53 (M)** Model registry with version, training date, metrics; if a model is missing the system falls back
  to neutral values and shows "מודל לא זמין" — never blocks optimization.
- **FR-54 (S)** Admin can retrain on demand (POC: on seeded history).

### 6.7 Settings & administration
- **FR-60 (M)** Planning policy: buffers, expiring-soon thresholds, idle policy (exclude / warn), tolerance
  usage (never / warn), touch-and-go weights per limit (read from reference data, editable by admin).
- **FR-61 (M)** Audit log viewer (who ran what, what was accepted).
- **FR-62 (S)** Hangar capacity per base per week (for clustering term).

### 6.8 Data integration
- **FR-70 (M)** Data-source adapter: `poc` (SQLite) and `vertica` (read-only views) selectable by config.
- **FR-71 (M)** Data freshness indicator ("עודכן לאחרונה") per source; data older than `max_staleness_hours`
  (default 24 h) ⇒ affected aircraft `UNKNOWN`.
- **FR-72 (M)** Seed generator creates the example DB anchored to the current time with all demo scenarios
  (DATABASE.md §6).

### 6.9 Language & UX
- **FR-80 (M)** 100 % Hebrew UI, RTL layout, Hebrew date/number formatting, Israeli timezone.
- **FR-81 (M)** Keyboard accessible, WCAG 2.2 AA contrast; screen-reader labels in Hebrew.
- **FR-82 (S)** Light and dark themes (ops rooms often dark).

## 7. Business rules

- **BR-01 Whichever first.** An inspection with several limits is due at the earliest limit reached.
- **BR-02 Projection to mission end.** For each limit: `projected = current + committed_before_start + mission_consumption (incl. buffer)`,
  where `committed_before_start` is the consumption of missions *already assigned* to that tail (never an ML forecast);
  aircraft is ineligible if `projected > due` for any limit (reaching exactly the limit is allowed; tolerance never used for eligibility in POC).
- **BR-03 Touch-and-go.** For `LDG`/`CYC` limits, each touch-and-go counts as `tg_weight` (default 1.0, per limit).
  `TG` limits count touch-and-goes directly.
- **BR-04 Calendar.** Calendar limits/expiries must be later than `mission_end + calendar_buffer` (default 2 h).
- **BR-05 Idle rule.** If days since last flight at mission start > idle limit for the type, the aircraft requires an
  idle check; default policy: **exclude** with `IDLE_LIMIT_EXCEEDED`.
- **BR-06 Grounding MA.** Any open MA with severity GROUNDING ⇒ ineligible.
- **BR-07 Deferred MA.** A deferral is a limit like any other (date/FH/LDG); it may revoke a qualification
  (`affects_qualification_code`).
- **BR-08 Permits.** Grounding permits (COA, registration, insurance) must be valid through mission end + buffer.
  Permit restrictions revoke qualifications: `DAY_ONLY`→NIGHT, `VFR_ONLY`→IFR, `NO_PAX`→PAX, `FERRY_ONLY`→all
  mission types except מעבר.
- **BR-09 Qualifications.** Effective qualifications = valid-through-mission-end qualifications − revoked by open MAs
  − revoked by permit restrictions. Mission required qualifications ⊆ effective qualifications.
- **BR-10 Engine requirement.** Matches `aircraft_type.engine_count`.
- **BR-11 Endurance.** `duration × (1 + buffer) ≤ aircraft_type.max_endurance_hours`.
- **BR-12 Availability.** Aircraft status must be `OPERATIONAL` and not assigned to an overlapping mission.
- **BR-13 Fail-safe.** Missing/stale data for any limit applicable to the aircraft ⇒ `DATA_MISSING` (ineligible).
- **BR-14 ML boundary.** ML values never change eligibility; they only change scores of eligible aircraft.
- **BR-15 All reasons.** The engine evaluates all rules and returns every failing reason, not just the first.

## 8. Key user flows

1. **Morning check (maintenance controller):** opens dashboard → red banner "4 מטוסים עם פריטים שפג תוקפם" → clicks →
   filtered list → opens `4X-S03` → 3D model glows red, annual inspection row red → exports/acts outside the app.
2. **Plan a mission (planner):** תכנון משימה → fills form (2 aircraft, NIGHT, 2.5 h, 1 landing, 6 touch-and-go) →
   חשב המלצה → sees recommended set + alternatives + excluded list with reasons → opens 3D for a candidate →
   accepts → assignment saved.
3. **What blocks this aircraft? (planner):** in results, expands an excluded aircraft → sees all reasons and
   "what would unblock" (e.g. "ביצוע בדיקת נחיתה" or "קיצור הטיסה ל-2.1 שעות").

## 9. Non-functional requirements

| Area | Requirement |
|------|-------------|
| Performance | Fleet list < 1 s for 300 aircraft; optimization < 2 s p95 for 200 aircraft; 3D first render < 2 s |
| Deployment | On-prem / air-gapped friendly: no CDN at runtime; fonts and 3D assets self-hosted; Docker Compose |
| Browsers | Latest Chrome / Edge (desktop); Firefox best-effort |
| Security | RBAC, SSO-ready (OIDC), audit trail, read-only Vertica account — see SECURITY.md |
| Reliability | Optimization deterministic for identical inputs (fixed solver seed, single worker in tests) |
| Observability | Structured JSON logs, request IDs, optimization run timing & solver status persisted |
| Accessibility | WCAG 2.2 AA; full information available without 3D |
| i18n | Hebrew only for POC, but all strings in one locale file to allow future languages |

## 10. POC scope vs. production

| Capability | POC | Production |
|-----------|-----|-----------|
| Data | Generated SQLite example DB (24 aircraft, 18 months history) | Vertica read views + PostgreSQL app store |
| Auth | Local demo users with roles | OIDC/SAML SSO |
| 3D | Procedural models by type | Optional glTF models per type |
| ML | Trained on synthetic history | Trained on lake history, scheduled retraining |
| Optimization | Single mission | + multi-mission horizon |

## 11. Demo script (5 minutes)

1. Dashboard: red banner, KPIs — point out `4X-S03` (annual inspection expired) and `4X-P05` (COA expired).
2. Open `4X-J04` in 3D: red hotspot on engine #2 (grounding oil leak), tail number on the fin.
3. Open `4X-P08`: amber hotspot on nose (weather radar deferred) — IFR shown as revoked.
4. Mission "אימון לילה": 2 aircraft, NIGHT, 2.5 h, 1 landing + 6 touch-and-go → show `4X-S05` excluded *because of
   touch-and-goes* on landing-gear limit; `4X-S07` excluded for idle days.
5. Mission "הובלת נוסעים": 3 twins, PAX + IFR, 4 h → `4X-P02` excluded (100 h inspection expires in flight),
   `4X-P01` recommended as best-fit (uses remaining hours before A-check), `4X-P03` excluded by 0.2 h.
6. Mission "טיסת מעבר ארוכה": 1 aircraft, 7 h, OVERWATER → turboprops excluded by endurance, `4X-J06` excluded
   because insurance expires mid-flight. Accept → assignment + audit log.

## 12. Open questions (confirm with the business before production)

1. Exact touch-and-go counting per inspection/limit (weight 1.0 default — confirm per type).
2. Default buffers (duration +15 %, calendar +2 h, landings +1) and expiring-soon thresholds.
3. May tolerances (e.g. 100 h ± 10 h) ever be used for planning, or warn only? (POC: warn only.)
4. Idle-aircraft rule values per type and whether the idle check can be scheduled before mission start.
5. Vertica schema/table names and refresh cadence; who owns the canonical views.
6. Identity provider (Azure AD / Keycloak / ADFS) and role mapping.
7. Real fleet size, types, and availability of 3D models (glTF) per type.
8. Should hangar capacity be modelled per base, per type, or per inspection class?
