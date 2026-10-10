import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

import { buildParts } from './procedural';
import { SHAPES, ZONES, type Shape } from './zones';

function bounds(shape: Shape): THREE.Box3 {
  const box = new THREE.Box3();
  for (const part of buildParts(shape)) {
    const g = part.geometry.clone();
    if (part.rotation) g.applyMatrix4(new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(...part.rotation)));
    if (part.position) g.translate(...part.position);
    g.computeBoundingBox();
    if (g.boundingBox) box.union(g.boundingBox);
  }
  return box;
}

describe('procedural aircraft', () => {
  it.each(Object.keys(SHAPES) as Shape[])('%s covers its zones, sits on the ground and fits its size', (shape) => {
    const spec = SHAPES[shape];
    const parts = buildParts(shape);
    const zones = new Set(parts.map((p) => p.zone));
    for (const zone of ZONES) {
      if (zone === 'ENGINE_2' && spec.engines === 1) expect(zones.has(zone), zone).toBe(false);
      else expect(zones.has(zone), zone).toBe(true);
    }
    for (const part of parts) {
      const pos = part.geometry.getAttribute('position');
      expect(pos.count).toBeGreaterThan(0);
      for (let i = 0; i < pos.array.length; i += 1) expect(Number.isFinite(pos.array[i])).toBe(true);
    }
    const box = bounds(shape);
    expect(box.min.y).toBeCloseTo(spec.groundY, 2); // wheels touch the ground
    expect(box.max.x - box.min.x).toBeLessThanOrEqual(spec.length + 0.35);
    expect(box.max.x - box.min.x).toBeGreaterThan(spec.length - 0.6);
    expect(box.max.z - box.min.z).toBeLessThanOrEqual(spec.span + 0.6);
    expect(box.max.z - box.min.z).toBeGreaterThan(spec.span * 0.9);
  });

  it('shares geometry between aircraft of the same shape', () => {
    expect(buildParts('FIGHTER_TWIN')).toBe(buildParts('FIGHTER_TWIN'));
  });
});
