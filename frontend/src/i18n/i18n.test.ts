import { describe, expect, it } from 'vitest';

import { statusPalette } from '../theme/tokens';
import { formatDateTime, formatHours, localInputToUtc, parseDuration } from './format';
import { hasKey, interpolate, t, tk } from './index';
import { makeNames, REASON_TEMPLATES, renderReason } from './reasonCodes';

const ref = {
  inspection_types: [{ inspection_type_code: 'LG_INSP', name_he: 'בדיקת כני נסע', zone_code: 'GEAR_MAIN', limits: [] }],
  permit_types: [{ permit_type_code: 'INSURANCE', name_he: 'פוליסת ביטוח' }],
  qualifications: [{ qualification_code: 'IFR', name_he: 'טיסת מכשירים' }],
  activity_units: [{ unit_code: 'LDG', name_he: 'נחיתות', short_he: 'נח׳', kind: 'COUNTER' }],
} as unknown as Parameters<typeof makeNames>[0];

// Same list as backend/app/domain/reason_codes.py (backend test also checks this file).
const ALL_CODES = [
  'AIRCRAFT_NOT_OPERATIONAL', 'ALREADY_ASSIGNED', 'ENGINE_REQUIREMENT_NOT_MET', 'ENDURANCE_EXCEEDED', 'GROUNDING_MA_OPEN',
  'INSPECTION_EXPIRED', 'INSPECTION_DUE_BEFORE_MISSION_END', 'PERMIT_EXPIRED', 'PERMIT_EXPIRES_BEFORE_MISSION_END',
  'DEFERRAL_EXPIRED', 'DEFERRAL_EXPIRES_BEFORE_MISSION_END', 'QUALIFICATION_MISSING', 'QUALIFICATION_EXPIRED',
  'QUALIFICATION_EXPIRES_BEFORE_MISSION_END', 'QUALIFICATION_REVOKED_BY_MA', 'QUALIFICATION_REVOKED_BY_PERMIT',
  'PERMIT_RESTRICTION_CONFLICT', 'IDLE_LIMIT_EXCEEDED', 'DATA_MISSING', 'DATA_STALE', 'MANUALLY_EXCLUDED',
  'JEOPARDIZES_LATER_ASSIGNMENT', 'NON_GROUNDING_PERMIT_INVALID', 'EXPIRES_WITHIN_WINDOW',
  'LOW_MARGIN_AFTER_MISSION', 'EXPIRES_SOON_AFTER_MISSION', 'HIGH_DEFECT_RISK', 'TOLERANCE_AVAILABLE',
  'IDLE_CHECK_REQUIRED', 'ML_MODEL_UNAVAILABLE', 'UNBLOCK_MAX_DURATION', 'UNBLOCK_MAX_LANDINGS', 'UNBLOCK_LATEST_END',
  'UNBLOCK_PERFORM_INSPECTION', 'UNBLOCK_RENEW_PERMIT', 'UNBLOCK_RENEW_QUALIFICATION', 'UNBLOCK_CLOSE_MA',
  'UNBLOCK_IDLE_CHECK',
];

