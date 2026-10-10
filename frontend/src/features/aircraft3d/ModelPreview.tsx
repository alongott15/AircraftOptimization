/** Settings: preview of every 3D aircraft model, to check how each aircraft type's model shape looks. */
import { Select, Stack } from '@mantine/core';
import { ContactShadows, OrbitControls } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';
import { useState } from 'react';

import { t, tk } from '../../i18n';
import { AircraftModel, StudioLights } from './AircraftModel';
import type { ZoneInfo } from './zoneStatus';
import { SHAPES, specFor, ZONES, type Shape, type Zone } from './zones';

const EMPTY = Object.fromEntries(ZONES.map((z): [Zone, ZoneInfo] => [z, { tone: null, items: [] }])) as Record<Zone, ZoneInfo>;

export default function ModelPreview() {
  const [shape, setShape] = useState<Shape>('FIGHTER_SINGLE');
  const spec = specFor(shape);
  return (
    <Stack gap="sm">
      <Select label={t('settings.models3dShape')} w={280} allowDeselect={false} value={shape} data-testid="model-preview-shape"
        data={(Object.keys(SHAPES) as Shape[]).map((s) => ({ value: s, label: tk(`shape.${s}`) }))}
        onChange={(v) => v && setShape(v as Shape)} />
      <div key={shape} className="ao-viewer" style={{ height: 400 }} data-testid="model-preview" data-shape={shape}>
        <Canvas shadows dpr={[1, 2]} camera={{ position: [spec.length * 0.85, spec.length * 0.42, spec.length * 0.95], fov: 40 }}>
          <StudioLights />
          <AircraftModel shape={shape} zones={EMPTY} selectedZone={null} onSelect={() => undefined}
            outlineExpired={false} pulse={false} />
          <ContactShadows position={[0, spec.groundY + 0.002, 0]} opacity={0.55} scale={spec.span * 1.7} blur={2.2} far={6} />
          <OrbitControls makeDefault enableDamping autoRotate autoRotateSpeed={0.6} />
        </Canvas>
      </div>
    </Stack>
  );
}
