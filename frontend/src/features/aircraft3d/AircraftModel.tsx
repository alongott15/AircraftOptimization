/** Realistic procedural aircraft (procedural.ts); every mesh carries its zone (DESIGN_SYSTEM.md §6.2). */
import { Environment, Lightformer, Outlines } from '@react-three/drei';
import { useFrame, type ThreeEvent } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import * as THREE from 'three';

import { palette3d, type Tone } from '../../theme/tokens';
import { buildParts, type MaterialKind, type PartDef } from './procedural';
import type { ZoneInfo } from './zoneStatus';
import { specFor, type Zone } from './zones';

const TONE_COLOR: Partial<Record<Tone, string>> = {
  EXPIRED: palette3d.expired,
  EXPIRING_SOON: palette3d.expiring,
  INFO: palette3d.info,
  UNKNOWN: '#8E9AAF',
};

interface Look {
  color: string;
  roughness: number;
  metalness: number;
  clearcoat?: number;
  opacity?: number;
  emissive?: string;
  glow?: number;
}

const CAMO_BASE = '#C8AE7C';
const LOOK: Record<MaterialKind, Look> = {
  paint: { color: palette3d.hull, roughness: 0.3, metalness: 0.08, clearcoat: 0.65 },
  accent: { color: palette3d.accent, roughness: 0.3, metalness: 0.08, clearcoat: 0.65 },
  camo: { color: '#ffffff', roughness: 0.72, metalness: 0.04 },
  radome: { color: '#8F969D', roughness: 0.62, metalness: 0.04 },
  glass: { color: '#1E2E44', roughness: 0.04, metalness: 0.35, clearcoat: 1, opacity: 0.62 },
  metal: { color: '#A6AFB8', roughness: 0.28, metalness: 0.9 },
  nozzle: { color: '#5E5852', roughness: 0.48, metalness: 0.75 },
  dark: { color: '#1F242B', roughness: 0.7, metalness: 0.2 },
  rubber: { color: '#16181C', roughness: 0.95, metalness: 0 },
  prop: { color: '#2B2F36', roughness: 0.45, metalness: 0.5 },
  navRed: { color: '#FF3B30', roughness: 0.3, metalness: 0, emissive: '#FF3B30', glow: 0.7 },
  navGreen: { color: '#2ECC71', roughness: 0.3, metalness: 0, emissive: '#2ECC71', glow: 0.7 },
  missile: { color: '#E6E8EA', roughness: 0.45, metalness: 0.1 },
};

let camo: THREE.Texture | null | undefined;

/** Three-tone desert camouflage (sand / brown / olive) with a light-grey underside band; deterministic. */
function desertCamo(): THREE.Texture | null {
  if (camo !== undefined) return camo;
  const canvas = document.createElement('canvas');
  const W = 1024;
  const H = 512;
  canvas.width = W;
  canvas.height = H;
  const ctx = typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null;
  if (!ctx) {
    camo = null;
    return camo;
  }
  let seed = 0x5eed;
  const rnd = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 2 ** 32;
  };
  ctx.fillStyle = CAMO_BASE;
  ctx.fillRect(0, 0, W, H);
  const blobs = (color: string, count: number) => {
    ctx.fillStyle = color;
    for (let b = 0; b < count; b += 1) {
      const cx = rnd() * W;
      const cy = H * (0.14 + rnd() * 0.72);
      const rx = 55 + rnd() * 95;
      const ry = 24 + rnd() * 42;
      const pts = Array.from({ length: 9 }, (_, i) => {
        const a = (i / 9) * Math.PI * 2;
        const k = 0.65 + rnd() * 0.6;
        return [Math.cos(a) * rx * k, Math.sin(a) * ry * k] as const;
      });
      for (const dx of [-W, 0, W]) {
        ctx.beginPath();
        pts.forEach(([px, py], i) => {
          const [nx, ny] = pts[(i + 1) % pts.length] ?? pts[0] ?? [0, 0];
          const mx = cx + dx + (px + nx) / 2;
          const my = cy + (py + ny) / 2;
          if (i === 0) ctx.moveTo(mx, my);
          else ctx.quadraticCurveTo(cx + dx + px, cy + py, mx, my);
        });
        ctx.closePath();
        ctx.fill();
      }
    }
  };
  blobs('#8B6943', 30);
  blobs('#A29C6E', 24);
  ctx.fillStyle = '#B9BEC3'; // underside (around θ = −90°, i.e. both texture edges)
  ctx.fillRect(0, 0, W, H * 0.11);
  ctx.fillRect(0, H * 0.89, W, H * 0.11);
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  camo = texture;
  return camo;
}

