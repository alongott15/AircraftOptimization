/** Procedural low-poly aircraft; every mesh carries its zone (DESIGN_SYSTEM.md §6.2). */
import { Outlines } from '@react-three/drei';
import { useFrame, type ThreeEvent } from '@react-three/fiber';
import { useRef, type ReactNode } from 'react';
import * as THREE from 'three';

import { palette3d, type Tone } from '../../theme/tokens';
import type { ZoneInfo } from './zoneStatus';
import { specFor, type Zone } from './zones';

const TONE_COLOR: Partial<Record<Tone, string>> = {
  EXPIRED: palette3d.expired,
  EXPIRING_SOON: palette3d.expiring,
  INFO: palette3d.info,
  UNKNOWN: '#8E9AAF',
};

interface PartProps {
  zone: Zone;
  info: ZoneInfo | undefined;
  selected: boolean;
  outline: boolean;
  pulse: boolean;
  onSelect: (z: Zone) => void;
  color?: string;
  hull?: string;
  children: ReactNode;
  position?: [number, number, number];
  rotation?: [number, number, number];
  scale?: [number, number, number];
  transparent?: boolean;
}

function Part({ zone, info, selected, outline, pulse, onSelect, color, hull, children, transparent, ...transform }: PartProps) {
  const material = useRef<THREE.MeshStandardMaterial>(null);
  const tone = info?.tone ?? null;
  const emissive = tone ? TONE_COLOR[tone] ?? '#000000' : '#000000';
  const base = tone === 'EXPIRED' ? 0.75 : tone ? 0.55 : 0;
  useFrame(({ clock }) => {
    if (!material.current) return;
    material.current.emissiveIntensity = pulse && tone === 'EXPIRED' ? 0.35 + 0.45 * (1 + Math.sin(clock.elapsedTime * 4)) / 2 : base;
  });
  const handleClick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    onSelect(zone);
  };
  return (
    <mesh {...transform} castShadow receiveShadow userData={{ zone }} onClick={handleClick}>
      {children}
      <meshStandardMaterial ref={material} color={color ?? hull ?? palette3d.hull} emissive={emissive} emissiveIntensity={base}
        metalness={0.25} roughness={0.55} transparent={transparent} opacity={transparent ? 0.6 : 1} />
      {(outline || selected) && (
        <Outlines thickness={selected ? 0.05 : 0.03} color={selected ? '#5B8DEF' : palette3d.expired} />
      )}
    </mesh>
  );
}

export interface AircraftModelProps {
  shape: string;
  engineCount: number;
  zones: Record<Zone, ZoneInfo>;
  selectedZone: string | null;
  onSelect: (z: Zone) => void;
  outlineExpired: boolean;
  pulse: boolean;
  hullColor?: string;
}

