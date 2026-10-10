/**
 * Aircraft shapes for the 3D viewer (DESIGN_SYSTEM.md §6.2): real-world proportions in metres, zone anchors and
 * marking positions. Geometry is generated from these numbers in procedural.ts. Nose → +X, up → +Y, right wing → +Z;
 * the fuselage axis is y = 0 and the wheels touch `groundY`.
 */

export const ZONES = [
  'NOSE', 'COCKPIT', 'FUSELAGE', 'WING_L', 'WING_R', 'ENGINE_1', 'ENGINE_2', 'GEAR_NOSE', 'GEAR_MAIN', 'TAIL',
] as const;
export type Zone = (typeof ZONES)[number];
export type Shape =
  | 'SINGLE_PROP_LOW_WING' | 'TWIN_TURBOPROP_HIGH_WING' | 'TWIN_JET_LOW_WING' | 'FIGHTER_SINGLE' | 'FIGHTER_TWIN';
export type Vec3 = [number, number, number];
/** Elliptical cross-section: [x, centre y, half-height, half-width]. */
export type S4 = [number, number, number, number];

// `length` is the overall length incl. tail-surface overhang and pitot (used for camera framing).
/** Lifting surface: root/tip chord, semi-span, leading-edge sweep and dihedral (deg), thickness ratio, root LE. */
export interface SurfaceDim {
  root: number;
  tip: number;
  span: number;
  sweep: number;
  dihedral?: number;
  thick: number;
  x: number;
  y: number;
  z: number;
  cant?: number; // vertical surfaces: tilt about X, degrees (180 = pointing down)
}

export interface ShapeSpec {
  family: 'prop' | 'turboprop' | 'bizjet' | 'fighter';
  engines: 1 | 2;
  length: number;
  span: number;
  groundY: number;
  camo: boolean;
  body: S4[];
  segments: { zone: Zone; from: number; to: number }[]; // fuselage split into maintenance zones
  canopy?: S4[];
  windshield?: { from: number; to: number };
  wing: SurfaceDim;
  fin: SurfaceDim;
  twinFins?: number; // |z| of twin vertical tails
  stab: SurfaceDim;
  gear: { noseX: number; noseTop: number; noseR: number; mainX: number; mainZ: number; mainTop: number; mainR: number;
    twin?: boolean };
  anchors: Partial<Record<Zone, Vec3>>;
  registration: { position: Vec3; size: number };
  tailMarks: { position: Vec3; side: 1 | -1; size: number }[];
}

