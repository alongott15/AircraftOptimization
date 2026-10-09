/** Design tokens — DESIGN_SYSTEM.md §2. Components never use raw hex values. */

export const COMPLIANCE_STATUSES = ['EXPIRED', 'UNKNOWN', 'EXPIRING_SOON', 'OK'] as const;
export type ComplianceStatus = (typeof COMPLIANCE_STATUSES)[number];
export type Tone = 'EXPIRED' | 'EXPIRING_SOON' | 'OK' | 'UNKNOWN' | 'INFO';

export const STATUS_RANK: Record<ComplianceStatus, number> = { EXPIRED: 3, UNKNOWN: 2, EXPIRING_SOON: 1, OK: 0 };

/** CSS variables defined in global.css (light + dark). */
export const toneVars: Record<Tone, { fg: string; bg: string }> = {
  EXPIRED: { fg: 'var(--ao-expired-fg)', bg: 'var(--ao-expired-bg)' },
  EXPIRING_SOON: { fg: 'var(--ao-expiring-fg)', bg: 'var(--ao-expiring-bg)' },
  OK: { fg: 'var(--ao-ok-fg)', bg: 'var(--ao-ok-bg)' },
  UNKNOWN: { fg: 'var(--ao-unknown-fg)', bg: 'var(--ao-unknown-bg)' },
  INFO: { fg: 'var(--ao-info-fg)', bg: 'var(--ao-info-bg)' },
};

/** Raw values (needed for WCAG contrast tests and WebGL materials). */
export const statusPalette: Record<Tone, { fgLight: string; bgLight: string; fgDark: string; bgDark: string }> = {
  EXPIRED: { fgLight: '#B42318', bgLight: '#FEE4E2', fgDark: '#F97066', bgDark: '#3B1214' },
  EXPIRING_SOON: { fgLight: '#B54708', bgLight: '#FEF0C7', fgDark: '#FDB022', bgDark: '#3A2A0A' },
  OK: { fgLight: '#067647', bgLight: '#DCFAE6', fgDark: '#47CD89', bgDark: '#0B2B1C' },
  UNKNOWN: { fgLight: '#475467', bgLight: '#EAECF0', fgDark: '#98A2B3', bgDark: '#1F2737' },
  INFO: { fgLight: '#175CD3', bgLight: '#D1E9FF', fgDark: '#84CAFF', bgDark: '#0F2747' },
};

export const palette3d = {
  hull: '#D8DEE6',
  hullDark: '#B8C2D0',
  accent: '#1D4E89',
  glass: '#2B3A55',
  expired: '#FF3B30',
  expiring: '#FFB020',
  info: '#3A86FF',
  ok: '#2ECC71',
  groundLight: '#E5E7EB',
  groundDark: '#1F2937',
} as const;

export const severityTone: Record<string, Tone> = { GROUNDING: 'EXPIRED', DEFERRED: 'EXPIRING_SOON', MONITOR: 'INFO' };

export const opStatusTone: Record<string, Tone> = {
  OPERATIONAL: 'OK',
  GROUNDED: 'EXPIRED',
  IN_MAINTENANCE: 'EXPIRING_SOON',
  STORED: 'UNKNOWN',
};

export const riskTone: Record<string, Tone> = { LOW: 'OK', MEDIUM: 'EXPIRING_SOON', HIGH: 'EXPIRED', UNAVAILABLE: 'UNKNOWN' };
