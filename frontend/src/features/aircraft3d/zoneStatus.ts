/** Map open MAs and zone-bound inspections to per-zone tone + item list (DESIGN_SYSTEM.md §6.3). */
import type { AircraftDetail } from '../../api/types';
import type { Tone } from '../../theme/tokens';
import { zoneForAta, type Zone, ZONES } from './zones';

export interface ZoneItem {
  kind: 'MA' | 'INSPECTION';
  ref: string;
  label: string;
  tone: Tone;
  detail?: string;
}

export interface ZoneInfo {
  tone: Tone | null;
  items: ZoneItem[];
}

const RANK: Record<Tone, number> = { EXPIRED: 4, UNKNOWN: 3, EXPIRING_SOON: 2, INFO: 1, OK: 0 };

export function worstTone(a: Tone | null, b: Tone): Tone {
  if (a === null) return b;
  return RANK[b] > RANK[a] ? b : a;
}

export function maTone(severity: string): Tone {
  return severity === 'GROUNDING' ? 'EXPIRED' : severity === 'DEFERRED' ? 'EXPIRING_SOON' : 'INFO';
}

export function computeZones(
  detail: AircraftDetail,
  inspectionName: (code: string) => string,
): Record<Zone, ZoneInfo> {
  const out = Object.fromEntries(ZONES.map((z) => [z, { tone: null, items: [] } as ZoneInfo])) as Record<Zone, ZoneInfo>;
  const engines: Zone[] = detail.engine_count >= 2 ? ['ENGINE_1', 'ENGINE_2'] : ['ENGINE_1'];
  const add = (zone: Zone, item: ZoneItem) => {
    const info = out[zone];
    info.items.push(item);
    info.tone = worstTone(info.tone, item.tone);
  };
  for (const ma of detail.maintenance_actions) {
    const known = ma.zone_code && (ZONES as readonly string[]).includes(ma.zone_code) ? (ma.zone_code as Zone) : null;
    const zone = known ?? zoneForAta(ma.ata_chapter) ?? 'FUSELAGE';
    let tone = maTone(ma.severity);
    if (ma.deferral?.status === 'EXPIRED') tone = 'EXPIRED';
    add(zone, { kind: 'MA', ref: ma.ma_number, label: ma.title, tone, detail: ma.severity });
  }
  for (const ins of detail.inspections) {
    if (!ins.zone_code || ins.status === 'OK') continue;
    const tone: Tone = ins.status === 'EXPIRED' ? 'EXPIRED' : ins.status === 'UNKNOWN' ? 'UNKNOWN' : 'EXPIRING_SOON';
    if (!(ZONES as readonly string[]).includes(ins.zone_code)) continue;  // unknown zone: listed in the side panel only
    const zones: Zone[] = ins.zone_code === 'ENGINE_1' ? engines : [ins.zone_code as Zone];
    for (const z of zones) {
      add(z, { kind: 'INSPECTION', ref: ins.inspection_code, label: inspectionName(ins.inspection_code), tone });
    }
  }
  return out;
}
