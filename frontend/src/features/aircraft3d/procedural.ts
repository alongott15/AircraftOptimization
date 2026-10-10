/**
 * Procedural, realistic aircraft geometry from zones.ts (DESIGN_SYSTEM.md §6.2) — no external model files, so the
 * app stays offline/air-gapped. Bodies are lofted through elliptical sections (analytic normals, no seams), lifting
 * surfaces use a NACA 4-digit thickness profile with taper/sweep/dihedral, rotating parts are lathed.
 * Every part carries its maintenance zone, so clicks, hotspots and expired highlighting keep working.
 * Nose → +X, up → +Y, right wing → +Z.
 */
import * as THREE from 'three';

import { specFor, type S4, type ShapeSpec, type SurfaceDim, type Vec3, type Zone } from './zones';

export type MaterialKind =
  | 'paint' | 'accent' | 'camo' | 'radome' | 'glass' | 'metal' | 'nozzle' | 'dark' | 'rubber' | 'prop'
  | 'navRed' | 'navGreen' | 'missile';

export interface PartDef {
  zone: Zone;
  kind: MaterialKind;
  geometry: THREE.BufferGeometry;
  position?: Vec3;
  rotation?: Vec3;
  /** Small details (windows, lights, missiles) are skipped in the fleet apron view. */
  detail?: boolean;
}

const DEG = Math.PI / 180;
// Camo/livery texture coordinates are taken from model space so patterns run continuously across parts.
const U = (x: number) => (x + 10) / 20;

function at<T>(items: readonly T[], i: number): T {
  const v = items[i];
  if (v === undefined) throw new RangeError(`index ${i}`);
  return v;
}

// ── bodies ──────────────────────────────────────────────────────────────
interface Sec {
  x: number;
  y: number;
  ry: number;
  rz: number;
}

const toSec = ([x, y, ry, rz]: S4): Sec => ({ x, y, ry, rz });
const catmull = (p0: number, p1: number, p2: number, p3: number, t: number) =>
  0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t * t * t);

/** Catmull-Rom densification of sections (sorted by x) so the body curves smoothly. */
export function smooth(sections: readonly S4[], perSpan = 6): Sec[] {
  const s = sections.map(toSec).sort((a, b) => a.x - b.x);
  const out: Sec[] = [];
  for (let i = 0; i < s.length - 1; i += 1) {
    const p0 = at(s, Math.max(i - 1, 0));
    const p1 = at(s, i);
    const p2 = at(s, i + 1);
    const p3 = at(s, Math.min(i + 2, s.length - 1));
    for (let k = 0; k < perSpan; k += 1) {
      const t = k / perSpan;
      out.push({
        x: p1.x + (p2.x - p1.x) * t, y: catmull(p0.y, p1.y, p2.y, p3.y, t),
        ry: Math.max(0, catmull(p0.ry, p1.ry, p2.ry, p3.ry, t)), rz: Math.max(0, catmull(p0.rz, p1.rz, p2.rz, p3.rz, t)),
      });
    }
  }
  out.push(at(s, s.length - 1));
  return out;
}

/** Section interpolated at x (clamped to the body). */
export function sectionAt(s: readonly Sec[], x: number): Sec {
  for (let i = 0; i < s.length - 1; i += 1) {
    const a = at(s, i);
    const b = at(s, i + 1);
    if (x >= a.x && x <= b.x) {
      const t = (x - a.x) / (b.x - a.x || 1);
      return { x, y: a.y + (b.y - a.y) * t, ry: a.ry + (b.ry - a.ry) * t, rz: a.rz + (b.rz - a.rz) * t };
    }
  }
  return x < at(s, 0).x ? at(s, 0) : at(s, s.length - 1);
}

function slice(s: readonly Sec[], from: number, to: number): Sec[] {
  return [sectionAt(s, from), ...s.filter((p) => p.x > from && p.x < to), sectionAt(s, to)];
}

/**
 * Loft through elliptical sections: point = (x, y + ry·sinθ, rz·cosθ). θ starts at the bottom so the seam is hidden;
 * a partial θ range makes strips (cheat lines, windshields). Normals are analytic (∂p/∂x × ∂p/∂θ).
 */