export function AircraftModel({
  shape, engineCount, zones, selectedZone, onSelect, outlineExpired, pulse, hullColor,
}: AircraftModelProps) {
  const s = specFor(shape);
  const half = s.length / 2;
  const wingHalf = s.span / 2;
  const wingLen = wingHalf - s.radius * 0.6;
  const sweep = THREE.MathUtils.degToRad(s.sweepDeg);
  const common = (zone: Zone) => ({
    zone, info: zones[zone], selected: selectedZone === zone, outline: outlineExpired, pulse, onSelect, hull: hullColor,
  });
  const engineZones: Zone[] = engineCount >= 2 ? ['ENGINE_1', 'ENGINE_2'] : ['ENGINE_1'];
  const gearY = -s.radius - s.gearHeight / 2;
  return (
    <group>
      {/* Fuselage (cylinder along X) */}
      <Part {...common('FUSELAGE')} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[s.radius, s.radius * 0.95, s.length - 1.6, 24]} />
      </Part>
      {/* Nose */}
      <Part {...common('NOSE')} position={[half - 0.8, 0, 0]} scale={[1.5, 1, 1]}>
        <sphereGeometry args={[s.radius * 0.97, 24, 16, 0, Math.PI * 2, 0, Math.PI]} />
      </Part>
      {/* Cockpit glass */}
      <Part {...common('COCKPIT')} color={palette3d.glass} transparent position={[half - 1.15, s.radius * 0.55, 0]}
        scale={[1.3, 0.55, 0.85]}>
        <sphereGeometry args={[s.radius * 0.75, 20, 12]} />
      </Part>
      {/* Tail cone + fin + stabilizer (TAIL) */}
      <Part {...common('TAIL')} position={[-half + 0.2, s.radius * 0.15, 0]} rotation={[0, 0, Math.PI / 2]}>
        <coneGeometry args={[s.radius * 0.95, 1.6, 24]} />
      </Part>
      <Part {...common('TAIL')} color={palette3d.accent} position={[s.finX, s.radius + s.finHeight / 2, 0]}
        rotation={[0, 0, -0.3]}>
        <boxGeometry args={[1.3, s.finHeight, 0.12]} />
      </Part>
      <Part {...common('TAIL')} position={[s.finX - 0.1, s.tTail ? s.radius + s.finHeight : s.radius * 0.3, 0]}>
        <boxGeometry args={[0.9, 0.08, s.hstabSpan]} />
      </Part>
      {/* Wings */}
      <Part {...common('WING_L')} position={[s.wingX, s.wingY, -(s.radius * 0.6 + wingLen / 2)]} rotation={[0, sweep, 0]}>
        <boxGeometry args={[s.chord, 0.14, wingLen]} />
      </Part>
      <Part {...common('WING_R')} position={[s.wingX, s.wingY, s.radius * 0.6 + wingLen / 2]} rotation={[0, -sweep, 0]}>
        <boxGeometry args={[s.chord, 0.14, wingLen]} />
      </Part>
      {/* Engines */}
      {s.engineMount === 'nose' ? (
        <group>
          <Part {...common('ENGINE_1')} position={[half + 0.25, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
            <cylinderGeometry args={[s.engineRadius * 0.8, s.radius * 0.95, s.engineLength, 20]} />
          </Part>
          <Part {...common('ENGINE_1')} color="#3B4252" position={[half + 0.75, 0, 0]}>
            <boxGeometry args={[0.06, 2.0, 0.16]} />
          </Part>
        </group>
      ) : (
        engineZones.map((zone) => {
          const z = zone === 'ENGINE_1' ? -s.engineZ : s.engineZ;
          return (
            <group key={zone}>
              <Part {...common(zone)} position={[s.engineX, s.engineY, z]} rotation={[0, 0, Math.PI / 2]}>
                <cylinderGeometry args={[s.engineRadius, s.engineRadius * 0.85, s.engineLength, 20]} />
              </Part>
              {s.propeller && (
                <Part {...common(zone)} color="#3B4252" position={[s.engineX + s.engineLength / 2 + 0.05, s.engineY, z]}>
                  <boxGeometry args={[0.05, 2.4, 0.14]} />
                </Part>
              )}
            </group>
          );
        })
      )}
      {/* Landing gear */}
      <Part {...common('GEAR_NOSE')} color="#5B6475" position={[half - 0.9, gearY, 0]}>
        <cylinderGeometry args={[0.06, 0.06, s.gearHeight, 8]} />
      </Part>
      <Part {...common('GEAR_NOSE')} color="#1F2937" position={[half - 0.9, gearY - s.gearHeight / 2, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.22, 0.22, 0.14, 16]} />
      </Part>
      {[-1, 1].map((side) => (
        <group key={side}>
          <Part {...common('GEAR_MAIN')} color="#5B6475" position={[s.mainGearX, gearY, side * s.mainGearZ]}>
            <cylinderGeometry args={[0.07, 0.07, s.gearHeight, 8]} />
          </Part>
          <Part {...common('GEAR_MAIN')} color="#1F2937" position={[s.mainGearX, gearY - s.gearHeight / 2, side * s.mainGearZ]}
            rotation={[Math.PI / 2, 0, 0]}>
            <cylinderGeometry args={[0.28, 0.28, 0.18, 16]} />
          </Part>
        </group>
      ))}
    </group>
  );
}

export function groundY(shape: string): number {
  const s = specFor(shape);
  return -s.radius - s.gearHeight - 0.28;
}