export const SHAPES: Record<Shape, ShapeSpec> = {
  // Turboprop trainer (tandem two-seat, bubble canopy, 4-blade propeller)
  SINGLE_PROP_LOW_WING: {
    family: 'prop', engines: 1, length: 10, span: 10.1, groundY: -1.5, camo: false,
    body: [[4.55, 0, 0.4, 0.38], [4.2, 0, 0.5, 0.47], [3.5, 0.02, 0.58, 0.53], [2.6, 0.05, 0.62, 0.55],
      [1.6, 0.06, 0.62, 0.55], [0.4, 0.06, 0.6, 0.52], [-1, 0.1, 0.52, 0.44], [-2.6, 0.2, 0.38, 0.31],
      [-3.9, 0.32, 0.24, 0.19], [-4.75, 0.4, 0.1, 0.08], [-5, 0.42, 0, 0]],
    segments: [{ zone: 'ENGINE_1', from: 3.0, to: 4.55 }, { zone: 'COCKPIT', from: 0, to: 3.0 },
      { zone: 'FUSELAGE', from: -2.4, to: 0 }, { zone: 'TAIL', from: -5, to: -2.4 }],
    canopy: [[2.35, 0.55, 0, 0], [2.1, 0.58, 0.2, 0.24], [1.5, 0.62, 0.36, 0.34], [0.6, 0.62, 0.38, 0.34],
      [-0.2, 0.58, 0.28, 0.28], [-0.55, 0.52, 0, 0]],
    wing: { root: 2.1, tip: 1.1, span: 4.65, sweep: 4, dihedral: 6, thick: 0.15, x: 1.3, y: -0.36, z: 0.45 },
    fin: { root: 1.7, tip: 0.75, span: 1.55, sweep: 38, thick: 0.11, x: -3.3, y: 0.3, z: 0 },
    stab: { root: 1.0, tip: 0.55, span: 1.7, sweep: 10, thick: 0.1, x: -3.75, y: 0.32, z: 0.18 },
    gear: { noseX: 3.2, noseTop: -0.4, noseR: 0.24, mainX: 0.55, mainZ: 1.55, mainTop: -0.3, mainR: 0.3 },
    anchors: { NOSE: [5.2, 0.15, 0], COCKPIT: [1.0, 1.1, 0], FUSELAGE: [-1.2, 0.75, 0], WING_L: [0.4, 0, -3.6],
      WING_R: [0.4, 0, 3.6], ENGINE_1: [3.8, 0.85, 0], GEAR_NOSE: [3.2, -0.95, 0], GEAR_MAIN: [0.55, -0.95, 1.55],
      TAIL: [-3.9, 2.0, 0] },
    registration: { position: [-1.6, 0.12, 0.41], size: 0.22 },
    tailMarks: [{ position: [-4.52, 1.07, 0.08], side: 1, size: 0.26 }, { position: [-4.52, 1.07, -0.08], side: -1, size: 0.26 }],
  },
  // High-wing twin turboprop transport (T-tail, main gear in fuselage sponsons)
  TWIN_TURBOPROP_HIGH_WING: {
    family: 'turboprop', engines: 2, length: 15, span: 14.5, groundY: -1.85, camo: false,
    body: [[7.5, -0.12, 0, 0], [7.35, -0.12, 0.32, 0.35], [7.0, -0.1, 0.6, 0.62], [6.3, -0.04, 0.88, 0.86],
      [5.4, 0, 1.05, 1.0], [4.0, 0, 1.1, 1.02], [-2.0, 0, 1.1, 1.02], [-3.6, 0.15, 0.95, 0.86],
      [-5.1, 0.45, 0.64, 0.56], [-6.6, 0.78, 0.32, 0.27], [-7.4, 0.92, 0.1, 0.08], [-7.5, 0.94, 0, 0]],
    segments: [{ zone: 'NOSE', from: 6.3, to: 7.5 }, { zone: 'COCKPIT', from: 4.6, to: 6.3 },
      { zone: 'FUSELAGE', from: -3.6, to: 4.6 }, { zone: 'TAIL', from: -7.5, to: -3.6 }],
    windshield: { from: 5.05, to: 6.2 },
    wing: { root: 2.5, tip: 1.3, span: 7.0, sweep: 3, dihedral: -1, thick: 0.17, x: 1.7, y: 1.08, z: 0.25 },
    fin: { root: 2.9, tip: 1.6, span: 2.7, sweep: 33, thick: 0.12, x: -4.3, y: 0.85, z: 0 },
    stab: { root: 1.5, tip: 0.8, span: 2.7, sweep: 12, thick: 0.1, x: -6.0, y: 3.55, z: 0.05 },
    gear: { noseX: 5.7, noseTop: -0.85, noseR: 0.26, mainX: -0.3, mainZ: 1.15, mainTop: -0.75, mainR: 0.36, twin: true },
    anchors: { NOSE: [7.6, 0.2, 0], COCKPIT: [5.7, 1.15, 0], FUSELAGE: [-2.4, 1.3, 0], WING_L: [1.0, 1.5, -5.2],
      WING_R: [1.0, 1.5, 5.2], ENGINE_1: [1.8, 1.45, -2.9], ENGINE_2: [1.8, 1.45, 2.9], GEAR_NOSE: [5.7, -1.3, 0],
      GEAR_MAIN: [-0.3, -1.3, 1.25], TAIL: [-5.6, 3.95, 0] },
    registration: { position: [-4.0, 0.35, 0.79], size: 0.28 },
    tailMarks: [{ position: [-6.3, 2.2, 0.15], side: 1, size: 0.38 }, { position: [-6.3, 2.2, -0.15], side: -1, size: 0.38 }],
  },
  // Business jet: swept low wing with winglets, rear-mounted engines, T-tail
  TWIN_JET_LOW_WING: {
    family: 'bizjet', engines: 2, length: 15.9, span: 13.3, groundY: -1.45, camo: false,
    body: [[7.25, -0.05, 0, 0], [7.05, -0.06, 0.22, 0.22], [6.6, -0.04, 0.45, 0.43], [5.8, 0, 0.7, 0.66],
      [4.8, 0, 0.85, 0.8], [-1.5, 0.02, 0.85, 0.8], [-3.0, 0.12, 0.72, 0.66], [-4.6, 0.3, 0.5, 0.44],
      [-6.2, 0.5, 0.25, 0.2], [-7.15, 0.6, 0.07, 0.06], [-7.25, 0.62, 0, 0]],
    segments: [{ zone: 'NOSE', from: 5.9, to: 7.25 }, { zone: 'COCKPIT', from: 4.3, to: 5.9 },
      { zone: 'FUSELAGE', from: -3.2, to: 4.3 }, { zone: 'TAIL', from: -7.25, to: -3.2 }],
    windshield: { from: 4.75, to: 5.85 },
    wing: { root: 2.7, tip: 0.95, span: 6.3, sweep: 25, dihedral: 3, thick: 0.12, x: 1.3, y: -0.5, z: 0.35 },
    fin: { root: 2.6, tip: 1.4, span: 2.2, sweep: 45, thick: 0.11, x: -4.4, y: 0.55, z: 0 },
    stab: { root: 1.35, tip: 0.7, span: 2.4, sweep: 30, thick: 0.1, x: -6.55, y: 2.75, z: 0.05 },
    gear: { noseX: 5.3, noseTop: -0.7, noseR: 0.22, mainX: -0.7, mainZ: 1.6, mainTop: -0.55, mainR: 0.3 },
    anchors: { NOSE: [7.4, 0.1, 0], COCKPIT: [5.2, 0.95, 0], FUSELAGE: [0.8, 1.05, 0], WING_L: [-0.5, 0, -4.6],
      WING_R: [-0.5, 0, 4.6], ENGINE_1: [-3.3, 1.15, -1.45], ENGINE_2: [-3.3, 1.15, 1.45], GEAR_NOSE: [5.3, -1.0, 0],
      GEAR_MAIN: [-0.7, -1.05, 1.6], TAIL: [-6.6, 3.1, 0] },
    registration: { position: [3.4, -0.3, 0.76], size: 0.26 },
    tailMarks: [{ position: [-6.5, 1.65, 0.125], side: 1, size: 0.3 }, { position: [-6.5, 1.65, -0.125], side: -1, size: 0.3 }],
  },
  // Single-engine multirole fighter (F-16I-like): ventral intake, two-seat bubble canopy, conformal tanks
  FIGHTER_SINGLE: {
    family: 'fighter', engines: 1, length: 15.4, span: 9.9, groundY: -2.0, camo: true,
    body: [[7.5, 0, 0, 0], [7.15, -0.02, 0.15, 0.15], [6.4, -0.02, 0.33, 0.33], [5.5, 0, 0.47, 0.47],
      [4.5, 0.02, 0.56, 0.56], [3.4, 0.04, 0.6, 0.62], [2.2, 0.02, 0.6, 0.72], [0.8, 0, 0.6, 0.8],
      [-1.0, 0, 0.6, 0.82], [-3.0, 0, 0.56, 0.7], [-4.8, 0, 0.5, 0.58], [-6.2, 0, 0.47, 0.5], [-6.9, 0, 0.45, 0.46]],
    segments: [{ zone: 'NOSE', from: 5.6, to: 7.5 }, { zone: 'COCKPIT', from: 1.6, to: 5.6 },
      { zone: 'FUSELAGE', from: -4.2, to: 1.6 }, { zone: 'TAIL', from: -6.9, to: -4.2 }],
    canopy: [[5.1, 0.48, 0, 0], [4.8, 0.52, 0.2, 0.22], [4.1, 0.62, 0.33, 0.3], [2.9, 0.68, 0.37, 0.31],
      [1.8, 0.64, 0.32, 0.28], [1.1, 0.58, 0.18, 0.2], [0.8, 0.55, 0, 0]],
    wing: { root: 4.9, tip: 1.05, span: 4.1, sweep: 40, dihedral: 0, thick: 0.045, x: 1.25, y: -0.05, z: 0.62 },
    fin: { root: 3.3, tip: 1.15, span: 2.75, sweep: 46, thick: 0.05, x: -3.05, y: 0.5, z: 0 },
    stab: { root: 2.1, tip: 0.85, span: 2.2, sweep: 40, dihedral: -10, thick: 0.04, x: -4.95, y: 0, z: 0.5 },
    gear: { noseX: 3.0, noseTop: -1.0, noseR: 0.3, mainX: -1.0, mainZ: 1.05, mainTop: -0.5, mainR: 0.38 },
    anchors: { NOSE: [6.6, 0.6, 0], COCKPIT: [3.0, 1.4, 0], FUSELAGE: [-1.6, 0.95, 0], WING_L: [-0.8, 0.3, -3.3],
      WING_R: [-0.8, 0.3, 3.3], ENGINE_1: [-6.8, 0.95, 0], GEAR_NOSE: [3.0, -1.45, 0], GEAR_MAIN: [-1.0, -1.4, 1.05],
      TAIL: [-4.9, 3.55, 0] },
    registration: { position: [-3.4, -0.05, 0.68], size: 0.24 },
    tailMarks: [{ position: [-5.58, 1.88, 0.068], side: 1, size: 0.42 }, { position: [-5.58, 1.88, -0.068], side: -1, size: 0.42 }],
  },
  // Twin-engine air-superiority/strike fighter (F-15I-like): side intakes, twin tails, two-seat canopy
  FIGHTER_TWIN: {
    family: 'fighter', engines: 2, length: 20.4, span: 13.0, groundY: -2.05, camo: true,
    body: [[9.7, 0, 0, 0], [9.2, 0, 0.2, 0.2], [8.2, 0, 0.42, 0.42], [7.0, 0.02, 0.6, 0.58], [5.8, 0.05, 0.7, 0.62],
      [4.6, 0.05, 0.72, 0.66], [3.0, 0.05, 0.66, 0.98], [0.5, 0.05, 0.6, 1.32], [-3.0, 0.05, 0.56, 1.38],
      [-6.0, 0.02, 0.52, 1.32], [-7.6, 0, 0.5, 1.25], [-8.2, 0, 0.42, 1.12], [-8.3, 0, 0.2, 0.5], [-8.35, 0, 0, 0]],
    segments: [{ zone: 'NOSE', from: 7.2, to: 9.7 }, { zone: 'COCKPIT', from: 4.6, to: 7.2 },
      { zone: 'FUSELAGE', from: -6.4, to: 4.6 }, { zone: 'TAIL', from: -8.35, to: -6.4 }],
    canopy: [[7.0, 0.55, 0, 0], [6.6, 0.62, 0.22, 0.25], [5.6, 0.78, 0.36, 0.33], [4.1, 0.84, 0.38, 0.34],
      [2.8, 0.76, 0.3, 0.28], [2.2, 0.66, 0, 0]],
    wing: { root: 6.0, tip: 1.6, span: 5.15, sweep: 45, dihedral: -1, thick: 0.05, x: 2.0, y: 0.42, z: 1.35 },
    fin: { root: 3.1, tip: 1.45, span: 2.9, sweep: 36, thick: 0.05, x: -5.55, y: 0.6, z: 0 },
    twinFins: 1.05,
    stab: { root: 2.5, tip: 1.0, span: 2.65, sweep: 48, dihedral: 0, thick: 0.04, x: -6.75, y: 0, z: 1.55 },
    gear: { noseX: 6.2, noseTop: -0.6, noseR: 0.33, mainX: -1.3, mainZ: 1.45, mainTop: -0.7, mainR: 0.46 },
    anchors: { NOSE: [8.6, 0.65, 0], COCKPIT: [4.8, 1.55, 0], FUSELAGE: [-1.5, 1.05, 0], WING_L: [-1.0, 0.9, -4.4],
      WING_R: [-1.0, 0.9, 4.4], ENGINE_1: [-8.7, 1.1, -0.62], ENGINE_2: [-8.7, 1.1, 0.62], GEAR_NOSE: [6.2, -1.6, 0],
      GEAR_MAIN: [-1.3, -1.6, 1.45], TAIL: [-7.4, 3.95, 1.05] },
    registration: { position: [1.5, -0.1, 1.66], size: 0.3 },
    tailMarks: [{ position: [-7.74, 2.05, 1.12], side: 1, size: 0.42 }, { position: [-7.74, 2.05, -1.12], side: -1, size: 0.42 }],
  },
};

