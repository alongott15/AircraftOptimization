/**
 * Hebrew templates for every backend reason / warning / unblock code (API.md §5, DESIGN_SYSTEM.md §8.3–8.5).
 * Backend contract test checks that every code appears here as `CODE:`.
 */
import type { Reference } from '../api/types';
import { formatDateTime, formatHours, formatNumber } from './format';
import { t, tk } from './index';

export type ReasonParams = Record<string, string | number | null | undefined>;

export interface Names {
  inspection: (code: unknown) => string;
  permit: (code: unknown) => string;
  qualification: (code: unknown) => string;
  unit: (code: unknown) => string;
  zone: (code: unknown) => string;
  type: (code: unknown) => string;
  squadron: (code: unknown) => string;
  ref: (source: unknown, ref: unknown) => string;
}

const s = (v: unknown): string => (v === null || v === undefined ? '' : String(v));
const n = (v: unknown, digits = 1): string => (typeof v === 'number' ? formatNumber(v, digits) : s(v));
/** Left-to-right isolate (U+2066…U+2069) so dates, codes and numbers keep their order inside Hebrew text. */
export const ltr = (v: string): string => `\u2066${v}\u2069`;
const d = (v: unknown): string => ltr(typeof v === 'string' ? formatDateTime(v) : s(v));

export function makeNames(ref?: Reference): Names {
  const map = <T extends Record<string, unknown>>(rows: T[] | undefined, key: keyof T, label: keyof T) =>
    new Map((rows ?? []).map((r) => [String(r[key]), String(r[label])]));
  const inspections = map(ref?.inspection_types, 'inspection_type_code', 'name_he');
  const permits = map(ref?.permit_types, 'permit_type_code', 'name_he');
  const quals = map(ref?.qualifications, 'qualification_code', 'name_he');
  const units = map(ref?.activity_units, 'unit_code', 'short_he');
  const types = map(ref?.aircraft_types, 'type_code', 'name_he');
  const squadrons = map(ref?.squadrons, 'squadron_code', 'name_he');
  const names: Names = {
    type: (c) => types.get(s(c)) ?? s(c),
    squadron: (c) => squadrons.get(s(c)) ?? s(c),
    inspection: (c) => inspections.get(s(c)) ?? s(c),
    permit: (c) => permits.get(s(c)) ?? s(c),
    qualification: (c) => quals.get(s(c)) ?? s(c),
    unit: (c) => units.get(s(c)) ?? s(c),
    zone: (c) => tk(`zone.${s(c)}`, undefined, s(c)),
    ref: (source, r) => {
      switch (s(source)) {
        case 'INSPECTION':
          return names.inspection(r);
        case 'PERMIT':
          return names.permit(r);
        case 'QUALIFICATION':
          return `כשירות ${names.qualification(r)}`;
        case 'DEFERRAL':
          return `דחיית תקלה ${ltr(s(r))}`;
        case 'IDLE':
          return 'בדיקה לאחר השבתה';
        case 'MAINTENANCE_ACTION':
          return `פעולת תחזוקה מקרקעת ${ltr(s(r))}`;
        case 'ASSIGNMENT':
          return `שיבוץ למשימה "${s(r)}" — המטוס אינו כשיר עוד, יש לבדוק מחדש`;
        case 'AIRCRAFT':
          return `נתוני המטוס (${ltr(s(r))})`;
        default:
          return s(r);
      }
    },
  };
  return names;
}

/** What is missing, for DATA_MISSING reasons and FIX_DATA maintenance tasks. */
export function missingItemName(kind: unknown, code: unknown, nm: Names): string {
  switch (s(kind)) {
    case 'COUNTER':
      return `מונה ${nm.unit(code)}`;
    case 'PERMIT':
      return nm.permit(code);
    case 'INSPECTION':
      return nm.inspection(code);
    case 'LAST_FLIGHT':
      return 'מועד טיסה אחרונה';
    case 'DEFERRAL':
      return `דחיית תקלה ${ltr(s(code))}`;
    case 'INSPECTION_LIMIT':
      return `הגדרת מגבלות ${nm.inspection(code)}`;
    case 'AIRCRAFT_TYPE':
      return `נתוני דגם ${ltr(s(code))}`;
    case 'AIRCRAFT_STATUS':
      return 'סטטוס מבצעי';
    case 'MAINTENANCE_ACTION':
      return `פעולת תחזוקה ${ltr(s(code))}`;
    default:
      return s(code);
  }
}

