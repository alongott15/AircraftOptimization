"""Effective qualifications (OPTIMIZATION_ENGINE.md §2.6, BR-09)."""

from __future__ import annotations

from datetime import datetime

from app.domain.models import RESTRICTION_REVOKES, Aircraft
from app.domain.reason_codes import Reason, ReasonCode


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def permit_revocations(ac: Aircraft, now: datetime) -> dict[str, tuple[str, str]]:
    """qualification_code → (permit_type_code, restriction) for every currently valid permit."""
    out: dict[str, tuple[str, str]] = {}
    for p in sorted(ac.permits, key=lambda p: (p.permit_type_code, p.permit_id)):
        if not p.is_active or (p.expires_at is not None and p.expires_at <= now):
            continue
        for restriction in p.restrictions:
            qual = RESTRICTION_REVOKES.get(restriction)
            if qual is not None and qual not in out:
                out[qual] = (p.permit_type_code, restriction)
    return out


def ma_revocations(ac: Aircraft) -> dict[str, str]:
    """qualification_code → MA number of the open MA that revokes it."""
    out: dict[str, str] = {}
    for ma in ac.open_mas:
        if ma.affects_qualification_code and ma.affects_qualification_code not in out:
            out[ma.affects_qualification_code] = ma.ma_number
    return out


def qualification_reasons(
    ac: Aircraft, required: frozenset[str], now: datetime, start_at: datetime, deadline: datetime
) -> list[Reason]:
    """One reason per required qualification that is not effective, most specific first."""
    by_ma = ma_revocations(ac)
    by_permit = permit_revocations(ac, now)
    held = {q.qualification_code: q for q in ac.qualifications}
    reasons: list[Reason] = []
    for code in sorted(required):
        q = held.get(code)
        if code in by_ma:
            reasons.append(Reason(ReasonCode.QUALIFICATION_REVOKED_BY_MA,
                                  {"qualification_code": code, "ma_number": by_ma[code]}))
        elif code in by_permit:
            permit_type, restriction = by_permit[code]
            reasons.append(Reason(ReasonCode.QUALIFICATION_REVOKED_BY_PERMIT,
                                  {"qualification_code": code, "permit_type_code": permit_type,
                                   "restriction": restriction}))
        elif q is None or q.valid_from > start_at:
            reasons.append(Reason(ReasonCode.QUALIFICATION_MISSING, {"qualification_code": code}))
        elif q.valid_to is not None and q.valid_to <= now:
            reasons.append(Reason(ReasonCode.QUALIFICATION_EXPIRED,
                                  {"qualification_code": code, "expired_at": _iso(q.valid_to)}))
        elif q.valid_to is not None and q.valid_to < deadline:
            reasons.append(Reason(ReasonCode.QUALIFICATION_EXPIRES_BEFORE_MISSION_END,
                                  {"qualification_code": code, "expires_at": _iso(q.valid_to),
                                   "mission_end": _iso(deadline)}))
    return reasons


def effective_qualifications(ac: Aircraft, now: datetime) -> list[dict[str, object]]:
    """Per held qualification: effective flag + revoked_by — for the aircraft page."""
    by_ma = ma_revocations(ac)
    by_permit = permit_revocations(ac, now)
    out: list[dict[str, object]] = []
    for q in sorted(ac.qualifications, key=lambda q: q.qualification_code):
        revoked_by: dict[str, str] | None = None
        if q.qualification_code in by_ma:
            revoked_by = {"kind": "MA", "ref": by_ma[q.qualification_code]}
        elif q.qualification_code in by_permit:
            ptype, restriction = by_permit[q.qualification_code]
            revoked_by = {"kind": "PERMIT", "ref": ptype, "restriction": restriction}
        expired = q.valid_to is not None and q.valid_to <= now
        out.append({
            "qualification_code": q.qualification_code,
            "valid_to": _iso(q.valid_to) if q.valid_to else None,
            "effective": revoked_by is None and not expired,
            "revoked_by": revoked_by,
        })
    return out