export function specFor(shape: string): ShapeSpec {
  return SHAPES[shape as Shape] ?? SHAPES.TWIN_TURBOPROP_HIGH_WING;
}

const COUNTERPART: Partial<Record<Shape, Shape>> = {
  FIGHTER_SINGLE: 'FIGHTER_TWIN', FIGHTER_TWIN: 'FIGHTER_SINGLE',
  SINGLE_PROP_LOW_WING: 'TWIN_TURBOPROP_HIGH_WING', TWIN_TURBOPROP_HIGH_WING: 'SINGLE_PROP_LOW_WING',
  TWIN_JET_LOW_WING: 'FIGHTER_SINGLE',
};

/**
 * The model to draw: the type's shape, unless the data's engine count disagrees with it — then the same family's
 * counterpart (engine count always comes from data, DESIGN_SYSTEM.md §6.2).
 */
export function shapeFor(shape: string | null | undefined, engineCount: number): Shape {
  const s: Shape = shape && shape in SHAPES ? (shape as Shape) : engineCount >= 2 ? 'TWIN_TURBOPROP_HIGH_WING' : 'SINGLE_PROP_LOW_WING';
  const wanted = engineCount >= 2 ? 2 : 1;
  if (SHAPES[s].engines === wanted) return s;
  const other = COUNTERPART[s];
  return other && SHAPES[other].engines === wanted ? other : wanted === 2 ? 'TWIN_TURBOPROP_HIGH_WING' : 'SINGLE_PROP_LOW_WING';
}

