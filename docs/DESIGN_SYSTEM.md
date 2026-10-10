# Design System — Hebrew RTL Ops UI + 3D Aircraft Viewer

Audience: operations planners and maintenance controllers in a control-room setting. Priorities, in order:
**(1) nothing expired goes unnoticed, (2) decisions are explainable at a glance, (3) dense but calm.**

## 1. Language & direction (non-negotiable)
- `<html lang="he" dir="rtl">`; Mantine `DirectionProvider initialDirection="rtl"` and `theme.dir = 'rtl'`.
- **All user-visible text lives in `frontend/src/i18n/he.ts`.** Components call `t('key')`. ESLint rule bans Hebrew
  literals and JSX text literals outside `i18n/` (CODE_STYLE.md §3.3).
- **CSS logical properties only:** `margin-inline-start`, `padding-inline-end`, `inset-inline-start`, `border-inline-start`,
  `text-align: start`. Physical `left/right` are banned by Stylelint (exception: 3D canvas internals).
- **Bidi isolation:** wrap Latin/number fragments in `<bdi>` — tail numbers (`<bdi dir="ltr">4X-P07</bdi>`), ATA codes,
  MA numbers, permit numbers, values with units (`<bdi>1.5</bdi> ש״ט`). Helper component `<Ltr>`.
- Directional icons (arrows, chevrons, "back") are mirrored in RTL (`.icon-dir { transform: scaleX(-1) }` under
  `[dir=rtl]`). Non-directional icons (plane, wrench, alert) are not mirrored.
- Progress bars and timelines fill from the inline-start (right) edge. Charts: time axis runs right → left.
- Dates: `DD/MM/YYYY HH:mm`, 24 h, timezone `Asia/Jerusalem`, dayjs locale `he`. Relative: "בעוד 3 ימים", "לפני יומיים".
- Numbers: `Intl.NumberFormat('he-IL')`, FH 1 decimal, counters integer, percentages integer. `font-variant-numeric: tabular-nums` in tables.
- Duration input accepts `2.5` or `2:30`; displayed as `2:30 ש׳`.

## 2. Design tokens (`frontend/src/theme/tokens.ts`)

### 2.1 Color — semantic
| Token | Light | Dark | Use |
|-------|-------|------|-----|
| `bg` | `#F5F7FA` | `#0B1220` | app background |
| `surface` | `#FFFFFF` | `#121A2B` | cards, panels |
| `surface-2` | `#EEF2F6` | `#18223A` | table headers, nested |
| `border` | `#D9E0E8` | `#24304A` | |
| `text` | `#101828` | `#E6EAF2` | |
| `text-muted` | `#475467` | `#98A2B3` | |
| `primary` | `#1D4E89` | `#5B8DEF` | actions, links, focus ring |
| `primary-contrast` | `#FFFFFF` | `#0B1220` | text on primary |

### 2.2 Color — status (the most important tokens)
| Status | Hebrew | fg light | bg light | fg dark | bg dark | Icon (Tabler) |
|--------|--------|----------|----------|---------|---------|---------------|
| `EXPIRED` | פג תוקף | `#B42318` | `#FEE4E2` | `#F97066` | `#3B1214` | `IconAlertOctagon` |
| `EXPIRING_SOON` | עומד לפוג | `#B54708` | `#FEF0C7` | `#FDB022` | `#3A2A0A` | `IconClockExclamation` |
| `OK` | בתוקף | `#067647` | `#DCFAE6` | `#47CD89` | `#0B2B1C` | `IconCircleCheck` |
| `UNKNOWN` | נתונים חסרים | `#475467` | `#EAECF0` | `#98A2B3` | `#1F2737` | `IconHelpCircle` |
| `INFO` | מידע | `#175CD3` | `#D1E9FF` | `#84CAFF` | `#0F2747` | `IconInfoCircle` |

MA severity reuses status colors: GROUNDING = EXPIRED, DEFERRED = EXPIRING_SOON, MONITOR = INFO.
Rule: **status is always color + icon + text** (never color alone). All fg/bg pairs must meet WCAG AA (≥ 4.5:1) —
verify with a contrast unit test over the token table.