/** Title of a maintenance task (FR-90) from its action code and reference — e.g. "ביצוע בדיקה שנתית". */
export function taskTitle(action: string, ref: string, nm: Names): string {
  const name = (() => {
    switch (action) {
      case 'PERFORM_INSPECTION':
        return nm.inspection(ref);
      case 'RENEW_PERMIT':
        return nm.permit(ref);
      case 'RENEW_QUALIFICATION':
        return nm.qualification(ref);
      case 'RETURN_TO_SERVICE':
        return tk(`opStatus.${ref}`, undefined, ref);
      case 'FIX_DATA': {
        const [kind, ...code] = ref.split(':');
        if (kind === 'STALE') return null; // own title: the whole feed is out of date
        return missingItemName(kind, code.join(':'), nm);
      }
      default:
        return ltr(ref);
    }
  })();
  if (name === null) return t('maintenance.action.FIX_STALE');
  return tk(`maintenance.action.${action}`, { name }, action);
}

type Template = (p: ReasonParams, names: Names) => string;

export const REASON_TEMPLATES: Record<string, Template> = {
  // ── ineligibility reasons ──
  AIRCRAFT_NOT_OPERATIONAL: (p) => `המטוס אינו מבצעי (סטטוס: ${tk(`opStatus.${s(p.status)}`)})`,
  ALREADY_ASSIGNED: (p) =>
    `המטוס כבר משובץ למשימה "${s(p.mission_name)}" (${d(p.window_start)}–${d(p.window_end)})`,
  ENGINE_REQUIREMENT_NOT_MET: (p) =>
    `דרישת מנועים לא מתקיימת: נדרש ${tk(`engineReq.${s(p.required)}`)}, למטוס ${s(p.actual)} מנועים`,
  AIRCRAFT_TYPE_NOT_SELECTED: (p, nm) => `הדגם (${nm.type(p.type_code)}) אינו בין הדגמים שנבחרו למשימה`,
  SQUADRON_NOT_SELECTED: (p, nm) =>
    p.squadron_code
      ? `המטוס משויך ל${nm.squadron(p.squadron_code)}, שאינה בין הטייסות שנבחרו למשימה`
      : 'המטוס אינו משויך לאף טייסת, ולכן אינו בין הטייסות שנבחרו למשימה',
  ENDURANCE_EXCEEDED: (p) =>
    `משך הטיסה כולל מרווח (${n(p.required_hours, 2)} ש׳) חורג מזמן השהייה המרבי באוויר (${n(p.max_hours)} ש׳)`,
  GROUNDING_MA_OPEN: (p, nm) => `פעולת תחזוקה מקרקעת פתוחה: ${ltr(s(p.ma_number))} (${nm.zone(p.zone_code)})`,
  INSPECTION_EXPIRED: (p, nm) =>
    p.over_by !== undefined && p.over_by !== null
      ? `${nm.inspection(p.inspection_code)} — פג תוקף (חריגה של ${n(p.over_by)} ${nm.unit(p.unit)})`
      : `${nm.inspection(p.inspection_code)} — פג תוקף ב-${d(p.due_at)}`,
  INSPECTION_DUE_BEFORE_MISSION_END: (p, nm) =>
    p.remaining !== undefined && p.remaining !== null
      ? `${nm.inspection(p.inspection_code)} תפוג במהלך המשימה: נותרו ${n(p.remaining)} ${nm.unit(p.unit)}, המשימה צורכת ${n(p.required)}`
      : `${nm.inspection(p.inspection_code)} תפוג ב-${d(p.due_at)}, לפני סיום המשימה (${d(p.mission_end)})`,
  PERMIT_EXPIRED: (p, nm) => `תוקף ${nm.permit(p.permit_type_code)} פג ב-${d(p.expired_at)}`,
  PERMIT_EXPIRES_BEFORE_MISSION_END: (p, nm) =>
    p.expires_at
      ? `תוקף ${nm.permit(p.permit_type_code)} יפוג ב-${d(p.expires_at)}, לפני סיום המשימה (${d(p.mission_end)})`
      : `תוקף ${nm.permit(p.permit_type_code)} יפוג במהלך המשימה (נותרו ${n(p.remaining)} ${nm.unit(p.unit)})`,
  DEFERRAL_EXPIRED: (p) => `תוקף הדחייה של ${ltr(s(p.ma_number))} פג`,
  DEFERRAL_EXPIRES_BEFORE_MISSION_END: (p) => `תוקף הדחייה של ${ltr(s(p.ma_number))} יפוג לפני סיום המשימה`,
  QUALIFICATION_MISSING: (p, nm) => `למטוס אין כשירות ${nm.qualification(p.qualification_code)}`,
  QUALIFICATION_EXPIRED: (p, nm) =>
    `תוקף כשירות ${nm.qualification(p.qualification_code)} פג ב-${d(p.expired_at)}`,
  QUALIFICATION_EXPIRES_BEFORE_MISSION_END: (p, nm) =>
    `תוקף כשירות ${nm.qualification(p.qualification_code)} יפוג ב-${d(p.expires_at)}, לפני סיום המשימה`,
  QUALIFICATION_REVOKED_BY_MA: (p, nm) =>
    `כשירות ${nm.qualification(p.qualification_code)} אינה בתוקף עקב פעולת תחזוקה ${ltr(s(p.ma_number))}`,
  QUALIFICATION_REVOKED_BY_PERMIT: (p, nm) =>
    `כשירות ${nm.qualification(p.qualification_code)} מוגבלת ב${nm.permit(p.permit_type_code)}: ${tk(`restriction.${s(p.restriction)}`, undefined, s(p.restriction))}`,
  PERMIT_RESTRICTION_CONFLICT: (p, nm) =>
    `הגבלה ב${nm.permit(p.permit_type_code)} (${tk(`restriction.${s(p.restriction)}`, undefined, s(p.restriction))}) אינה מתאימה למשימת ${tk(`missionType.${s(p.mission_type)}`)}`,
  IDLE_LIMIT_EXCEEDED: (p) =>
    `המטוס לא טס ${n(p.idle_days, 0)} ימים (מגבלה: ${n(p.limit_days, 0)}) — נדרשת בדיקה לאחר השבתה`,
  DATA_MISSING: (p, nm) => `נתונים חסרים (${missingItemName(p.item_kind, p.item_code, nm)}) — לא ניתן לאשר כשירות`,
  DATA_STALE: (p, nm) => {
    const source = tk(`feed.${s(p.source_name)}`, undefined, s(p.source_name));
    const what = p.unit ? `${source} (${nm.unit(p.unit)})` : source;
    if (p.age_hours === null || p.age_hours === undefined) {
      return `לא התקבלו נתונים ממקור ${what} — לא ניתן לאשר כשירות`;
    }
    return typeof p.age_hours === 'number' && p.age_hours < 0
      ? `חותמת הזמן ממקור ${what} היא בעתיד (${n(-p.age_hours, 0)} שעות) — לא ניתן לאשר כשירות`
      : `הנתונים ממקור ${what} אינם עדכניים (לפני ${n(p.age_hours, 0)} שעות)`;
  },
  MANUALLY_EXCLUDED: () => 'הוחרג ידנית על ידי המתכנן',
  JEOPARDIZES_LATER_ASSIGNMENT: (p, nm) =>
    `שיבוץ זה יגרום לחריגה ב${nm.ref(p.source, p.ref)} (${n(p.over_by)} ${nm.unit(p.unit)}) עד סיום המשימה המשובצת "${s(p.mission_name)}"`,
  // ── warnings ──
  LOW_MARGIN_AFTER_MISSION: (p, nm) =>
    `מרווח נמוך: לאחר המשימה יוותרו ${n(p.remaining_after)} ${nm.unit(p.unit)} עד ${nm.ref(p.source, p.ref)}`,
  EXPIRES_SOON_AFTER_MISSION: (p, nm) => `${nm.ref(p.source, p.ref)} יפוג זמן קצר לאחר המשימה (${d(p.due_at)})`,
  HIGH_DEFECT_RISK: (p) => `סיכון גבוה לתקלה (${s(p.probability)}%)`,
  TOLERANCE_AVAILABLE: (p, nm) =>
    `קיימת טולרנס של ${n(p.tolerance)} ${nm.unit(p.unit)} ל${nm.inspection(p.inspection_code)} — שימוש בה באישור גורם מוסמך בלבד`,
  IDLE_CHECK_REQUIRED: (p) => `נדרשת בדיקה לאחר השבתה לפני הטיסה (${n(p.idle_days, 0)} ימים ללא טיסה)`,
  ML_MODEL_UNAVAILABLE: (p) =>
    `${tk(`model.${s(p.model_name)}`, undefined, s(p.model_name))} אינו זמין — הדירוג חושב ללא רכיב זה`,
  EXPIRES_WITHIN_WINDOW: (p, nm) => {
    const amount = (v: unknown) => (p.unit === 'CAL_DAY' ? `${n(v, 0)} ימים` : `${n(v)} ${nm.unit(p.unit)}`);
    return `תוקף ${nm.ref(p.source, p.ref)} יפוג בטווח "תוך ${n(p.within, 0)} ועד ${amount(p.until)}": נותרו ${amount(p.margin)} לאחר המשימה`;
  },
  NON_GROUNDING_PERMIT_INVALID: (p, nm) =>
    p.outcome === 'EXPIRED'
      ? `תוקף ${nm.permit(p.permit_type_code)} פג (היתר שאינו מקרקע — לבדיקת גורם מוסמך)`
      : `תוקף ${nm.permit(p.permit_type_code)} יפוג לפני סיום המשימה (היתר שאינו מקרקע — לבדיקת גורם מוסמך)`,
  // ── unblock hints ──
  UNBLOCK_MAX_DURATION: (p) =>
    `כשיר אם משך הטיסה יקוצר ל-${typeof p.max_hours === 'number' ? formatHours(p.max_hours) : s(p.max_hours)} שעות לכל היותר`,
  UNBLOCK_MAX_LANDINGS: (p) => `כשיר אם יבוצעו עד ${s(p.max_landings_plus_tg)} נחיתות (כולל גע-וסע)`,
  UNBLOCK_LATEST_END: (p) => `כשיר אם המשימה תסתיים עד ${d(p.latest_end)}`,
  UNBLOCK_PERFORM_INSPECTION: (p, nm) => `ביצוע ${nm.inspection(p.inspection_code)} יחזיר את המטוס לכשירות`,
  UNBLOCK_RENEW_PERMIT: (p, nm) => `נדרש חידוש ${nm.permit(p.permit_type_code)}`,
  UNBLOCK_RENEW_QUALIFICATION: (p, nm) => `נדרש חידוש כשירות ${nm.qualification(p.qualification_code)}`,
  UNBLOCK_CLOSE_MA: (p) => `נדרשת סגירת פעולת תחזוקה ${ltr(s(p.ma_number))}`,
  UNBLOCK_IDLE_CHECK: () => 'נדרש ביצוע בדיקה לאחר השבתה',
};

export function renderReason(reason: { code: string; params?: ReasonParams | null }, names: Names): string {
  const tpl = REASON_TEMPLATES[reason.code];
  return tpl ? tpl(reason.params ?? {}, names) : reason.code;
}