/** Anchor positions for hotspots. ENGINE_1 is the left engine (−Z); single-engine aircraft have no ENGINE_2. */
export function zoneAnchors(shape: string, engineCount: number): Partial<Record<Zone, Vec3>> {
  const anchors = { ...specFor(shape).anchors };
  if (engineCount < 2) delete anchors.ENGINE_2;
  return anchors;
}

/** Top-view positions (percent of the 2D fallback box; right wing at the bottom). */
export const ZONE_2D: Record<Zone, { x: number; y: number }> = {
  NOSE: { x: 92, y: 50 },
  COCKPIT: { x: 80, y: 50 },
  FUSELAGE: { x: 50, y: 50 },
  WING_L: { x: 52, y: 14 },
  WING_R: { x: 52, y: 86 },
  ENGINE_1: { x: 64, y: 30 },
  ENGINE_2: { x: 64, y: 70 },
  GEAR_NOSE: { x: 84, y: 60 },
  GEAR_MAIN: { x: 46, y: 62 },
  TAIL: { x: 8, y: 50 },
};

/** ATA chapter → zone (fallback when an MA has no zone; mirrors DATABASE.md §3). */
export function zoneForAta(ata: number | null | undefined): Zone | null {
  if (ata === null || ata === undefined) return null;
  if (ata >= 21 && ata <= 26) return 'FUSELAGE';
  if (ata === 27 || ata === 30 || ata === 57) return 'WING_L';
  if (ata === 28) return 'WING_R';
  if (ata === 31 || ata === 34) return 'NOSE';
  if (ata === 32) return 'GEAR_MAIN';
  if ((ata >= 33 && ata <= 38) || ata === 52 || ata === 53) return 'FUSELAGE';
  if (ata === 55) return 'TAIL';
  if (ata === 61 || (ata >= 71 && ata <= 80)) return 'ENGINE_1';
  return null;
}