### 2.3 3D palette
| Token | Value | Use |
|-------|-------|-----|
| `hull` | `#D8DEE6` | fuselage/wings base |
| `hull-accent` | `#1D4E89` | tail fin, stripe |
| `glass` | `#2B3A55` (opacity 0.6) | cockpit |
| `zone-expired` | `#FF3B30` emissive | grounding MA / expired item in zone |
| `zone-expiring` | `#FFB020` emissive | deferral / expiring item |
| `zone-info` | `#3A86FF` emissive | monitor items |
| `outline-expired` | `#FF3B30` | whole-aircraft pulse when anything is expired |
| `ground` | `#1F2937` (dark) / `#E5E7EB` (light) | |

### 2.4 Typography
- Font: **Heebo** (self-hosted via `@fontsource/heebo`, weights 400/500/700). Fallback: `Arial, sans-serif`. No CDN.
- Scale (px): `xs 12 · sm 14 · md 16 (base) · lg 18 · xl 22 · h3 18/700 · h2 22/700 · h1 28/700`. Line height 1.5.
- Tail numbers in tables: 500 weight, `letter-spacing: 0.02em`.

### 2.5 Spacing, radius, elevation, motion
- Spacing scale 4 px: `4 · 8 · 12 · 16 · 24 · 32 · 48`.
- Radius: inputs 6, cards 8, badges 999.
- Shadows: `sm` cards only; no heavy elevation.
- Motion: 150 ms ease-out for UI; expired pulse 1.6 s ease-in-out infinite. **`prefers-reduced-motion` ⇒ no pulse**
  (static outline + icon).

### 2.6 Layout
- Desktop-first: optimized 1440×900, supported ≥ 1024 px. Dashboard and aircraft view readable at 375 px (stacked).
- Mantine `AppShell`: navbar on the **right** (inline-start), 240 px, collapsible to icons; header 56 px.
- Header (right → left): logo + "רקיע" · nav breadcrumbs · data-freshness indicator · theme toggle · user menu.
- Global `AlertBanner` sits directly under the header on every page while any expired item exists.

## 3. Components (`frontend/src/components/`)

| Component | Purpose | Key props / behaviour |
|-----------|---------|-----------------------|
| `StatusBadge` | Status pill | `status`, `size`, `count?`; icon + Hebrew label from `he.status.*` |
| `AlertBanner` | Global red banner | "{n} מטוסים עם פריטים שפג תוקפם" + button "הצג" → `/fleet?compliance_status=EXPIRED`; role=`alert`; not dismissible while expired items exist |
| `KpiCard` | Dashboard metric | value, label, status tint, click-through filter |
| `FleetTable` | Fleet list | sticky header, row `border-inline-start: 4px` in status color, sort/filter, row click → aircraft page; expired rows get `EXPIRED` bg tint |
| `AircraftCard` | Grid alternative to table | tail, type, status badge, next due, mini counters |
| `ExpiryCountdown` | Remaining time/units | "בעוד 3 ימים" · "נותרו 1.5 ש״ט" · "פג לפני יומיים" (red) |
| `LimitProgress` | Used vs interval bar | fills from inline-start; color by status; tooltip with exact numbers; one bar per limit ("whichever first" shown as stacked rows, binding limit marked "קובע") |
| `ComplianceTable` | Inspections/permits/deferrals | columns: פריט · יחידה · נותר · מועד יעד · סטטוס; expired rows first |
| `MaList` | Open maintenance actions | severity chip, MA number, title, zone, ATA, opened, deferral expiry; click → highlight zone in 3D |
| `QualificationChips` | Effective qualifications | effective = solid; revoked = strikethrough + tooltip reason; expired = red |
| `MissionForm` | Mission definition | sections below (§5.4); inline Hebrew validation; duration-buffer suggestion chip |
| `RecommendationPanel` | Optimization result | tabs: "המלצה" · "חלופה 1..3"; per aircraft `AircraftResultCard`; summary line "נבחרו 3 מתוך 7 מטוסים כשירים" |
| `AircraftResultCard` | One recommended aircraft | tail, type, score ring (0–100), `ScoreBreakdown`, warnings, post-mission margins, "הצג בתלת-ממד" |
| `ScoreBreakdown` | Explain score | horizontal bars per term with weight and contribution; Hebrew term names |
| `ExcludedList` | Ineligible aircraft | accordion grouped by primary reason; each shows **all** reasons + unblock hints |
| `ShortfallNotice` | Not enough aircraft | "חסרים {n} מטוסים" + nearest-to-eligible list |
| `AssignConfirmDialog` | Confirm assignment | lists tails, window, disclaimer; "אשר שיבוץ" |
| `Disclaimer` | Advisory notice | fixed text (§8.6) on every recommendation screen |
| `DataFreshnessIndicator` | Source age | green/amber/red dot + "עודכן לפני 12 דק׳" |
| `Ltr` | Bidi isolation | `<bdi dir="ltr">` wrapper |
| `EmptyState` / `ErrorState` / `LoadingSkeleton` | States | Hebrew copy; error shows request id |
| `Aircraft3DViewer` | 3D model | §6 |

