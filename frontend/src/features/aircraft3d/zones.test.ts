import { describe, expect, it } from 'vitest';

import type { AircraftDetail } from '../../api/types';
import { computeZones, worstTone } from './zoneStatus';
import { SHAPES, shapeFor, ZONES, zoneAnchors, zoneForAta, type Shape } from './zones';

describe('zone anchors', () => {
  it.each(Object.keys(SHAPES) as Shape[])('%s has an anchor for every zone it can show', (shape) => {
    const engines = SHAPES[shape].engines;
    const anchors = zoneAnchors(shape, engines);
    for (const zone of ZONES) {
      if (zone === 'ENGINE_2' && engines === 1) {
        expect(anchors[zone]).toBeUndefined();
      } else {
        expect(anchors[zone], zone).toHaveLength(3);
      }
    }
  });

  it('draws the engine count from data, not from the shape name', () => {
    expect(shapeFor('FIGHTER_TWIN', 2)).toBe('FIGHTER_TWIN');
    expect(shapeFor('FIGHTER_TWIN', 1)).toBe('FIGHTER_SINGLE');
    expect(shapeFor('TWIN_TURBOPROP_HIGH_WING', 1)).toBe('SINGLE_PROP_LOW_WING');
    expect(shapeFor('TWIN_JET_LOW_WING', 1)).toBe('FIGHTER_SINGLE');
    expect(shapeFor('NOT_A_SHAPE', 2)).toBe('TWIN_TURBOPROP_HIGH_WING');
    expect(shapeFor(null, 1)).toBe('SINGLE_PROP_LOW_WING');
  });

  it('maps ATA chapters to zones', () => {
    expect(zoneForAta(32)).toBe('GEAR_MAIN');
    expect(zoneForAta(34)).toBe('NOSE');
    expect(zoneForAta(79)).toBe('ENGINE_1');
    expect(zoneForAta(55)).toBe('TAIL');
    expect(zoneForAta(null)).toBeNull();
  });
});

describe('zone status', () => {
  it('keeps the worst tone', () => {
    expect(worstTone('INFO', 'EXPIRED')).toBe('EXPIRED');
    expect(worstTone('EXPIRED', 'EXPIRING_SOON')).toBe('EXPIRED');
    expect(worstTone(null, 'INFO')).toBe('INFO');
  });

  it('colors zones from MAs and zone-bound inspections', () => {
    const detail = {
      engine_count: 2,
      maintenance_actions: [
        { ma_number: 'MA-1', title: 'leak', severity: 'GROUNDING', zone_code: 'ENGINE_2', ata_chapter: 79, deferral: null },
        { ma_number: 'MA-2', title: 'radar', severity: 'DEFERRED', zone_code: null, ata_chapter: 34, deferral: null },
      ],
      inspections: [
        { inspection_code: 'LG_INSP', zone_code: 'GEAR_MAIN', status: 'EXPIRING_SOON', limits: [] },
        { inspection_code: 'ENG_BORESCOPE', zone_code: 'ENGINE_1', status: 'EXPIRED', limits: [] },
      ],
    } as unknown as AircraftDetail;
    const zones = computeZones(detail, (c) => c);
    expect(zones.ENGINE_2.tone).toBe('EXPIRED');
    expect(zones.NOSE.tone).toBe('EXPIRING_SOON');
    expect(zones.GEAR_MAIN.tone).toBe('EXPIRING_SOON');
    expect(zones.ENGINE_1.tone).toBe('EXPIRED');
    expect(zones.TAIL.tone).toBeNull();
  });
});