interface PartProps {
  part: PartDef;
  info: ZoneInfo | undefined;
  selected: boolean;
  outline: boolean;
  pulse: boolean;
  onSelect: (z: Zone) => void;
  hullColor?: string;
  lite: boolean;
}

function Part({ part, info, selected, outline, pulse, onSelect, hullColor, lite }: PartProps) {
  const material = useRef<THREE.MeshPhysicalMaterial>(null);
  const look = LOOK[part.kind];
  const tone = info?.tone ?? null;
  const highlight = tone ? TONE_COLOR[tone] ?? null : null;
  const base = highlight ? (tone === 'EXPIRED' ? 0.75 : 0.55) : look.glow ?? 0;
  useFrame(({ clock }) => {
    if (!material.current) return;
    material.current.emissiveIntensity =
      pulse && tone === 'EXPIRED' ? 0.35 + (0.45 * (1 + Math.sin(clock.elapsedTime * 4))) / 2 : base;
  });
  // a status tint (fleet apron) replaces the camouflage so the status colour stays readable (golden rule 6)
  const map = part.kind === 'camo' && !hullColor ? desertCamo() : null;
  const tintable = part.kind === 'paint' || part.kind === 'camo';
  const color = tintable && hullColor ? hullColor : part.kind === 'camo' && !map ? CAMO_BASE : look.color;
  const handleClick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    onSelect(part.zone);
  };
  return (
    <mesh geometry={part.geometry} position={part.position} rotation={part.rotation} castShadow={!lite}
      receiveShadow={!lite} userData={{ zone: part.zone }} onClick={handleClick}>
      <meshPhysicalMaterial ref={material} color={color} map={map} emissive={highlight ?? look.emissive ?? '#000000'}
        emissiveIntensity={base} roughness={look.roughness} metalness={look.metalness}
        clearcoat={lite ? 0 : look.clearcoat ?? 0} clearcoatRoughness={0.18} transparent={look.opacity !== undefined}
        opacity={look.opacity ?? 1} side={THREE.DoubleSide} />
      {(outline || selected) && (
        <Outlines thickness={selected ? 0.05 : 0.03} color={selected ? '#5B8DEF' : palette3d.expired} />
      )}
    </mesh>
  );
}

export interface AircraftModelProps {
  shape: string;
  zones: Record<Zone, ZoneInfo>;
  selectedZone: string | null;
  onSelect: (z: Zone) => void;
  outlineExpired: boolean;
  pulse: boolean;
  hullColor?: string;
  /** Fleet apron: no small details, no shadows/clearcoat (dozens of aircraft at once). */
  lite?: boolean;
}

export function AircraftModel({
  shape, zones, selectedZone, onSelect, outlineExpired, pulse, hullColor, lite = false,
}: AircraftModelProps) {
  const parts = useMemo(() => buildParts(shape).filter((p) => !(lite && p.detail)), [shape, lite]);
  return (
    <group>
      {parts.map((part, i) => (
        <Part key={i} part={part} info={zones[part.zone]} selected={selectedZone === part.zone} outline={outlineExpired}
          pulse={pulse} onSelect={onSelect} hullColor={hullColor} lite={lite} />
      ))}
    </group>
  );
}

/** Studio lighting + reflections from in-scene light panels (no HDR download — offline friendly). */
export function StudioLights({ shadows = true }: { shadows?: boolean }) {
  return (
    <>
      <hemisphereLight args={['#ffffff', '#5b6472', 0.55]} />
      <directionalLight position={[10, 16, 8]} intensity={1.7} castShadow={shadows} shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-14} shadow-camera-right={14} shadow-camera-top={14} shadow-camera-bottom={-14} shadow-bias={-0.0004} />
      <directionalLight position={[-9, 6, -7]} intensity={0.45} />
      <Environment resolution={256} frames={1}>
        <Lightformer form="rect" intensity={2.4} position={[0, 9, 0]} rotation-x={Math.PI / 2} scale={[16, 16, 1]} />
        <Lightformer form="rect" intensity={1.3} position={[-10, 3, 5]} rotation-y={Math.PI / 2} scale={[12, 4, 1]} />
        <Lightformer form="rect" intensity={1.0} position={[10, 3, -5]} rotation-y={-Math.PI / 2} scale={[12, 4, 1]} />
        <Lightformer form="ring" intensity={0.9} position={[0, 2, 12]} scale={7} />
      </Environment>
    </>
  );
}

/** y of the ground plane (wheel bottoms) for a shape. */
export function groundY(shape: string): number {
  return specFor(shape).groundY;
}