export function loft(sections: readonly Sec[], opts: { radial?: number; thetaStart?: number; thetaLength?: number;
  inflate?: number } = {}): THREE.BufferGeometry {
  const radial = opts.radial ?? 40;
  const t0 = opts.thetaStart ?? -Math.PI / 2;
  const tl = opts.thetaLength ?? Math.PI * 2;
  const grow = opts.inflate ?? 0;
  const n = sections.length;
  const pos: number[] = [];
  const nor: number[] = [];
  const uv: number[] = [];
  const idx: number[] = [];
  const d = (i: number, key: 'y' | 'ry' | 'rz') => {
    const a = at(sections, Math.max(i - 1, 0));
    const b = at(sections, Math.min(i + 1, n - 1));
    return (b[key] - a[key]) / (b.x - a.x || 1);
  };
  for (let i = 0; i < n; i += 1) {
    const s = at(sections, i);
    const ry = s.ry > 0 ? s.ry + grow : 0;
    const rz = s.rz > 0 ? s.rz + grow : 0;
    const dy = d(i, 'y');
    const dry = d(i, 'ry');
    const drz = d(i, 'rz');
    for (let j = 0; j <= radial; j += 1) {
      const th = t0 + (tl * j) / radial;
      const sn = Math.sin(th);
      const cs = Math.cos(th);
      pos.push(s.x, s.y + ry * sn, rz * cs);
      const tx = new THREE.Vector3(1, dy + dry * sn, drz * cs);
      const tt = new THREE.Vector3(0, ry * cs, -rz * sn);
      const nv = new THREE.Vector3().crossVectors(tx, tt);
      if (nv.lengthSq() < 1e-10) nv.set(i === 0 ? -1 : 1, 0, 0);
      nv.normalize();
      nor.push(nv.x, nv.y, nv.z);
      uv.push(U(s.x), (th - t0) / (Math.PI * 2));
    }
  }
  for (let i = 0; i < n - 1; i += 1) {
    for (let j = 0; j < radial; j += 1) {
      const a = i * (radial + 1) + j;
      const b = (i + 1) * (radial + 1) + j;
      idx.push(a, b, a + 1, b, b + 1, a + 1);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.Float32BufferAttribute(nor, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx);
  return g;
}

const body = (sections: readonly S4[]) => loft(smooth(sections));

// ── lifting surfaces ─────────────────────────────────────────────────────
const CHORD_POINTS = 18;
const XC = Array.from({ length: CHORD_POINTS }, (_, i) => (1 - Math.cos((Math.PI * i) / (CHORD_POINTS - 1))) / 2);
const naca = (t: number, x: number) =>
  5 * t * (0.2969 * Math.sqrt(x) - 0.126 * x - 0.3516 * x ** 2 + 0.2843 * x ** 3 - 0.1036 * x ** 4);

/**
 * Tapered, swept lifting surface with a symmetric NACA section. Horizontal surfaces extend along ±Z (side), vertical
 * ones along +Y (then tilted by `cant` about X). Horizontal: `d.z` is the root offset from the centreline (mirrored by
 * `side`); vertical: `d.z` is the absolute position. The tip is closed; the root sits inside the body.
 */
export function surface(d: SurfaceDim, side: 1 | -1 = 1, vertical = false): THREE.BufferGeometry {
  const loop: [number, number][] = [];
  for (let i = CHORD_POINTS - 1; i >= 0; i -= 1) loop.push([at(XC, i), naca(d.thick, at(XC, i))]);
  for (let i = 1; i < CHORD_POINTS; i += 1) loop.push([at(XC, i), -naca(d.thick, at(XC, i))]);
  const L = loop.length;
  const sweep = Math.tan(d.sweep * DEG);
  const dih = (d.dihedral ?? 0) * DEG;
  const cant = side * (d.cant ?? 0) * DEG;
  const pos: number[] = [];
  const uv: number[] = [];
  const stations = 6;
  const point = (s: number, xc: number, yt: number): Vec3 => {
    const chord = d.root + (d.tip - d.root) * s;
    const x = d.x - sweep * d.span * s - xc * chord;
    if (!vertical) {
      return [x, d.y + d.span * s * Math.sin(dih) + yt * chord, side * (d.z + d.span * s * Math.cos(dih))];
    }
    const yy = d.span * s;
    const zz = yt * chord;
    return [x, d.y + yy * Math.cos(cant) - zz * Math.sin(cant), d.z + yy * Math.sin(cant) + zz * Math.cos(cant)];
  };
  for (let k = 0; k < stations; k += 1) {
    const s = k / (stations - 1);
    for (let m = 0; m < L; m += 1) {
      const [xc, yt] = at(loop, m);
      const p = point(s, xc, yt);
      pos.push(...p);
      uv.push(U(p[0]), vertical ? 0.2 + p[1] / 10 : 0.24 + (p[2] + 7) / 22);
    }
  }
  const idx: number[] = [];
  const flip = vertical || side < 0;
  for (let k = 0; k < stations - 1; k += 1) {
    for (let m = 0; m < L - 1; m += 1) {
      const a = k * L + m;
      const b = (k + 1) * L + m;
      if (flip) idx.push(a, a + 1, b, b, a + 1, b + 1);
      else idx.push(a, b, a + 1, b, b + 1, a + 1);
    }
  }
  // tip cap (own vertices so the sharp edge stays crisp)
  const base = pos.length / 3;
  const tip = Array.from({ length: L }, (_, m) => point(1, at(loop, m)[0], at(loop, m)[1]));
  const centre = tip.reduce<Vec3>((acc, p) => [acc[0] + p[0] / L, acc[1] + p[1] / L, acc[2] + p[2] / L], [0, 0, 0]);
  for (const p of [...tip, centre]) {
    pos.push(...p);
    uv.push(U(p[0]), 0.5);
  }
  for (let m = 0; m < L - 1; m += 1) idx.push(base + m, base + m + 1, base + L);
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

// ── turned parts, wheels, gear ──────────────────────────────────────────
/** Lathe around the X axis; profile points are [x, radius]. */
export function revolve(profile: readonly [number, number][], segments = 32): THREE.BufferGeometry {
  const g = new THREE.LatheGeometry(profile.map(([x, r]) => new THREE.Vector2(Math.max(r, 0), x)), segments);
  g.rotateZ(-Math.PI / 2);
  return g;
}

/** Flat elliptical disc facing ±X (intake mouths, nozzle exits). */
function disc(ry: number, rz: number, facing: 1 | -1): THREE.BufferGeometry {
  const g = new THREE.CircleGeometry(1, 28);
  g.rotateY((facing * Math.PI) / 2);
  g.scale(1, ry, rz);
  return g;
}

function cylinderBetween(from: Vec3, to: Vec3, r: number): Pick<PartDef, 'geometry' | 'position' | 'rotation'> {
  const a = new THREE.Vector3(...from);
  const b = new THREE.Vector3(...to);
  const g = new THREE.CylinderGeometry(r, r, a.distanceTo(b), 10);
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), b.clone().sub(a).normalize());
  const e = new THREE.Euler().setFromQuaternion(q);
  const mid = a.add(b).multiplyScalar(0.5);
  return { geometry: g, position: [mid.x, mid.y, mid.z], rotation: [e.x, e.y, e.z] };
}

function wheel(zone: Zone, x: number, y: number, z: number, r: number, width: number): PartDef[] {
  const tire = new THREE.TorusGeometry(r - width / 2, width / 2, 14, 32);
  const hub = new THREE.CylinderGeometry(r * 0.55, r * 0.55, width * 1.04, 20);
  hub.rotateX(Math.PI / 2);
  return [
    { zone, kind: 'rubber', geometry: tire, position: [x, y, z] },
    { zone, kind: 'metal', geometry: hub, position: [x, y, z] },
  ];
}

function gearLeg(zone: Zone, x: number, z: number, top: number, ground: number, r: number, twin = false): PartDef[] {
  const axle = ground + r;
  const parts: PartDef[] = [{ zone, kind: 'metal', ...cylinderBetween([x, top, z], [x, axle, z], Math.max(0.045, r * 0.2)) }];
  const width = r * 0.6;
  if (twin) {
    const off = width / 2 + 0.04;
    parts.push(...wheel(zone, x, axle, z - off, r, width), ...wheel(zone, x, axle, z + off, r, width));
  } else {
    parts.push(...wheel(zone, x, axle, z, r, width));
  }
  return parts;
}

function gear(spec: ShapeSpec): PartDef[] {
  const g = spec.gear;
  return [
    ...gearLeg('GEAR_NOSE', g.noseX, 0, g.noseTop, spec.groundY, g.noseR, g.twin),
    ...gearLeg('GEAR_MAIN', g.mainX, -g.mainZ, g.mainTop, spec.groundY, g.mainR, g.twin),
    ...gearLeg('GEAR_MAIN', g.mainX, g.mainZ, g.mainTop, spec.groundY, g.mainR, g.twin),
  ];
}

// ── shared assemblies ────────────────────────────────────────────────────
function fuselage(spec: ShapeSpec, skin: MaterialKind, noseKind: MaterialKind = skin): PartDef[] {
  const s = smooth(spec.body);
  return spec.segments.map(({ zone, from, to }) => ({
    zone, kind: zone === 'NOSE' ? noseKind : skin, geometry: loft(slice(s, from, to)),
  }));
}

function wings(spec: ShapeSpec, skin: MaterialKind): PartDef[] {
  return [
    { zone: 'WING_L', kind: skin, geometry: surface(spec.wing, -1) },
    { zone: 'WING_R', kind: skin, geometry: surface(spec.wing, 1) },
  ];
}

function empennage(spec: ShapeSpec, finKind: MaterialKind, skin: MaterialKind): PartDef[] {
  const fins = spec.twinFins ? [{ ...spec.fin, z: -spec.twinFins }, { ...spec.fin, z: spec.twinFins }] : [spec.fin];
  return [
    ...fins.map((f): PartDef => ({ zone: 'TAIL', kind: finKind, geometry: surface(f, f.z < 0 ? -1 : 1, true) })),
    { zone: 'TAIL', kind: skin, geometry: surface(spec.stab, -1) },
    { zone: 'TAIL', kind: skin, geometry: surface(spec.stab, 1) },
  ];
}

function wingTip(spec: ShapeSpec): { x: number; y: number; z: number } {
  const w = spec.wing;
  const dih = (w.dihedral ?? 0) * DEG;
  return {
    x: w.x - Math.tan(w.sweep * DEG) * w.span, y: w.y + w.span * Math.sin(dih), z: w.z + w.span * Math.cos(dih),
  };
}

function wingTipLights(spec: ShapeSpec): PartDef[] {
  const t = wingTip(spec);
  const x = t.x - spec.wing.tip * 0.25;
  const bulb = () => new THREE.SphereGeometry(0.06, 12, 8);
  return [
    { zone: 'WING_L', kind: 'navRed', geometry: bulb(), position: [x, t.y, -(t.z + 0.03)], detail: true },
    { zone: 'WING_R', kind: 'navGreen', geometry: bulb(), position: [x, t.y, t.z + 0.03], detail: true },
  ];
}

function cheatLines(spec: ShapeSpec): PartDef[] {
  const s = smooth(spec.body);
  const part = slice(s, -spec.length * 0.38, spec.length * 0.3);
  const band = (start: number): PartDef => ({
    zone: 'FUSELAGE', kind: 'accent', geometry: loft(part, { thetaStart: start, thetaLength: 0.13, inflate: 0.006, radial: 4 }),
  });
  return [band(-0.02), band(Math.PI - 0.11)];
}

function windshield(spec: ShapeSpec): PartDef[] {
  if (!spec.windshield) return [];
  const s = smooth(spec.body);
  const part = slice(s, spec.windshield.from, spec.windshield.to);
  return [{ zone: 'COCKPIT', kind: 'glass', geometry: loft(part, { thetaStart: 0.4, thetaLength: Math.PI - 0.8, inflate: 0.012 }) }];
}

function canopy(spec: ShapeSpec): PartDef[] {
  return spec.canopy ? [{ zone: 'COCKPIT', kind: 'glass', geometry: body(spec.canopy) }] : [];
}

function windows(spec: ShapeSpec, xs: number[], y: number, r = 0.15): PartDef[] {
  const s = smooth(spec.body);
  return xs.flatMap((x) => {
    const sec = sectionAt(s, x);
    const off = sec.rz * Math.sqrt(Math.max(0, 1 - ((y - sec.y) / (sec.ry || 1)) ** 2)) + 0.006;
    return ([1, -1] as const).map((side): PartDef => {
      const g = new THREE.CircleGeometry(r, 18);
      g.scale(1, 1.3, 1);
      return { zone: 'FUSELAGE', kind: 'dark', geometry: g, position: [x, y, side * off], rotation: [0, side === 1 ? 0 : Math.PI, 0], detail: true };
    });
  });
}

/** Propeller at hub x around the X axis through (y, z): spinner (spinnerZone) + pitched blades (bladeZone). */
function propeller(spinnerZone: Zone, bladeZone: Zone, hubX: number, y: number, z: number, blades: number,
  length: number, spinner: [number, number][]): PartDef[] {
  const parts: PartDef[] = [{ zone: spinnerZone, kind: 'metal', geometry: revolve(spinner), position: [0, y, z] }];
  const hubR = at(spinner, 0)[1] * 0.8;
  for (let k = 0; k < blades; k += 1) {
    const g = surface({ root: 0.24, tip: 0.13, span: length, sweep: 0, thick: 0.09, x: 0.12, y: hubR, z: 0 }, 1, true);
    g.rotateY(24 * DEG); // blade pitch
    g.rotateX((2 * Math.PI * k) / blades + Math.PI / blades);
    parts.push({ zone: bladeZone, kind: 'prop', geometry: g, position: [hubX, y, z] });
  }
  return parts;
}

// ── families ─────────────────────────────────────────────────────────────
function trainer(spec: ShapeSpec): PartDef[] {
  const exhaust = (side: 1 | -1): PartDef => {
    const g = new THREE.CylinderGeometry(0.07, 0.08, 0.32, 12);
    g.rotateX(Math.PI / 2);
    return { zone: 'ENGINE_1', kind: 'dark', geometry: g, position: [3.55, -0.05, side * 0.52] };
  };
  return [
    ...fuselage(spec, 'paint'), ...canopy(spec), ...wings(spec, 'paint'), ...empennage(spec, 'accent', 'paint'),
    ...cheatLines(spec), ...wingTipLights(spec), ...gear(spec), exhaust(1), exhaust(-1),
    ...propeller('NOSE', 'ENGINE_1', 4.6, 0, 0, 4, 1.2, [[4.55, 0.3], [4.7, 0.29], [4.85, 0.22], [4.98, 0.1], [5.0, 0]]),
  ];
}

function turboprop(spec: ShapeSpec): PartDef[] {
  const nacelle: S4[] = [[3.05, 0.78, 0.36, 0.33], [2.7, 0.76, 0.46, 0.4], [1.6, 0.72, 0.5, 0.42], [0.4, 0.75, 0.42, 0.34],
    [-0.7, 0.86, 0.14, 0.11]];
  const sponson: S4[] = [[1.2, -0.72, 0, 0], [0.9, -0.72, 0.3, 0.28], [-0.6, -0.72, 0.32, 0.3], [-1.6, -0.65, 0.18, 0.16],
    [-2.0, -0.6, 0, 0]];
  const fairing: S4[] = [[2.3, 1.0, 0, 0], [1.9, 1.02, 0.18, 0.55], [0, 1.02, 0.22, 0.62], [-1.0, 1.0, 0.12, 0.4], [-1.4, 0.98, 0, 0]];
  const engine = (zone: Zone, z: number): PartDef[] => [
    { zone, kind: 'paint', geometry: body(nacelle), position: [0, 0, z] },
    ...propeller(zone, zone, 3.12, 0.78, z, 4, 1.45, [[3.05, 0.3], [3.25, 0.28], [3.5, 0.2], [3.7, 0.08], [3.75, 0]]),
  ];
  return [
    ...fuselage(spec, 'paint'), ...windshield(spec), ...wings(spec, 'paint'), ...empennage(spec, 'accent', 'paint'),
    { zone: 'FUSELAGE', kind: 'paint', geometry: body(fairing) },
    { zone: 'GEAR_MAIN', kind: 'paint', geometry: body(sponson), position: [0, 0, -1.0] },
    { zone: 'GEAR_MAIN', kind: 'paint', geometry: body(sponson), position: [0, 0, 1.0] },
    ...engine('ENGINE_1', -2.9), ...engine('ENGINE_2', 2.9),
    ...cheatLines(spec), ...wingTipLights(spec), ...gear(spec),
    ...windows(spec, Array.from({ length: 9 }, (_, i) => -2.6 + i * 0.78), 0.28),
  ];
}

function bizjet(spec: ShapeSpec): PartDef[] {
  const nacelle: S4[] = [[-2.05, 0.5, 0.36, 0.36], [-2.35, 0.5, 0.42, 0.42], [-3.6, 0.5, 0.42, 0.42], [-4.5, 0.5, 0.34, 0.34],
    [-4.75, 0.5, 0.3, 0.3]];
  const tip = wingTip(spec);
  const winglet = (side: 1 | -1): PartDef => ({
    zone: side === 1 ? 'WING_R' : 'WING_L', kind: 'accent',
    geometry: surface({ root: 0.85, tip: 0.4, span: 0.95, sweep: 40, thick: 0.08, x: tip.x - 0.05, y: tip.y, z: side * tip.z, cant: 12 }, side, true),
  });
  const engine = (zone: Zone, side: 1 | -1): PartDef[] => [
    { zone, kind: 'paint', geometry: body(nacelle), position: [0, 0, side * 1.45] },
    { zone, kind: 'dark', geometry: disc(0.3, 0.3, 1), position: [-2.04, 0.5, side * 1.45] },
    { zone, kind: 'dark', geometry: disc(0.26, 0.26, -1), position: [-4.76, 0.5, side * 1.45] },
    { zone, kind: 'paint', geometry: surface({ root: 1.3, tip: 1.1, span: 0.75, sweep: 8, thick: 0.22, x: -2.75, y: 0.5, z: 0.55 }, side) },
  ];
  return [
    ...fuselage(spec, 'paint'), ...windshield(spec), ...wings(spec, 'paint'), winglet(1), winglet(-1),
    ...empennage(spec, 'accent', 'paint'), ...engine('ENGINE_1', -1), ...engine('ENGINE_2', 1),
    ...cheatLines(spec), ...wingTipLights(spec), ...gear(spec),
    ...windows(spec, Array.from({ length: 6 }, (_, i) => -1.4 + i * 0.85), 0.18, 0.14),
  ];
}

function nozzle(zone: Zone, x0: number, length: number, r0: number, z: number): PartDef[] {
  const profile: [number, number][] = [[x0, r0], [x0 - length * 0.3, r0 * 0.98], [x0 - length * 0.75, r0 * 0.92], [x0 - length, r0 * 0.88]];
  return [
    { zone, kind: 'nozzle', geometry: revolve(profile), position: [0, 0, z] },
    { zone, kind: 'dark', geometry: disc(r0 * 0.72, r0 * 0.72, -1), position: [x0 - length * 0.55, 0, z] },
  ];
}

function missile(zone: Zone, z: number): PartDef[] {
  const g = revolve([[-1.45, 0], [-1.4, 0.065], [1.15, 0.065], [1.4, 0.03], [1.5, 0]], 16);
  return [{ zone, kind: 'missile', geometry: g, position: [-1.0, -0.05, z], detail: true }];
}

function fighterSingle(spec: ShapeSpec): PartDef[] {
  const spine: S4[] = [[1.3, 0.5, 0, 0], [1.0, 0.55, 0.2, 0.24], [-0.8, 0.55, 0.26, 0.32], [-3.4, 0.48, 0.2, 0.26],
    [-5.0, 0.42, 0.08, 0.1], [-5.4, 0.4, 0, 0]];
  const cft: S4[] = [[1.8, 0.3, 0, 0], [1.4, 0.32, 0.18, 0.18], [0.2, 0.34, 0.26, 0.26], [-2.2, 0.3, 0.22, 0.22], [-3.2, 0.26, 0, 0]];
  const intake: S4[] = [[3.7, -0.7, 0.34, 0.4], [3.2, -0.7, 0.37, 0.43], [1.8, -0.62, 0.36, 0.44], [0.2, -0.42, 0.26, 0.36],
    [-1.2, -0.22, 0.08, 0.16]];
  const lerx = (side: 1 | -1): PartDef => ({
    zone: side === 1 ? 'WING_R' : 'WING_L', kind: 'camo',
    geometry: surface({ root: 3.4, tip: 1.4, span: 0.5, sweep: 70, thick: 0.03, x: 4.35, y: -0.03, z: 0.5 }, side),
  });
  const ventral = (side: 1 | -1): PartDef => ({
    zone: 'TAIL', kind: 'camo',
    geometry: surface({ root: 0.95, tip: 0.6, span: 0.55, sweep: 35, thick: 0.04, x: -4.1, y: -0.42, z: side * 0.32, cant: 155 }, side, true),
  });
  const pitot = new THREE.CylinderGeometry(0.018, 0.018, 0.55, 8);
  pitot.rotateZ(Math.PI / 2);
  return [
    ...fuselage(spec, 'camo', 'radome'), ...canopy(spec), ...wings(spec, 'camo'), lerx(1), lerx(-1),
    ...empennage(spec, 'camo', 'camo'), ventral(1), ventral(-1),
    { zone: 'FUSELAGE', kind: 'camo', geometry: body(spine) },
    { zone: 'FUSELAGE', kind: 'camo', geometry: body(cft), position: [0, 0, -0.72] },
    { zone: 'FUSELAGE', kind: 'camo', geometry: body(cft), position: [0, 0, 0.72] },
    { zone: 'ENGINE_1', kind: 'camo', geometry: body(intake) },
    { zone: 'ENGINE_1', kind: 'dark', geometry: disc(0.3, 0.36, 1), position: [3.71, -0.7, 0] },
    ...nozzle('ENGINE_1', -6.9, 0.8, 0.45, 0),
    { zone: 'NOSE', kind: 'metal', geometry: pitot, position: [7.75, -0.01, 0], detail: true },
    ...missile('WING_L', -4.82), ...missile('WING_R', 4.82), ...gear(spec),
  ];
}

function fighterTwin(spec: ShapeSpec): PartDef[] {
  const spine: S4[] = [[2.6, 0.55, 0, 0], [2.2, 0.62, 0.18, 0.26], [-1.5, 0.62, 0.22, 0.4], [-4.0, 0.55, 0.12, 0.3], [-4.8, 0.52, 0, 0]];
  const intake: S4[] = [[4.35, -0.12, 0.58, 0.4], [3.0, -0.16, 0.62, 0.44], [0.5, -0.16, 0.6, 0.46], [-2.0, -0.12, 0.52, 0.42],
    [-3.0, -0.1, 0.3, 0.25]];
  const side = (zone: Zone, s: 1 | -1): PartDef[] => [
    { zone, kind: 'camo', geometry: body(intake), position: [0, 0, s * 1.18] },
    { zone, kind: 'dark', geometry: disc(0.5, 0.34, 1), position: [4.36, -0.12, s * 1.18] },
    ...nozzle(zone, -8.15, 1.35, 0.55, s * 0.62),
  ];
  return [
    ...fuselage(spec, 'camo', 'radome'), ...canopy(spec), ...wings(spec, 'camo'), ...empennage(spec, 'camo', 'camo'),
    { zone: 'FUSELAGE', kind: 'camo', geometry: body(spine) },
    ...side('ENGINE_1', -1), ...side('ENGINE_2', 1), ...gear(spec),
  ];
}

const BUILDERS: Record<ShapeSpec['family'], (spec: ShapeSpec) => PartDef[]> = {
  prop: trainer,
  turboprop,
  bizjet,
  fighter: (spec) => (spec.engines === 2 ? fighterTwin(spec) : fighterSingle(spec)),
};
const cache = new Map<ShapeSpec, PartDef[]>();

/** All parts of a shape (cached; geometries are shared by every aircraft of that shape). */
export function buildParts(shape: string): PartDef[] {
  const spec = specFor(shape);
  const hit = cache.get(spec);
  if (hit) return hit;
  const parts = BUILDERS[spec.family](spec);
  cache.set(spec, parts);
  return parts;
}
