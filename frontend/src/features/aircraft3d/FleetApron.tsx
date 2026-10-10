/** Fleet apron view (FR-24): every aircraft parked by base, hull tinted by compliance status; click opens it. */
import { ContactShadows, Html, OrbitControls } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';
import { useMemo, useRef } from 'react';

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
const COL = 5.2; // spacing along a line
const ROW = 7.6; // spacing between lines (fits the F-15-like length 20.4 m × SCALE)
const PER_LINE = 10;

interface Props {
  aircraft: AircraftSummary[];
  shapes: Record<string, string>;
  bases: { base_code: string; name_he: string }[];
  onOpen: (tail: string) => void;
}

export default function FleetApron({ aircraft, shapes, bases, onOpen }: Props) {
  // Stable mount target for drei <Html>: otherwise the first overlay mounts before the canvas event target is
  // connected, is re-attached on connect and stays empty (a missing hotspot).
  const host = useRef<HTMLDivElement>(null);
  const { layout, labels } = useMemo(() => {
    // one or more lines per base (at most PER_LINE aircraft each)
    const lines = bases.flatMap((b) => {
      const own = aircraft.filter((a) => a.base_code === b.base_code);
      const chunks = Array.from({ length: Math.max(1, Math.ceil(own.length / PER_LINE)) },
        (_, k) => own.slice(k * PER_LINE, (k + 1) * PER_LINE));
      return chunks.map((row, k) => ({ base: b, row, first: k === 0, count: chunks.length }));
    });
    const z = (r: number) => (r - (lines.length - 1) / 2) * ROW;
    // RTL: each line starts on the right (+x), with its base name just before the first aircraft
    return {
      layout: lines.flatMap((l, r) => l.row.map((a, i) => ({
        a, pos: [((l.row.length - 1) / 2 - i) * COL, 0, z(r)] as [number, number, number],
      }))),
      labels: lines.flatMap((l, r) => (l.first ? [{
        base: l.base, pos: [((l.row.length - 1) / 2) * COL + 5, 0, z(r + (l.count - 1) / 2)] as [number, number, number],
      }] : [])),
    };
  }, [aircraft, bases]);

  return (
    <div ref={host} className="ao-viewer" style={{ height: 620 }} data-testid="fleet-apron" role="img" aria-label={t('fleet3d.title')}>
      <Canvas dpr={[1, 2]} frameloop="demand" camera={{ position: [0, 36, 40], fov: 40 }}>
        <StudioLights shadows={false} />
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
              <Html position={[0, 2.6, 0]} center wrapperClass="ao-html" portal={host as React.RefObject<HTMLElement>} zIndexRange={[5, 0]}>
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
        {labels.map(({ base, pos }) => (
          <Html key={base.base_code} position={pos} center wrapperClass="ao-html" portal={host as React.RefObject<HTMLElement>} zIndexRange={[5, 0]}>
            <div className="ao-info-card" style={{ fontWeight: 700, minInlineSize: 0, whiteSpace: 'nowrap' }}>{base.name_he}</div>
          </Html>
        ))}
        <ContactShadows position={[0, 0, 0]} opacity={0.35} scale={70} blur={2.5} far={4} />
        <OrbitControls makeDefault enableDamping maxPolarAngle={Math.PI / 2.2} minDistance={12} maxDistance={70} />
      </Canvas>
    </div>
  );
}