describe('i18n', () => {
  it('translates and interpolates', () => {
    expect(t('banner.expired', { count: 4 })).toBe('מטוסים עם פריטים שפג תוקפם: 4');
    expect(interpolate('{a}-{b}', { a: 1 })).toBe('1-');
    expect(tk('status.NOPE', undefined, 'fallback')).toBe('fallback');
    expect(hasKey('status.EXPIRED')).toBe(true);
  });

  it('has a Hebrew template for every backend code', () => {
    const missing = ALL_CODES.filter((c) => !(c in REASON_TEMPLATES));
    expect(missing).toEqual([]);
  });

  it('renders reasons with names resolved from reference data', () => {
    const names = makeNames(ref);
    const text = renderReason({
      code: 'INSPECTION_DUE_BEFORE_MISSION_END',
      params: { inspection_code: 'LG_INSP', unit: 'LDG', remaining: 5, required: 8 },
    }, names);
    expect(text).toBe('בדיקת כני נסע תפוג במהלך המשימה: נותרו 5 נח׳, המשימה צורכת 8');
    expect(renderReason({ code: 'UNBLOCK_MAX_LANDINGS', params: { max_landings_plus_tg: 4 } }, names))
      .toBe('כשיר אם יבוצעו עד 4 נחיתות (כולל גע-וסע)');
    expect(renderReason({ code: 'UNKNOWN_CODE', params: {} }, names)).toBe('UNKNOWN_CODE');
  });

  it('renders fail-safe data reasons and later-assignment conflicts in Hebrew', () => {
    const names = makeNames(ref);
    expect(renderReason({ code: 'DATA_STALE', params: { source_name: 'permits', age_hours: null } }, names))
      .toBe('לא התקבלו נתונים ממקור מערכת ההיתרים — לא ניתן לאשר כשירות');
    expect(renderReason({ code: 'DATA_STALE', params: { source_name: 'usage_counter', unit: 'LDG', age_hours: 72 } },
      names)).toBe('הנתונים ממקור מוני השימוש (נח׳) אינם עדכניים (לפני 72 שעות)');
    expect(renderReason({ code: 'DATA_STALE', params: { source_name: 'last_flight', age_hours: -5 } }, names))
      .toBe('חותמת הזמן ממקור מועד הטיסה האחרונה היא בעתיד (5 שעות) — לא ניתן לאשר כשירות');
    expect(names.ref('ASSIGNMENT', 'ניווט')).toBe('שיבוץ למשימה "ניווט" — המטוס אינו כשיר עוד, יש לבדוק מחדש');
    expect(renderReason({
      code: 'EXPIRES_WITHIN_WINDOW',
      params: { source: 'INSPECTION', ref: 'LG_INSP', unit: 'LDG', margin: 3, within: 0, until: 5 },
    }, names)).toBe('תוקף בדיקת כני נסע יפוג בטווח "תוך 0 ועד 5 נח׳": נותרו 3 נח׳ לאחר המשימה');
    const later = renderReason({
      code: 'JEOPARDIZES_LATER_ASSIGNMENT',
      params: { mission_id: 9, mission_name: 'ניווט', source: 'INSPECTION', ref: 'LG_INSP', unit: 'LDG', over_by: 2 },
    }, names);
    expect(later).toBe('שיבוץ זה יגרום לחריגה בבדיקת כני נסע (2 נח׳) עד סיום המשימה המשובצת "ניווט"');
    expect(renderReason({ code: 'NON_GROUNDING_PERMIT_INVALID', params: { permit_type_code: 'INSURANCE', outcome: 'EXPIRED' } },
      names)).toBe('תוקף פוליסת ביטוח פג (היתר שאינו מקרקע — לבדיקת גורם מוסמך)');
  });
});

describe('format', () => {
  it('formats in Asia/Jerusalem across the DST change', () => {
    expect(formatDateTime('2026-10-24T22:30:00Z')).toBe('25/10/2026 01:30'); // IDT, UTC+3
    expect(formatDateTime('2026-10-25T22:30:00Z')).toBe('26/10/2026 00:30'); // IST, UTC+2
    expect(formatDateTime(null)).toBe('—');
  });

  it('converts local Israel input to UTC', () => {
    expect(localInputToUtc('2026-01-15T10:00')).toBe('2026-01-15T08:00:00Z');
  });

  it('parses and formats durations', () => {
    expect(parseDuration('2:30')).toBe(2.5);
    expect(parseDuration('2.5')).toBe(2.5);
    expect(parseDuration('2,5')).toBe(2.5);
    expect(parseDuration('abc')).toBeNull();
    expect(formatHours(1.2)).toBe('1:12');
  });
});

function luminance(hex: string): number {
  const v = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * (v[0] ?? 0) + 0.7152 * (v[1] ?? 0) + 0.0722 * (v[2] ?? 0);
}

describe('tokens', () => {
  it('every status fg/bg pair meets WCAG AA (4.5:1) in light and dark', () => {
    for (const [tone, p] of Object.entries(statusPalette)) {
      for (const [fg, bg] of [[p.fgLight, p.bgLight], [p.fgDark, p.bgDark]] as const) {
        const [a, b] = [luminance(fg), luminance(bg)].sort((x, y) => y - x) as [number, number];
        const ratio = (a + 0.05) / (b + 0.05);
        expect(ratio, `${tone} ${fg}/${bg}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});
