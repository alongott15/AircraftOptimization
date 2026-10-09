/** Dates, numbers and units — DESIGN_SYSTEM.md §1 (Asia/Jerusalem, DD/MM/YYYY HH:mm, he-IL). */
import dayjs from 'dayjs';
import 'dayjs/locale/he';
import relativeTime from 'dayjs/plugin/relativeTime';
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';

dayjs.extend(utc);
dayjs.extend(timezone);
dayjs.extend(relativeTime);
dayjs.locale('he');

export const TZ = 'Asia/Jerusalem';
const DASH = '—';

export function formatDateTime(iso?: string | null): string {
  return iso ? dayjs.utc(iso).tz(TZ).format('DD/MM/YYYY HH:mm') : DASH;
}

export function formatDate(iso?: string | null): string {
  return iso ? dayjs.utc(iso).tz(TZ).format('DD/MM/YYYY') : DASH;
}

export function formatRelative(iso?: string | null, nowIso?: string | null): string {
  if (!iso) return DASH;
  const now = nowIso ? dayjs.utc(nowIso) : dayjs.utc();
  return dayjs.utc(iso).from(now);
}

export function formatNumber(value?: number | null, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return new Intl.NumberFormat('he-IL', { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(value);
}

export function formatPercent(fraction?: number | null): string {
  if (fraction === null || fraction === undefined) return DASH;
  return `${formatNumber(fraction * 100, 0)}%`;
}

/** 2.5 → "2:30" */
export function formatHours(hours?: number | null): string {
  if (hours === null || hours === undefined) return DASH;
  const total = Math.round(hours * 60);
  const h = Math.floor(total / 60);
  const m = total % 60;
  return `${h}:${m.toString().padStart(2, '0')}`;
}

/** Accepts "2.5", "2,5" or "2:30" → 2.5; returns null when invalid. */
export function parseDuration(input: string): number | null {
  const s = input.trim();
  if (!s) return null;
  const hm = /^(\d{1,2}):([0-5]\d)$/.exec(s);
  if (hm) return Number(hm[1]) + Number(hm[2]) / 60;
  const dec = /^\d+([.,]\d+)?$/.exec(s);
  if (dec) return Number(s.replace(',', '.'));
  return null;
}

/** "2026-01-15T10:00" in Israel time → UTC ISO with Z */
export function localInputToUtc(local: string): string {
  return dayjs.tz(local, TZ).utc().format('YYYY-MM-DDTHH:mm:ss[Z]');
}

export function utcToLocalInput(iso: string): string {
  return dayjs.utc(iso).tz(TZ).format('YYYY-MM-DDTHH:mm');
}

export function addHoursIso(iso: string, hours: number): string {
  return dayjs.utc(iso).add(hours, 'hour').format('YYYY-MM-DDTHH:mm:ss[Z]');
}

/** Rounded-up next full hour + offset hours, as a datetime-local value in Israel time. */
export function defaultStartLocal(nowIso?: string | null, offsetHours = 2): string {
  const now = nowIso ? dayjs.utc(nowIso) : dayjs.utc();
  return now.add(offsetHours, 'hour').startOf('hour').tz(TZ).format('YYYY-MM-DDTHH:mm');
}
