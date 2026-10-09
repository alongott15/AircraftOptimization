/** 2D top-view fallback when WebGL is unavailable — same hotspots, same data (DESIGN_SYSTEM.md §6.4). */
import { Alert, Box } from '@mantine/core';

import type { AircraftDetail } from '../../api/types';
import { t, tk } from '../../i18n';
import { palette3d } from '../../theme/tokens';
import type { ZoneInfo } from './zoneStatus';
import { ZONE_2D, ZONES, type Zone } from './zones';

interface Props {
  detail: AircraftDetail;
  zones: Record<Zone, ZoneInfo>;
  selectedZone: string | null;
  onSelectZone: (z: string | null) => void;
}

const color = (info: ZoneInfo) =>
  info.tone === 'EXPIRED' ? palette3d.expired : info.tone === 'EXPIRING_SOON' ? palette3d.expiring
    : info.tone === 'INFO' ? palette3d.info : '#8E9AAF';

export function Fallback2D({ detail, zones, selectedZone, onSelectZone }: Props) {
  const twin = detail.engine_count >= 2;
  return (
    <Box data-testid="aircraft-fallback" data-selected-zone={selectedZone ?? ''}>
      <Alert color="gray" mb="sm">{t('viewer.fallback')}</Alert>
      <Box style={{ position: 'relative', aspectRatio: '5 / 3', direction: 'ltr' }}>
        <svg viewBox="0 0 200 120" width="100%" height="100%" role="img" aria-label={detail.tail_number}>
          <rect x="8" y="52" width="176" height="16" rx="8" fill={palette3d.hull} stroke="#94A3B8" />
          <polygon points="96,52 110,52 104,8 92,8" fill={palette3d.hull} stroke="#94A3B8" />
          <polygon points="96,68 110,68 104,112 92,112" fill={palette3d.hull} stroke="#94A3B8" />
          <polygon points="10,56 22,56 18,38 12,38" fill={palette3d.accent} />
          <polygon points="10,64 22,64 18,82 12,82" fill={palette3d.accent} />
          {twin ? (
            <>
              <rect x="118" y="30" width="22" height="8" rx="4" fill="#64748B" />
              <rect x="118" y="82" width="22" height="8" rx="4" fill="#64748B" />
            </>
          ) : (
            <rect x="182" y="55" width="10" height="10" rx="3" fill="#64748B" />
          )}
          <text x="60" y="63" fontSize="7" fill={palette3d.accent} fontWeight="700">{detail.tail_number}</text>
        </svg>
        {ZONES.filter((z) => zones[z].items.length && (z !== 'ENGINE_2' || twin)).map((zone) => (
          <button key={zone} type="button" className="ao-hotspot" data-zone={zone} data-tone={zones[zone].tone ?? 'OK'}
            aria-label={`${tk(`zone.${zone}`)}: ${zones[zone].items.length}`}
            onClick={() => onSelectZone(selectedZone === zone ? null : zone)}
            style={{
              position: 'absolute', left: `${ZONE_2D[zone].x}%`, top: `${ZONE_2D[zone].y}%`, transform: 'translate(-50%, -50%)',
              background: color(zones[zone]), outline: selectedZone === zone ? '3px solid #5B8DEF' : undefined,
            }}>
            {zones[zone].items.length}
          </button>
        ))}
      </Box>
    </Box>
  );
}
