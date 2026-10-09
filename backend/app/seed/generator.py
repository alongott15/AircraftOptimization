"""Deterministic example-DB generator (DATABASE.md §6).

Usage:  python -m app.seed.generator --anchor now --seed 42 --out data/poc.sqlite

Steps: reference data → fleet → 18 months of simulated flights/defects (planted ML signal) → counters →
healthy due state → demo scenarios SC-01…SC-16 (applied last, so they always hold) → app store.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from argon2 import PasswordHasher
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.clock import parse_utc
from app.config import REPO_ROOT, get_settings
from app.db import orm
from app.db.session import make_engine
from app.domain.policy import WEIGHT_PRESETS, PlanningPolicy
from app.seed import reference as ref

HISTORY_DAYS = 548
BASE_HAZARD_PER_FH = 0.008
RELIABILITY_SIGMA = 0.7


@dataclass(frozen=True)
class TypeProfile:
    fly_prob: float
    median_hours: float
    mission_mix: tuple[tuple[str, float], ...]
    fh_start: tuple[float, float]
    tg_ratio_start: float


PROFILES = {
    "SE-100": TypeProfile(0.55, 1.4, (("TRAINING", 0.9), ("OTHER", 0.1)), (800, 6000), 1.6),
    "TP-200": TypeProfile(0.45, 2.2, (("TRANSPORT", 0.6), ("TRAINING", 0.3), ("FERRY", 0.1)), (2000, 12000), 0.3),
    "JT-300": TypeProfile(0.35, 3.5, (("TRANSPORT", 0.8), ("FERRY", 0.2)), (3000, 15000), 0.0),
}

FLEET: list[tuple[str, str, str]] = (
    [(f"4X-S0{i}", "SE-100", "NTH" if i <= 4 else "CEN") for i in range(1, 9)]
    + [(f"4X-P{i:02d}", "TP-200", "CEN" if i <= 5 else "STH") for i in range(1, 11)]
    + [(f"4X-J0{i}", "JT-300", "CEN") for i in range(1, 7)]
)


@dataclass
class SimState:
    fh: float
    ldg: float
    tg: float
    cyc: float
    eng: float
    fh_since_major: float
    grounded_until: datetime | None = None
    flights: list[orm.FlightLogRow] = field(default_factory=list)
    mas: list[orm.MaintenanceActionRow] = field(default_factory=list)
    events: list[tuple[datetime, float]] = field(default_factory=list)


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _pick(rng: random.Random, options: tuple[tuple[str, float], ...]) -> str:
    r, acc = rng.random(), 0.0
    for value, weight in options:
        acc += weight
        if r <= acc:
            return value
    return options[-1][0]


class Generator:
    def __init__(self, session: Session, anchor: datetime, seed: int, demo_password: str) -> None:
        self.s = session
        self.t0 = anchor
        self.rng = random.Random(seed)
        self.demo_password = demo_password
        self.ma_seq = 1000  # scenario MAs use fixed numbers below 1000 (MA-2026-00398 / 00412)
        self.flight_seq = 0
        self.permit_seq = 0

    # ── helpers ──────────────────────────────────────────────────────────
    def _ma_number(self) -> str:
        self.ma_seq += 1
        return f"MA-{self.t0.year}-{self.ma_seq:05d}"

    def _add(self, *rows: object) -> None:
        self.s.add_all(list(rows))

    # ── 1. reference ─────────────────────────────────────────────────────
    def reference(self) -> None:
        self._add(*[orm.AircraftTypeRow(type_code=t, name_he=n, engine_count=e, max_endurance_hours=h,
                                        idle_limit_days=i, model_shape=m) for t, n, e, h, i, m in ref.AIRCRAFT_TYPES])
        self._add(*[orm.BaseRow(base_code=c, name_he=n) for c, n in ref.BASES])
        self._add(*[orm.ActivityUnitRow(unit_code=c, name_he=n, short_he=s, kind=k)
                    for c, n, s, k in ref.ACTIVITY_UNITS])
        self._add(*[orm.QualificationRow(qualification_code=c, name_he=n) for c, n in ref.QUALIFICATIONS])
        self._add(*[orm.ZoneRow(zone_code=c, name_he=n) for c, n in ref.ZONES])
        self.s.flush()
        self._add(*[orm.AtaZoneMapRow(ata_from=a, ata_to=b, zone_code=z) for a, b, z in ref.ATA_ZONE_MAP])
        self._add(*[orm.InspectionTypeRow(inspection_type_code=c, name_he=n, zone_code=z, is_grounding_if_overdue=True)
                    for c, n, z in ref.INSPECTION_TYPES])
        self.s.flush()
        self._add(*[orm.InspectionLimitRow(inspection_type_code=c, type_code=t, unit_code=u, interval_value=i,
                                           tolerance_value=tol, tg_weight=w)
                    for c, t, u, i, tol, w in ref.INSPECTION_LIMITS])
        self._add(*[orm.PermitTypeRow(permit_type_code=c, name_he=n, is_grounding=g, is_required=r)
                    for c, n, g, r in ref.PERMIT_TYPES])
        self.s.flush()

    # ── 2–3. fleet + simulation ─────────────────────────────────────────
    def fleet(self) -> None:
        types = {t[0]: t for t in ref.AIRCRAFT_TYPES}
        for idx, (tail, type_code, base) in enumerate(FLEET, start=1):
            age_years = self.rng.uniform(3, 18)
            row = orm.AircraftRow(
                aircraft_id=idx, tail_number=tail, serial_number=f"{type_code.replace('-', '')}-{idx:04d}",
                type_code=type_code, base_code=base, status="OPERATIONAL",
                manufacture_date=(self.t0 - timedelta(days=365 * age_years)).date(),
                last_flight_at=None, source_updated_at=self.t0)
            self.s.add(row)
            self.s.flush()
            engines = types[type_code][2]
            reliability = math.exp(self.rng.gauss(0, RELIABILITY_SIGMA))
            if tail == "4X-S02":
                reliability = math.exp(RELIABILITY_SIGMA * 1.645)  # 95th percentile — SC-12
            state = self._simulate(row, engines, reliability)
            self._finalize_aircraft(row, engines, state)

    def _simulate(self, ac: orm.AircraftRow, engines: int, reliability: float) -> SimState:
        prof = PROFILES[ac.type_code]
        major_interval = ref.MAJOR_INSPECTION[ac.type_code][1]
        fh0 = self.rng.uniform(*prof.fh_start)
        ldg0 = fh0 * self.rng.uniform(0.5, 0.8)
        st = SimState(fh=fh0, ldg=ldg0, tg=fh0 * prof.tg_ratio_start, cyc=ldg0, eng=ldg0 * engines,
                      fh_since_major=self.rng.uniform(0, major_interval * 0.8))
        start = self.t0 - timedelta(days=HISTORY_DAYS)
        st.events.append((start, fh0 - st.fh_since_major))  # major inspection before the history window
        idle_cutoff =self.t0 - timedelta(days=40) if ac.tail_number == "4X-S07" else None
        endurance = {t[0]: t[3] for t in ref.AIRCRAFT_TYPES}[ac.type_code]
        for d in range(HISTORY_DAYS):
            day = start + timedelta(days=d)
            if idle_cutoff and day >= idle_cutoff - timedelta(days=1):
                break
            if st.grounded_until and day < st.grounded_until:
                continue
            if self.rng.random() >= prof.fly_prob:
                continue
            sorties = self.rng.choice([1, 1, 2, 3])
            takeoff = day + timedelta(hours=5 + self.rng.random() * 2)
            for _ in range(sorties):
                mission_type = _pick(self.rng, prof.mission_mix)
                hours = min(max(self.rng.lognormvariate(math.log(prof.median_hours), 0.35), 0.4), endurance * 0.9)
                landing = takeoff + timedelta(hours=hours)
                if landing >= self.t0 - timedelta(hours=1):
                    break
                self._fly(ac, st, engines, reliability, mission_type, takeoff, hours, major_interval)
                takeoff = landing + timedelta(hours=1 + self.rng.random() * 2)
                if st.grounded_until and st.grounded_until > takeoff:
                    break
        return st

    def _fly(self, ac: orm.AircraftRow, st: SimState, engines: int, reliability: float, mission_type: str,
             takeoff: datetime, hours: float, major_interval: float) -> None:
        ratio = self.rng.gauss(1.12, 0.10) if mission_type == "FERRY" else self.rng.gauss(1.05, 0.08)
        landings = 1 + (1 if self.rng.random() < 0.15 else 0)
        tg = _poisson(self.rng, 5) if mission_type == "TRAINING" else 0
        landing = takeoff + timedelta(hours=hours)
        self.flight_seq += 1
        st.flights.append(orm.FlightLogRow(
            flight_id=self.flight_seq, aircraft_id=ac.aircraft_id, mission_type=mission_type,
            planned_duration_hours=round(hours / max(ratio, 0.7), 2), takeoff_at=takeoff, landing_at=landing,
            flight_hours=round(hours, 2), landings=landings, touch_and_goes=tg, engine_starts=1,
            source_updated_at=self.t0))
        st.fh += hours
        st.ldg += landings
        st.tg += tg
        st.cyc += landings
        st.eng += engines
        st.fh_since_major += hours
        if st.fh_since_major >= major_interval:
            st.events.append((landing, st.fh))
            st.fh_since_major = 0.0
        # Planted defect signal (DATABASE.md §6.3)
        open_deferred = sum(1 for m in st.mas if m.severity == "DEFERRED" and m.closed_at and m.closed_at > landing)
        hazard = (BASE_HAZARD_PER_FH * reliability * (1 + st.fh_since_major / 250) * (1 + 0.4 * open_deferred)
                  * (1 + 0.02 * tg / hours))
        if self.rng.random() < 1 - math.exp(-hazard * hours):
            self._defect(ac, st, engines, landing)

    def _defect(self, ac: orm.AircraftRow, st: SimState, engines: int, at: datetime,
                severity: str | None = None) -> orm.MaintenanceActionRow:
        catalogue = [d for d in ref.DEFECT_CATALOGUE if engines > 1 or not d[3]]
        ata, zone, title, _ = self.rng.choice(catalogue)
        r = self.rng.random()
        severity = severity or ("GROUNDING" if r < 0.2 else "DEFERRED" if r < 0.7 else "MONITOR")
        days = {"GROUNDING": (1, 5), "DEFERRED": (3, 20), "MONITOR": (5, 30)}[severity]
        closed = at + timedelta(days=self.rng.uniform(*days))
        ma = orm.MaintenanceActionRow(
            ma_number=self._ma_number(), aircraft_id=ac.aircraft_id, title=title, description=None, ata_chapter=ata,
            zone_code=zone, severity=severity, mel_category="C" if severity == "DEFERRED" else None, status="CLOSED",
            opened_at=at, closed_at=closed,
            deferral_expires_at=at + timedelta(days=days[1]) if severity == "DEFERRED" else None,
            source_updated_at=self.t0)
        st.mas.append(ma)
        if severity == "GROUNDING":
            st.grounded_until = closed
        return ma

    # ── 4–5. counters, due state, permits, qualifications ───────────────
    def _finalize_aircraft(self, ac: orm.AircraftRow, engines: int, st: SimState) -> None:
        t0 = self.t0
        # Close everything still open at T0 — scenarios add explicit open MAs later.
        for ma in st.mas:
            if ma.closed_at is not None and ma.closed_at >= t0:
                ma.closed_at = t0 - timedelta(hours=1)
        # Healthy aircraft flew within the last 1–6 days (S07 is the idle scenario).
        last = max((f.landing_at for f in st.flights), default=None)
        if ac.tail_number == "4X-S07":
            takeoff = t0 - timedelta(days=40, hours=1.2)
            self._fly(ac, st, engines, 0.0, "TRAINING", takeoff, 1.2, 1e9)
        elif last is None or t0 - last > timedelta(days=6):
            takeoff = t0 - timedelta(days=self.rng.uniform(1, 5))
            self._fly(ac, st, engines, 0.0, "OTHER", takeoff, 1.0, 1e9)
        ac.last_flight_at = max(f.landing_at for f in st.flights)
        self._add(*st.flights, *st.mas)

        counters = {"FH": st.fh, "LDG": st.ldg, "TG": st.tg, "CYC": st.cyc, "ENG_START": st.eng}
        for unit, value in counters.items():
            self.s.add(orm.UsageCounterRow(aircraft_id=ac.aircraft_id, unit_code=unit, value=round(value, 2),
                                           as_of=t0, source_updated_at=t0))
        days_span = HISTORY_DAYS
        rates = {u: max(v / days_span, 0.01) for u, v in counters.items()}
        # Major inspection: due state follows the simulated maintenance history (keeps ML features consistent).
        # If the simulated aircraft is too close to its due point to count as healthy, it was inspected early,
        # at its last landing — after every labelled training window, so no feature/label is affected.
        major_code, major_interval = ref.MAJOR_INSPECTION[ac.type_code]
        if major_interval - st.fh_since_major < 0.25 * major_interval:
            st.events.append((ac.last_flight_at, st.fh))
            st.fh_since_major = 0.0
        major_due = round(st.fh - st.fh_since_major + major_interval, 2)
        for at, fh_at in st.events:
            self.s.add(orm.InspectionEventRow(aircraft_id=ac.aircraft_id, inspection_type_code=major_code,
                                              performed_at=at, fh_at=round(fh_at, 2), source_updated_at=t0))
        for code, type_code, unit, interval, _tol, tg_weight in ref.INSPECTION_LIMITS:
            if type_code != ac.type_code or unit == "IDLE_DAY":
                continue
            if unit == "CAL_DAY":
                due_at = t0 + timedelta(days=self.rng.uniform(30, min(300, 0.9 * interval)))
                self.s.add(orm.AircraftInspectionDueRow(
                    aircraft_id=ac.aircraft_id, inspection_type_code=code, unit_code=unit,
                    last_done_at=due_at - timedelta(days=interval), due_at=due_at, source_updated_at=t0))
                continue
            current = counters[unit] + (tg_weight * counters["TG"] if unit in ("LDG", "CYC") else 0.0)
            remaining = self.rng.uniform(0.25, 0.9) * interval
            due_value = round(current + remaining, 2)
            if code == major_code and unit == "FH":
                due_value = major_due
                remaining = due_value - current
            used = interval - remaining
            self.s.add(orm.AircraftInspectionDueRow(
                aircraft_id=ac.aircraft_id, inspection_type_code=code, unit_code=unit,
                last_done_value=round(due_value - interval, 2),
                last_done_at=t0 - timedelta(days=min(used / rates[unit], days_span)),
                due_value=due_value, source_updated_at=t0))
        for code, _n, _g, required in ref.PERMIT_TYPES:
            if required:
                self._permit(ac.aircraft_id, code, t0 + timedelta(days=self.rng.uniform(60, 365)))
        for qual in ref.TYPE_QUALIFICATIONS[ac.type_code]:
            self.s.add(orm.AircraftQualificationRow(aircraft_id=ac.aircraft_id, qualification_code=qual,
                                                    valid_from=t0 - timedelta(days=1000), valid_to=None,
                                                    source_updated_at=t0))
        self.s.flush()

    def _permit(self, aircraft_id: int, code: str, expires_at: datetime | None,
                restrictions: str | None = None) -> orm.AircraftPermitRow:
        self.permit_seq += 1
        row = orm.AircraftPermitRow(
            permit_id=self.permit_seq, aircraft_id=aircraft_id, permit_type_code=code,
            permit_number=f"{code}-{aircraft_id:03d}-{self.permit_seq:04d}",
            issued_at=self.t0 - timedelta(days=self.rng.uniform(30, 300)), expires_at=expires_at,
            restrictions=restrictions, status="ACTIVE", source_updated_at=self.t0)
        self.s.add(row)
        return row

    # ── 6. demo scenarios (DATABASE.md §6.2) ────────────────────────────
    def scenarios(self) -> None:
        t0, s = self.t0, self.s
        by_tail = {a.tail_number: a for a in s.query(orm.AircraftRow).all()}

        def aid(tail: str) -> int:
            return by_tail[tail].aircraft_id

        def due(tail: str, code: str, unit: str) -> orm.AircraftInspectionDueRow:
            row = s.get(orm.AircraftInspectionDueRow, (aid(tail), code, unit))
            assert row is not None, (tail, code, unit)
            return row

        def counter(tail: str, unit: str) -> float:
            row = s.get(orm.UsageCounterRow, (aid(tail), unit))
            assert row is not None
            return row.value

        def permit(tail: str, code: str) -> orm.AircraftPermitRow:
            return s.query(orm.AircraftPermitRow).filter_by(aircraft_id=aid(tail), permit_type_code=code).one()

        def open_ma(tail: str, title: str, ata: int, zone: str, severity: str, opened_days_ago: float,
                    **extra: object) -> None:
            s.add(orm.MaintenanceActionRow(
                ma_number=extra.pop("ma_number", None) or self._ma_number(), aircraft_id=aid(tail), title=title,
                ata_chapter=ata, zone_code=zone, severity=severity, status="OPEN",
                opened_at=t0 - timedelta(days=opened_days_ago), source_updated_at=t0, **extra))

        due("4X-S03", "ANNUAL", "CAL_DAY").due_at = t0 - timedelta(days=1)                      # SC-01
        permit("4X-P05", "COA").expires_at = t0 - timedelta(days=3)                             # SC-02
        due("4X-P02", "I100H", "FH").due_value = round(counter("4X-P02", "FH") + 1.5, 2)        # SC-03
        lg = counter("4X-S05", "LDG") + 1.0 * counter("4X-S05", "TG")                          # SC-04
        due("4X-S05", "LG_INSP", "LDG").due_value = round(lg + 5, 2)
        # SC-05 (4X-S07 idle 40 days) is produced by the simulation cut-off.
        open_ma("4X-J04", "דליפת שמן במנוע מס׳ 2", 79, "ENGINE_2", "GROUNDING", 2,              # SC-06
                ma_number="MA-2026-00412", description="דליפת שמן נצפתה בבדיקה לפני טיסה")
        open_ma("4X-P08", "מכ״ם מזג אוויר אינו תקין", 34, "NOSE", "DEFERRED", 4,                 # SC-07
                ma_number="MA-2026-00398", mel_category="C", deferral_expires_at=t0 + timedelta(days=6),
                affects_qualification_code="IFR")
        permit("4X-J06", "INSURANCE").expires_at = t0 + timedelta(hours=5)                      # SC-08
        sp = self._permit(aid("4X-P09"), "SPECIAL_FLIGHT", t0 + timedelta(days=20), "DAY_ONLY,NO_PAX")  # SC-09
        sp.issued_at = t0 - timedelta(days=10)
        q = s.get(orm.AircraftQualificationRow, (aid("4X-J02"), "OVERWATER"))                   # SC-10
        assert q is not None
        q.valid_to = t0 - timedelta(days=10)
        s.execute(delete(orm.UsageCounterRow).where(                                             # SC-11
            orm.UsageCounterRow.aircraft_id == aid("4X-P04"), orm.UsageCounterRow.unit_code == "LDG"))
        # SC-12: high defect risk — 4 recent defects (closed) + 3 open non-qualification MAs
        for days_ago, severity in ((12, "DEFERRED"), (25, "MONITOR"), (33, "DEFERRED"), (51, "DEFERRED"),
                                   (64, "MONITOR"), (77, "DEFERRED"), (120, "GROUNDING")):
            s.add(orm.MaintenanceActionRow(
                ma_number=self._ma_number(), aircraft_id=aid("4X-S02"), title="רעידות במדחף", ata_chapter=61,
                zone_code="ENGINE_1", severity=severity, mel_category="C" if severity == "DEFERRED" else None,
                status="CLOSED", opened_at=t0 - timedelta(days=days_ago),
                closed_at=t0 - timedelta(days=days_ago - 4), source_updated_at=t0))
        open_ma("4X-S02", "נורית תאורה פנימית אינה פועלת", 33, "FUSELAGE", "MONITOR", 6)
        open_ma("4X-S02", "שחיקת צמיג ראשי", 32, "GEAR_MAIN", "DEFERRED", 5, mel_category="C",
                deferral_expires_at=t0 + timedelta(days=35))
        open_ma("4X-S02", "מחוון גובה מציג סטייה", 31, "COCKPIT", "DEFERRED", 3, mel_category="C",
                deferral_expires_at=t0 + timedelta(days=45))
        due("4X-P01", "A_CHECK", "FH").due_value = round(counter("4X-P01", "FH") + 5.0, 2)      # SC-13a
        due("4X-P03", "A_CHECK", "FH").due_value = round(counter("4X-P03", "FH") + 4.4, 2)      # SC-13b
        by_tail["4X-J03"].status = "IN_MAINTENANCE"                                             # SC-14
        due("4X-P06", "ANNUAL", "CAL_DAY").due_at = t0 + timedelta(days=3)                      # SC-16
        # A few harmless open items so the 3D viewer has something to show on healthy aircraft.
        open_ma("4X-P07", "סדק בצבע במייצב האופקי", 55, "TAIL", "MONITOR", 9)
        open_ma("4X-J01", "נורית תאורה פנימית אינה פועלת", 33, "FUSELAGE", "DEFERRED", 2, mel_category="D",
                deferral_expires_at=t0 + timedelta(days=40))
        open_ma("4X-P10", "דליפת דלק קלה בכנף ימין", 28, "WING_R", "MONITOR", 1)
        s.flush()

    # ── 7. app store ─────────────────────────────────────────────────────
    def app_store(self) -> None:
        t0, s = self.t0, self.s
        hasher = PasswordHasher()
        users = [
            (1, "admin", "מנהל מערכת", "ADMIN"),
            (2, "planner", "מתכנן משימות", "PLANNER"),
            (3, "maint", "בקר תחזוקה", "MAINTENANCE_CONTROLLER"),
            (4, "ops", "מנהל תפעול", "VIEWER"),
        ]
        for uid, username, display, role in users:
            s.add(orm.AppUserRow(user_id=uid, username=username, display_name=display, role=role, is_active=True,
                                 password_hash=hasher.hash(self.demo_password), created_at=t0))
        for key, value in PlanningPolicy().to_dict().items():
            s.add(orm.PlanningPolicyRow(policy_key=key, value_json=json.dumps(value), updated_by=None, updated_at=t0))
        for code, weights in WEIGHT_PRESETS.items():
            s.add(orm.WeightPresetRow(preset_code=code, weights_json=json.dumps(dict(weights))))
        for base, _ in ref.BASES:
            for w in range(9):
                iso = (t0 + timedelta(weeks=w)).isocalendar()
                s.add(orm.HangarCapacityRow(base_code=base, iso_week=f"{iso.year}-W{iso.week:02d}", slots=2))
        s.flush()
        # SC-15: the existing mission is assigned to 4X-S08, window T0+1h → T0+4h
        # (1.7 h + 0.3 h floor + 1 h turnaround)
        mission = orm.MissionRow(
            mission_id=1, name="אימון בוקר — משימה קיימת", mission_type="TRAINING", status="ASSIGNED",
            required_aircraft_count=1, start_at=t0 + timedelta(hours=1), est_duration_hours=1.7, est_landings=1,
            est_touch_and_goes=4, est_engine_starts=1, departure_base_code="CEN", engine_requirement="SINGLE",
            same_type_required=False, priority=3, notes=None, overrides_json=None, created_by=1, created_at=t0,
            updated_at=t0)
        s.add(mission)
        s.flush()
        s08 = s.query(orm.AircraftRow).filter_by(tail_number="4X-S08").one()
        s.add(orm.MissionAssignmentRow(
            mission_id=1, aircraft_id=s08.aircraft_id, run_id=None, set_index=None,
            window_start=t0 + timedelta(hours=1), window_end=t0 + timedelta(hours=4), status="ACTIVE",
            assigned_by=1, assigned_at=t0))
        for source in ("mro", "flight_ops", "permits"):
            s.add(orm.DataLoadStatusRow(source_name=source, loaded_at=t0))
        s.flush()


def generate(out: Path, anchor: datetime, seed: int, demo_password: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(out) + suffix)
        if p.exists():
            p.unlink()
    engine = make_engine(f"sqlite:///{out}")
    for metadata in orm.ALL_METADATA:
        metadata.create_all(engine)
    _stamp_alembic_head(f"sqlite:///{out}")
    with Session(engine) as session:
        gen = Generator(session, anchor, seed, demo_password)
        gen.reference()
        gen.fleet()
        gen.scenarios()
        gen.app_store()
        session.commit()
    engine.dispose()


def _stamp_alembic_head(url: str) -> None:
    """Mark the freshly created app store as up to date so later Alembic migrations apply on top of it."""
    from alembic import command
    from alembic.config import Config

    backend = Path(__file__).resolve().parents[2]
    ini = backend / "alembic.ini"
    cfg = Config(str(ini)) if ini.exists() else Config()
    # Absolute, so stamping works from any working directory and in images without alembic.ini.
    cfg.set_main_option("script_location", str(backend / "app" / "db" / "migrations"))
    cfg.attributes["url"] = url
    command.stamp(cfg, "head")


def _floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the POC example database")
    parser.add_argument("--anchor", default="now", help="ISO-8601 UTC anchor (T0) or 'now'")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "poc.sqlite"))
    args = parser.parse_args()
    anchor = datetime.now(tz=UTC) if args.anchor == "now" else parse_utc(args.anchor)
    password = get_settings().demo_password or os.environ.get("APP_DEMO_PASSWORD")
    if not password:
        raise SystemExit("APP_DEMO_PASSWORD is not set (copy .env.example to .env)")
    generate(Path(args.out), _floor_hour(anchor), args.seed, password)
    print(f"Seeded {args.out} with anchor {_floor_hour(anchor).isoformat()}")


if __name__ == "__main__":
    main()
