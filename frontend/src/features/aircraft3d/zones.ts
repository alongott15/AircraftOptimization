/** Procedural aircraft geometry specs and zone anchors (DESIGN_SYSTEM.md §6.2). Nose → +X, up → +Y, right wing → +Z. */

export const ZONES = [
  'NOSE', 'COCKPIT', 'FUSELAGE', 'WING_L', 'WING_R', 'ENGINE_1', 'ENGINE_2', 'GEAR_NOSE', 'GEAR_MAIN', 'TAIL',
] as const;
export type Zone = (typeof ZONES)[number];
export type Shape = 'SINGLE_PROP_LOW_WING' | 'TWIN_TURBOPROP_HIGH_WING' | 'TWIN_JET_LOW_WING';
export type Vec3 = [number, number, number];

export interface ShapeSpec {
  length: number;
  radius: number;
  span: number;
  chord: number;
  wingY: number;
  wingX: number;
  sweepDeg: number;
  finHeight: number;
  finX: number;
  hstabSpan: number;
  tTail: boolean;
  engineMount: 'nose' | 'wing-nacelle' | 'wing-pod';
  engineZ: number;
  engineY: number;
  engineX: number;
  engineRadius: number;
  engineLength: number;
  propeller: boolean;
  gearHeight: number;
  mainGearZ: number;
  mainGearX: number;
}

export const SHAPES: Record<Shape, ShapeSpec> = {
  SINGLE_PROP_LOW_WING: {
    length: 7, radius: 0.55, span: 9, chord: 1.4, wingY: -0.3, wingX: 0.6, sweepDeg: 0, finHeight: 1.3, finX: -3.0,
    hstabSpan: 3, tTail: false, engineMount: 'nose', engineZ: 0, engineY: 0, engineX: 3.75, engineRadius: 0.5,
    engineLength: 0.9, propeller: true, gearHeight: 0.7, mainGearZ: 1.2, mainGearX: 0.4,
  },
  TWIN_TURBOPROP_HIGH_WING: {
    length: 10, radius: 0.85, span: 14, chord: 1.8, wingY: 0.8, wingX: 0.8, sweepDeg: 0, finHeight: 2.2, finX: -4.3,
    hstabSpan: 4.4, tTail: true, engineMount: 'wing-nacelle', engineZ: 2.8, engineY: 0.5, engineX: 1.7,
    engineRadius: 0.38, engineLength: 2.2, propeller: true, gearHeight: 0.8, mainGearZ: 1.1, mainGearX: 0.2,
  },
  TWIN_JET_LOW_WING: {
    length: 13, radius: 1.0, span: 15, chord: 2.4, wingY: -0.45, wingX: 0.3, sweepDeg: 25, finHeight: 2.4, finX: -5.6,
    hstabSpan: 5.4, tTail: false, engineMount: 'wing-pod', engineZ: 3.2, engineY: -1.05, engineX: 1.1,
    engineRadius: 0.5, engineLength: 2.4, propeller: false, gearHeight: 0.9, mainGearZ: 1.6, mainGearX: -0.3,
  },
};

export function specFor(shape: string): ShapeSpec {
  return SHAPES[shape as Shape] ?? SHAPES.TWIN_TURBOPROP_HIGH_WING;
}

/** Anchor positions for hotspots. ENGINE_1 is the left engine (−Z); single-engine aircraft use the nose. */
export function zoneAnchors(shape: string, engineCount: number): Partial<Record<Zone, Vec3>> {
  const s = specFor(shape);
  const half = s.length / 2;
  const wingHalf = s.span / 2;
  const anchors: Partial<Record<Zone, Vec3>> = {
    NOSE: [half + 0.35, s.radius * 0.2, 0],
    COCKPIT: [half - 0.8, s.radius * 1.05, 0],
    FUSELAGE: [0, s.radius * 1.1, 0],
    WING_L: [s.wingX - 0.3, s.wingY + 0.25, -wingHalf * 0.7],
    WING_R: [s.wingX - 0.3, s.wingY + 0.25, wingHalf * 0.7],
    GEAR_NOSE: [half - 0.9, -s.radius - s.gearHeight * 0.6, 0],
    GEAR_MAIN: [s.mainGearX, -s.radius - s.gearHeight * 0.6, s.mainGearZ],
    TAIL: [s.finX, s.radius + s.finHeight + 0.2, 0],
  };
  if (s.engineMount === 'nose' || engineCount < 2) {
    anchors.ENGINE_1 = [half + 0.9, 0.55, 0];
  } else {
    anchors.ENGINE_1 = [s.engineX, s.engineY + s.engineRadius + 0.35, -s.engineZ];
    anchors.ENGINE_2 = [s.engineX, s.engineY + s.engineRadius + 0.35, s.engineZ];
  }
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