## 4. Pages & wireframes (RTL — right side is the start)

### 4.1 לוח בקרה (Dashboard) `/`
```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ⛔ 4 מטוסים עם פריטים שפג תוקפם                                    [הצג]   │  AlertBanner
├──────────────────────────────────────────────────────────────────────────────┤
│ [זמינות הצי 62%] [כשירים כעת 15] [פגי תוקף 4] [יפוגו ב-7 ימים 9] [מקורקעים 1] │  KpiCards
├───────────────────────────────────────┬──────────────────────────────────────┤
│ התראות (פג תוקף / עומד לפוג / חסר)    │  פריטים קרובים — ציר זמן 30 יום       │
│ • 4X-S03 בדיקה שנתית — פג לפני יום    │  (timeline, right→left)               │
│ • 4X-P05 תעודת כושר טיסה — פג          │                                       │
│ • 4X-J06 פוליסת ביטוח — בעוד 5 שעות   │                                       │
├───────────────────────────────────────┴──────────────────────────────────────┤
│ זמינות לפי דגם (bars)                       │  מצב הצי (mini grid of tails)    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 צי המטוסים `/fleet`
Filters row (דגם · בסיס · סטטוס · מצב תוקף · כשירות · חיפוש מספר זנב) → `FleetTable` (toggle to card grid).
Columns: מספר זנב · דגם · מנועים · בסיס · סטטוס · מצב תוקף · פריט קרוב · פעולות תחזוקה פתוחות · סיכון.

### 4.3 מטוס `/aircraft/:tail`
```
┌────────────────────────────────────────────────────────────────────────────┐
│ 4X-J04 · מטוס סילון דו-מנועי · בסיס מרכז   [מקורקע] [פעולת תחזוקה מקרקעת 1] │
├───────────────────────────────┬────────────────────────────────────────────┤
│ Side panel (tabs)             │                                            │
│ סקירה | בדיקות | היתרים |      │          3D VIEWER                          │
│ פעולות תחזוקה | כשירויות | שימוש│   (hotspots, tail number on fin,           │
│                               │    red pulse on ENGINE_2)                   │
│ …content…                     │   [מבט צד] [מבט עליון] [מבט קדמי] [⟲]      │
└───────────────────────────────┴────────────────────────────────────────────┘
```
Panel is on the right (inline-start), viewer fills the rest. Clicking a list row highlights the zone in 3D and vice versa.

### 4.4 תכנון משימה `/missions/new`
Two-column: `MissionForm` (right) · results area (left) that shows `RecommendationPanel` + `ExcludedList` after
"חשב המלצה". Sticky footer: [חשב המלצה] [שמור טיוטה]; after results: [שבץ מטוסים].

### 4.5 משימות `/missions` · הגדרות `/settings` · יומן פעולות `/audit` · התחברות `/login`
Standard tables/forms using the same components.

## 5. Interaction rules
1. Expired always wins: sorting defaults put `EXPIRED` first; banner persists; 3D pulses.
2. Every number with a unit shows the unit (`ש״ט`, `נח׳`, `ימים`).
3. Every exclusion shows all reasons; first line in bold is the most severe.
4. Optimization results announced via `aria-live="polite"`: "נמצאה המלצה: 3 מטוסים".
5. Destructive or committing actions (שיבוץ, ביטול משימה, שינוי מדיניות) require confirmation dialogs.
6. Loading > 300 ms shows skeletons; optimization shows a progress state "מחשב המלצה…".
7. Focus ring 2 px `primary`, always visible on keyboard focus.

### 5.4 Mission form sections
1. **פרטי משימה** — שם המשימה · סוג משימה · עדיפות · הערות
2. **מטוסים ודרישות** — מספר מטוסים נדרש · כשירויות נדרשות (multi-select chips) · דרישת מנועים (segmented: ללא דרישה / חד-מנועי / דו-מנועי / לפחות 2 מנועים) · כל המטוסים מאותו דגם (switch)
3. **זמן ופעילות** — מועד המראה (date-time picker, Hebrew) · משך טיסה משוער · נחיתות מלאות · גע-וסע · התנעות מנוע לכל מטוס · בסיס יציאה
4. **הגדרות מתקדמות** (collapsed) — מרווח זמן טיסה (%) · מרווח קלנדרי (שעות) · נחיתות רזרבה · פרופיל משקלות · כלול בהכרח · החרג

## 6. 3D Aircraft Viewer (`features/aircraft3d/`)

### 6.1 Tech
`@react-three/fiber` + `@react-three/drei`; lazy-loaded route chunk; `<Canvas frameloop="demand" dpr={[1, 2]}>`.
Lights: `hemisphereLight` + `directionalLight` (+ `ContactShadows`). **No HDR environment maps** (they load from a CDN;
the app must work offline). `OrbitControls` with damping, min/max distance, polar clamp above ground.

### 6.2 Procedural models by `model_shape`
Realistic proportions in metres (`zones.ts`), geometry generated in `procedural.ts` — no model files (offline):
fuselages lofted through elliptical sections, NACA-section wings/tails with taper, sweep and dihedral, lathed
spinners/nacelles/nozzles, propellers with pitched blades, tyred landing gear; physically based materials (gloss
paint with clearcoat, glass, metal, rubber) lit by an in-scene light rig (`StudioLights`, no HDR download).

| Shape | Geometry |
|-------|----------|
| `SINGLE_PROP_LOW_WING` | turboprop trainer: tandem bubble canopy, 4-blade prop + spinner (NOSE/ENGINE_1), low wing, nav lights |
| `TWIN_TURBOPROP_HIGH_WING` | transport: high wing + fairing, 2 nacelles with 4-blade props, sponson main gear, T-tail, windows |
| `TWIN_JET_LOW_WING` | business jet: swept wing with winglets, rear-mounted engines on pylons, T-tail, oval windows |
| `FIGHTER_SINGLE` | F-16-like: ventral intake, two-seat bubble canopy, LERX, conformal tanks, single fin, ventral fins, tip missiles |
| `FIGHTER_TWIN` | F-15-like: boxy side intakes, large swept shoulder wing, twin vertical tails, twin nozzles, two-seat canopy |

Fighters use a generated three-tone desert camouflage (deterministic canvas texture, grey underside, grey radome).
Each mesh carries `userData.zone` (`NOSE, COCKPIT, FUSELAGE, WING_L, WING_R, ENGINE_1, ENGINE_2, GEAR_NOSE, GEAR_MAIN, TAIL`).
Engine count comes from data (`model.engine_count`): `shapeFor()` swaps to the same family's counterpart when a shape
disagrees with it. The tail number is drawn on the fin(s) (white on livery, dark on camouflage). Settings → "דגמי
תלת-ממד" previews every model. The fleet apron uses a lighter variant (no small details, shadows or clearcoat).
Optional future: glTF per type in `public/models/<type>.glb` with the same zone node names.

### 6.3 Data overlays
- **Tail number** rendered with drei `<Text>` on both sides of the vertical fin (Latin glyphs; self-hosted `.woff`/`.ttf`
  font from `public/fonts/`), plus small on the fuselage side.
- **Zone tint:** zone with open GROUNDING MA or expired zone-bound inspection → `zone-expired` emissive pulse;
  DEFERRED/expiring → `zone-expiring`; MONITOR → `zone-info`.
- **Hotspots:** drei `<Html>` markers at zone anchors (`zones.ts` per shape): circle with count badge and status color.
  Click → popover (RTL Hebrew) listing that zone's MAs and inspections with `StatusBadge` and `ExpiryCountdown`.
- **Floating info card** (Html, above aircraft): tail number, type, and chips — "פעולות תחזוקה פתוחות: 2",
  "היתרים שפג תוקפם: 1", "בדיקה קרובה: בדיקת 100 שעות — נותרו 1.5 ש״ט".
- **Expired anywhere** → whole-aircraft red outline pulse (drei `<Outlines>`) + red "פג תוקף" badge in the info card.
- Non-zone items (permits, calendar inspections without zone) appear in the info card and side panel, not as hotspots.

### 6.4 Controls & a11y
- Toolbar (top inline-end): מבט צד · מבט עליון · מבט קדמי · איפוס תצוגה · הצג/הסתר נקודות חמות · סיבוב אוטומטי.
- Canvas `aria-label` = generated Hebrew summary ("מטוס 4X-J04, פעולת תחזוקה מקרקעת אחת במנוע 2").
- Everything shown in 3D is also in the side panel (keyboard/screen-reader users never need the canvas).
- WebGL unavailable → `Fallback2D` (SVG top view with the same hotspots).
- Dispose geometries/materials on unmount; `invalidate()` only on interaction or pulse ticks.

### 6.5 Fleet apron view (Should)
Instanced simplified models in a grid per base, colored by compliance status; hover shows tail; click navigates.

## 7. Charts
Mantine Charts (Recharts). Status colors from tokens; time axis reversed for RTL; Hebrew axis labels and tooltips;
always include a text/table alternative.

## 8. Hebrew copy (seed for `he.ts`)

### 8.1 Navigation & common
| Key | Hebrew |
|-----|--------|
| app.title | רקיע (tagline: כשירות וזמינות הצי · subtitle: מערכת תומכת החלטה לכשירות וזמינות הצי) |
| nav.dashboard | לוח בקרה |
| nav.fleet | צי המטוסים |
| nav.missionNew | תכנון משימה |
| nav.missions | משימות |
| nav.settings | הגדרות |
| nav.audit | יומן פעולות |
| action.optimize | חשב המלצה |
| action.assign | שבץ מטוסים |
| action.confirmAssign | אשר שיבוץ |
| action.saveDraft | שמור טיוטה |
| action.duplicate | שכפל |
| action.cancelMission | בטל משימה |
| action.show | הצג |
| action.clearFilters | נקה סינון |
| action.view3d | הצג בתלת-ממד |
| action.login / logout | התחבר / התנתק |
| banner.expired | {count} מטוסים עם פריטים שפג תוקפם |
| freshness.updated | עודכן {relative} |

### 8.2 Domain labels
| Key | Hebrew |
|-----|--------|
| status.EXPIRED / EXPIRING_SOON / OK / UNKNOWN | פג תוקף / עומד לפוג / בתוקף / נתונים חסרים |
| opStatus.OPERATIONAL / IN_MAINTENANCE / GROUNDED / STORED | מבצעי / בתחזוקה / מקורקע / באחסון |
| severity.GROUNDING / DEFERRED / MONITOR | מקרקעת / נדחית (MEL) / למעקב |
| risk.LOW / MEDIUM / HIGH / UNAVAILABLE | נמוך / בינוני / גבוה / לא זמין |
| engineReq.ANY / SINGLE / TWIN / MIN_2 | ללא דרישה / חד-מנועי / דו-מנועי / לפחות 2 מנועים |
| missionType.TRAINING / TRANSPORT / FERRY / OTHER | אימון / הובלה / טיסת מעבר / אחר |
| missionStatus.DRAFT / OPTIMIZED / ASSIGNED / CANCELLED / COMPLETED | טיוטה / חושבה המלצה / משובץ / בוטל / הושלם |
| restriction.DAY_ONLY / VFR_ONLY / NO_PAX / NO_OVERWATER / FERRY_ONLY | טיסות יום בלבד / טיסה לפי ראייה בלבד (VFR) / ללא נוסעים / ללא טיסה מעל מים / טיסות מעבר בלבד |
| score.risk / best_fit / balance / capacity / base_match / warnings | סיכון לתקלה / ניצול מרווח עד בדיקה / איזון ניצולת / עומס האנגר / התאמת בסיס / אזהרות |
| preset.BALANCED / MAX_SAFETY / MAX_AVAILABILITY | מאוזן / בטיחות מקסימלית / זמינות מקסימלית |
| tab.overview / inspections / permits / mas / qualifications / usage | סקירה / בדיקות / היתרים / פעולות תחזוקה / כשירויות / שימוש |
| field.tailNumber | מספר זנב |
| field.requiredCount | מספר מטוסים נדרש |
| field.startAt | מועד המראה |
| field.duration | משך טיסה משוער (שעות) |
| field.landings | נחיתות מלאות משוערות |
| field.touchAndGoes | גע-וסע משוערים |
| field.engineStarts | התנעות מנוע לכל מטוס |
| field.base | בסיס יציאה |
| field.qualifications | כשירויות נדרשות |
| field.engineRequirement | דרישת מנועים |
| field.sameType | כל המטוסים מאותו דגם |
| field.durationBuffer | מרווח זמן טיסה (%) |
| field.calendarBuffer | מרווח קלנדרי (שעות) |
| field.landingBuffer | נחיתות רזרבה |
| field.forceInclude / forceExclude | כלול בהכרח / החרג |
| result.summary | נבחרו {selected} מתוך {eligible} מטוסים כשירים |
| result.recommended / alternative | המלצה / חלופה {n} |
| result.excluded | מטוסים שאינם כשירים למשימה ({count}) |
| result.shortfall | חסרים {count} מטוסים לביצוע המשימה |
| result.suggestedBuffer | מרווח מומלץ לפי היסטוריה: {pct}% |

### 8.3 Reason templates (`reasonCodes.ts`) — params resolved to Hebrew names via `/reference`
| Code | Template |
|------|----------|
| AIRCRAFT_NOT_OPERATIONAL | המטוס אינו מבצעי (סטטוס: {status}) |
| ALREADY_ASSIGNED | המטוס כבר משובץ למשימה "{mission_name}" ({window_start}–{window_end}) |
| ENGINE_REQUIREMENT_NOT_MET | דרישת מנועים לא מתקיימת: נדרש {required}, למטוס {actual} מנועים |
| ENDURANCE_EXCEEDED | משך הטיסה כולל מרווח ({required_hours} ש׳) חורג מזמן השהייה המרבי באוויר ({max_hours} ש׳) |
| GROUNDING_MA_OPEN | פעולת תחזוקה מקרקעת פתוחה: {ma_number} ({zone}) |
| INSPECTION_EXPIRED | {inspection} — פג תוקף (חריגה של {over_by} {unit}) / פג תוקף ב-{due_at} |
| INSPECTION_DUE_BEFORE_MISSION_END | {inspection} תפוג במהלך המשימה: נותרו {remaining} {unit}, המשימה צורכת {required} / תפוג ב-{due_at}, לפני סיום המשימה ({mission_end}) |
| PERMIT_EXPIRED | תוקף {permit} פג ב-{expired_at} |
| PERMIT_EXPIRES_BEFORE_MISSION_END | תוקף {permit} יפוג ב-{expires_at}, לפני סיום המשימה ({mission_end}) |
| DEFERRAL_EXPIRED | תוקף הדחייה של {ma_number} פג |
| DEFERRAL_EXPIRES_BEFORE_MISSION_END | תוקף הדחייה של {ma_number} יפוג לפני סיום המשימה |
| QUALIFICATION_MISSING | למטוס אין כשירות {qualification} |
| QUALIFICATION_EXPIRED | תוקף כשירות {qualification} פג ב-{expired_at} |
| QUALIFICATION_EXPIRES_BEFORE_MISSION_END | תוקף כשירות {qualification} יפוג ב-{expires_at}, לפני סיום המשימה |
| QUALIFICATION_REVOKED_BY_MA | כשירות {qualification} אינה בתוקף עקב פעולת תחזוקה {ma_number} |
| QUALIFICATION_REVOKED_BY_PERMIT | כשירות {qualification} מוגבלת ב{permit}: {restriction} |
| PERMIT_RESTRICTION_CONFLICT | הגבלה ב{permit} ({restriction}) אינה מתאימה למשימת {mission_type} |
| IDLE_LIMIT_EXCEEDED | המטוס לא טס {idle_days} ימים (מגבלה: {limit_days}) — נדרשת בדיקה לאחר השבתה |
| DATA_MISSING | נתונים חסרים ({item}) — לא ניתן לאשר כשירות |
| DATA_STALE | הנתונים ממקור {source_name} אינם עדכניים (לפני {age_hours} שעות) |
| MANUALLY_EXCLUDED | הוחרג ידנית על ידי המתכנן |

### 8.4 Warning templates
| Code | Template |
|------|----------|
| LOW_MARGIN_AFTER_MISSION | מרווח נמוך: לאחר המשימה יוותרו {remaining_after} {unit} עד {ref} |
| EXPIRES_SOON_AFTER_MISSION | {ref} יפוג זמן קצר לאחר המשימה ({due_at}) |
| HIGH_DEFECT_RISK | סיכון גבוה לתקלה ({probability}%) |
| TOLERANCE_AVAILABLE | קיימת טולרנס של {tolerance} {unit} ל{inspection} — שימוש בה באישור גורם מוסמך בלבד |
| IDLE_CHECK_REQUIRED | נדרשת בדיקה לאחר השבתה לפני הטיסה ({idle_days} ימים ללא טיסה) |
| ML_MODEL_UNAVAILABLE | המודל "{model_name}" אינו זמין — הדירוג חושב ללא רכיב זה |

### 8.5 Unblock hints
| Code | Template |
|------|----------|
| UNBLOCK_MAX_DURATION | כשיר אם משך הטיסה יקוצר ל-{max_hours} שעות לכל היותר |
| UNBLOCK_MAX_LANDINGS | כשיר אם יבוצעו עד {max_landings_plus_tg} נחיתות (כולל גע-וסע) |
| UNBLOCK_LATEST_END | כשיר אם המשימה תסתיים עד {latest_end} |
| UNBLOCK_PERFORM_INSPECTION | ביצוע {inspection} יחזיר את המטוס לכשירות |
| UNBLOCK_RENEW_PERMIT | נדרש חידוש {permit} |
| UNBLOCK_RENEW_QUALIFICATION | נדרש חידוש כשירות {qualification} |
| UNBLOCK_CLOSE_MA | נדרשת סגירת פעולת תחזוקה {ma_number} |
| UNBLOCK_IDLE_CHECK | נדרש ביצוע בדיקה לאחר השבתה |

### 8.6 Errors, validation, disclaimer
| Key | Hebrew |
|-----|--------|
| err.UNAUTHENTICATED | פג תוקף ההתחברות — יש להתחבר מחדש |
| err.FORBIDDEN | אין לך הרשאה לבצע פעולה זו |
| err.NOT_FOUND | הפריט המבוקש לא נמצא |
| err.RUN_STALE | ההמלצה אינה עדכנית — יש לחשב מחדש |
| err.ELIGIBILITY_CHANGED | מצב הכשירות השתנה מאז החישוב — יש לחשב מחדש |
| err.FORCE_INCLUDE_INELIGIBLE | לא ניתן לכלול את {tail_number}: המטוס אינו כשיר למשימה |
| err.DATA_SOURCE_UNAVAILABLE | מקור הנתונים אינו זמין כרגע |
| err.RATE_LIMITED | בוצעו יותר מדי ניסיונות — נסו שוב בעוד מספר דקות |
| err.INTERNAL_ERROR | אירעה שגיאה. נסו שוב או פנו לתמיכה (מזהה: {request_id}) |
| val.FIELD_REQUIRED | שדה חובה |
| val.VALUE_TOO_SMALL | הערך קטן מהמינימום ({min}) |
| val.VALUE_TOO_LARGE | הערך גדול מהמקסימום ({max}) |
| val.START_IN_PAST | מועד ההמראה כבר עבר |
| val.TAIL_FORCED_BOTH_WAYS | לא ניתן לכלול ולהחריג את אותו מטוס |
| disclaimer | המערכת מספקת המלצה בלבד. ההחלטה הסופית על כשירות המטוס לטיסה ושחרורו לשירות נתונה לגורם המוסמך. |

Copy rules: plain, short, active voice; gender-neutral phrasing where possible; no English in UI except codes/tails;
Hebrew abbreviations use gershayim (`ש״ט`) not double quotes. Have a native Hebrew speaker review `he.ts` before demo.
