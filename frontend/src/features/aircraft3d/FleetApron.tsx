/** Fleet apron view (FR-24): every aircraft parked by base, hull tinted by compliance status; click opens it. */
import { ContactShadows, Html, OrbitControls } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';
import { useMemo } from 'react';

import type { AircraftSummary } from '../../api/types';
import { t, tk } from '../../i18n';
import { palette3d } from '../../theme/tokens';
import { AircraftModel, StudioLights } from './AircraftModel';
import type { ZoneInfo } from './zoneStatus';
import { shapeFor, specFor, type Zone, ZONES } from './zones';

// Tint by compliance status; OK keeps the real livery/camouflage.
const HULL_BY_STATUS: Record<string, string | undefined> = {
  EXPIRED: '#F3A6A0',
  EXPIRING_SOON: '#F8D48A',
  UNKNOWN: '#9AA5B5',
};
const EMPTY_ZONES = Object.fromEntries(
  ZONES.map((z): [Zone, ZoneInfo] => [z, { tone: null, items: [] }]),
) as Record<Zone, ZoneInfo>;
const SCALE = 0.32;

interface Props {
  aircraft: AircraftSummary[];
  shapes: Record<string, string>;
  bases: { base_code: string; name_he: string }[];
  onOpen: (tail: string) => void;
}

export default function FleetApron({ aircraft, shapes, bases, onOpen }: Props) {
  const layout = useMemo(() => {
    const rows = bases.map((b) => aircraft.filter((a) => a.base_code === b.base_code));
    return rows.flatMap((row, r) => row.map((a, i) => ({
      a, pos: [(i - (row.length - 1) / 2) * 5.2, 0, (r - (rows.length - 1) / 2) * 6.5] as [number, number, number],
    })));
  }, [aircraft, bases]);

  return (
    <div className="ao-viewer" style={{ height: 620 }} data-testid="fleet-apron" role="img" aria-label={t('fleet3d.title')}>
      <Canvas dpr={[1, 2]} frameloop="demand" camera={{ position: [0, 26, 30], fov: 40 }}>
        <StudioLights shadows={false} />
        {bases.map((b, r) => (
          <Html key={b.base_code} position={[-30, 0, (r - (bases.length - 1) / 2) * 6.5]} center zIndexRange={[5, 0]}>
            <div className="ao-info-card" style={{ fontWeight: 700 }}>{b.name_he}</div>
          </Html>
        ))}
        {layout.map(({ a, pos }) => {
          const status = a.derived_status === 'GROUNDED' ? 'EXPIRED' : a.compliance_status;
          const shape = shapeFor(shapes[a.type_code], a.engine_count);
          const spec = specFor(shape);
          return (
            <group key={a.aircraft_id} position={pos} rotation={[0, Math.PI / 2, 0]}
              onClick={(e) => { e.stopPropagation(); onOpen(a.tail_number); }}
              onPointerOver={() => { document.body.style.cursor = 'pointer'; }}
              onPointerOut={() => { document.body.style.cursor = ''; }}>
              <group scale={[SCALE, SCALE, SCALE]} position={[0, -spec.groundY * SCALE, 0]}>
                <AircraftModel shape={shape} zones={EMPTY_ZONES} selectedZone={null} lite
                  onSelect={() => onOpen(a.tail_number)} outlineExpired={status === 'EXPIRED'} pulse={false}
                  hullColor={HULL_BY_STATUS[status]} />
              </group>
              <Html position={[0, 2.6, 0]} center zIndexRange={[5, 0]}>
                <button type="button" className="ao-hotspot" data-tail={a.tail_number} data-status={status}
                  style={{ background: status === 'EXPIRED' ? palette3d.expired : status === 'EXPIRING_SOON'
                    ? palette3d.expiring : status === 'UNKNOWN' ? '#8E9AAF' : palette3d.ok, fontSize: 11 }}
                  aria-label={`${a.tail_number} · ${tk(`status.${status}`)}`} onClick={() => onOpen(a.tail_number)}>
                  <bdi dir="ltr">{a.tail_number}</bdi>
                </button>
              </Html>
            </group>
          );
        })}
        <ContactShadows position={[0, 0, 0]} opacity={0.35} scale={70} blur={2.5} far={4} />
        <OrbitControls makeDefault enableDamping maxPolarAngle={Math.PI / 2.2} minDistance={12} maxDistance={70} />
      </Canvas>
    </div>
  );
}
