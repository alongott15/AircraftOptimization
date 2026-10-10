/** 3D aircraft viewer (DESIGN_SYSTEM.md §6): procedural model, zone hotspots, tail number, expired highlighting. */
import { ActionIcon, Group, Paper, Stack, Text, Tooltip } from '@mantine/core';
import { useReducedMotion } from '@mantine/hooks';
import { ContactShadows, Html, OrbitControls, Text as Text3D } from '@react-three/drei';
import { Canvas, useThree } from '@react-three/fiber';
import { IconArrowsMaximize, IconEye, IconEyeOff, IconRotate360, IconView360 } from '@tabler/icons-react';
import tailFontUrl from '@fontsource/heebo/files/heebo-latin-700-normal.woff?url';
import { useEffect, useMemo, useState } from 'react';

import type { AircraftDetail } from '../../api/types';
import { useNames } from '../../components/results';
import { formatNumber } from '../../i18n/format';
import { t, tk } from '../../i18n';
import { palette3d, toneVars } from '../../theme/tokens';
import { AircraftModel, groundY, StudioLights } from './AircraftModel';
import { Fallback2D } from './Fallback2D';
import { hasWebGL } from './webgl';
import { computeZones, type ZoneInfo } from './zoneStatus';
import { shapeFor, specFor, zoneAnchors, type Zone, ZONES } from './zones';

type Preset = 'iso' | 'side' | 'top' | 'front';

function cameraFor(preset: Preset, length: number): [number, number, number] {
  switch (preset) {
    case 'side':
      return [0, length * 0.15, length * 1.75];
    case 'top':
      return [0, length * 1.95, 0.01];
    case 'front':
      return [length * 1.75, length * 0.12, 0];
    default:
      return [length * 1.15, length * 0.62, length * 1.3];
  }
}

function CameraRig({ preset, length, nonce }: { preset: Preset; length: number; nonce: number }) {
  const { camera, controls, invalidate } = useThree();
  useEffect(() => {
    const [x, y, z] = cameraFor(preset, length);
    camera.position.set(x, y, z);
    camera.lookAt(0, 0, 0);
    const c = controls as unknown as { target?: { set: (a: number, b: number, c: number) => void }; update?: () => void } | null;
    c?.target?.set(0, 0, 0);
    c?.update?.();
    invalidate();
  }, [preset, length, nonce, camera, controls, invalidate]);
  return null;
}

function hotspotColor(info: ZoneInfo): string {
  const tone = info.tone ?? 'OK';
  return tone === 'EXPIRED' ? palette3d.expired : tone === 'EXPIRING_SOON' ? palette3d.expiring
    : tone === 'INFO' ? palette3d.info : '#8E9AAF';
}

interface ViewerProps {
  detail: AircraftDetail;
  selectedZone: string | null;
  onSelectZone: (zone: string | null) => void;
}

export default function AircraftViewer({ detail, selectedZone, onSelectZone }: ViewerProps) {
  const names = useNames();
  const reducedMotion = useReducedMotion();
  const [webgl] = useState(hasWebGL);
  const [preset, setPreset] = useState<Preset>('iso');
  const [nonce, setNonce] = useState(0);
  const [showHotspots, setShowHotspots] = useState(true);
  const [autoRotate, setAutoRotate] = useState(false);
  const shape = shapeFor(detail.model.model_shape, detail.model.engine_count);
  const spec = specFor(shape);
  const zones = useMemo(() => computeZones(detail, names.inspection), [detail, names]);
  const anchors = useMemo(() => zoneAnchors(shape, detail.model.engine_count), [shape, detail.model.engine_count]);
  const anyExpired = detail.compliance_status === 'EXPIRED' || detail.derived_status === 'GROUNDED';
  const pulse = anyExpired && !reducedMotion;
  const expiredPermits = detail.permits.filter((p) => p.status === 'EXPIRED').length;
  const nextIns = detail.inspections.find((i) => i.status !== 'OK');
  const summary = t('viewer.ariaSummary', {
    tail: detail.tail_number, type: detail.type_name_he, mas: detail.open_ma_count, expired: detail.expired_count,
  });

  if (!webgl) {
    return <Fallback2D detail={detail} zones={zones} selectedZone={selectedZone} onSelectZone={onSelectZone} />;
  }

  const select = (z: Zone) => onSelectZone(selectedZone === z ? null : z);
  const tools: { key: Preset; label: string }[] = [
    { key: 'side', label: t('viewer.side') }, { key: 'top', label: t('viewer.top') }, { key: 'front', label: t('viewer.front') },
  ];

  return (
    <div className="ao-viewer" data-testid="aircraft-viewer" data-selected-zone={selectedZone ?? ''}
      data-zone-highlight={selectedZone ?? ''} data-expired={anyExpired ? 'true' : 'false'} role="img" aria-label={summary}
      style={{ height: 640 }}>
      <Paper withBorder p={4} radius="md" style={{ position: 'absolute', insetBlockStart: 12, insetInlineEnd: 12, zIndex: 2 }}>
        <Group gap={4}>
          {tools.map((tool) => (
            <Tooltip key={tool.key} label={tool.label}>
              <ActionIcon variant={preset === tool.key ? 'filled' : 'subtle'} aria-label={tool.label}
                onClick={() => { setPreset(tool.key); setNonce((n) => n + 1); }}>
                <IconView360 size={16} />
              </ActionIcon>
            </Tooltip>
          ))}
          <Tooltip label={t('viewer.reset')}>
            <ActionIcon variant="subtle" aria-label={t('viewer.reset')} onClick={() => { setPreset('iso'); setNonce((n) => n + 1); onSelectZone(null); }}>
              <IconArrowsMaximize size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label={t('viewer.hotspots')}>
            <ActionIcon variant="subtle" aria-label={t('viewer.hotspots')} aria-pressed={showHotspots}
              onClick={() => setShowHotspots((v) => !v)}>
              {showHotspots ? <IconEye size={16} /> : <IconEyeOff size={16} />}
            </ActionIcon>
          </Tooltip>
          <Tooltip label={t('viewer.autoRotate')}>
            <ActionIcon variant={autoRotate ? 'filled' : 'subtle'} aria-label={t('viewer.autoRotate')} aria-pressed={autoRotate}
              onClick={() => setAutoRotate((v) => !v)}>
              <IconRotate360 size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Paper>
      <div className="ao-info-card" data-testid="info-card"
        style={{ position: 'absolute', insetBlockStart: 12, insetInlineStart: 12, zIndex: 2 }}>
        <div style={{ fontWeight: 700, fontSize: 15 }}><bdi dir="ltr">{detail.tail_number}</bdi> · {detail.type_name_he}</div>
        {anyExpired && (
          <div className={pulse ? 'ao-pulse' : undefined}
            style={{ marginBlock: 4, padding: '2px 8px', borderRadius: 999, background: palette3d.expired, color: '#fff', display: 'inline-block', fontWeight: 700 }}>
            {detail.derived_status === 'GROUNDED' ? tk('opStatus.GROUNDED') : t('status.EXPIRED')}
          </div>
        )}
        <div>{t('aircraft.openMas', { count: detail.open_ma_count })}</div>
        {expiredPermits > 0 && <div style={{ color: palette3d.expired }}>{t('aircraft.expiredPermits', { count: expiredPermits })}</div>}
        {nextIns && (
          <div>
            {t('aircraft.nextInspection', { name: names.inspection(nextIns.inspection_code) })}
            {nextIns.limits[0]?.remaining !== undefined && nextIns.limits[0]?.remaining !== null && !nextIns.limits[0]?.due_at
              ? ` — ${t('common.remaining', { value: formatNumber(nextIns.limits[0].remaining, 1), unit: names.unit(nextIns.limits[0].unit) })}`
              : ''}
          </div>
        )}
      </div>
      {selectedZone && (
        <Paper withBorder shadow="sm" p="sm" radius="md" data-testid="zone-panel"
          style={{ position: 'absolute', insetBlockEnd: 12, insetInlineStart: 12, zIndex: 2, maxWidth: 340 }}>
          <Stack gap={4}>
            <Text fw={700} size="sm">{t('common.selectedZone', { zone: tk(`zone.${selectedZone}`) })}</Text>
            {(zones[selectedZone as Zone]?.items ?? []).length === 0 ? (
              <Text size="xs" c="dimmed">{t('common.zoneEmpty')}</Text>
            ) : (
              zones[selectedZone as Zone].items.map((it) => (
                <Text key={`${it.kind}-${it.ref}`} size="xs" style={{ borderInlineStart: `3px solid ${toneVars[it.tone].fg}`, paddingInlineStart: 6 }}>
                  {it.kind === 'MA' ? <bdi dir="ltr">{it.ref}</bdi> : null} {it.label}
                  {it.detail ? ` · ${tk(`severity.${it.detail}`)}` : ''}
                </Text>
              ))
            )}
          </Stack>
        </Paper>
      )}
      <Canvas shadows dpr={[1, 2]} frameloop={pulse || autoRotate ? 'always' : 'demand'}
        camera={{ position: cameraFor('iso', spec.length), fov: 40, near: 0.1, far: 200 }}
        onPointerMissed={() => onSelectZone(null)}>
        <StudioLights />
        <AircraftModel shape={shape} zones={zones} selectedZone={selectedZone} onSelect={select}
          outlineExpired={anyExpired} pulse={pulse} />
        {spec.tailMarks.map((m) => (
          <Text3D key={m.side} font={tailFontUrl} fontSize={m.size} color={spec.camo ? '#1D1D1B' : '#ffffff'}
            anchorX="center" anchorY="middle" position={m.position} rotation={[0, m.side === 1 ? 0 : Math.PI, 0]}
            outlineWidth={spec.camo ? 0 : 0.01} outlineColor={palette3d.accent}>
            {detail.tail_number}
          </Text3D>
        ))}
        <Text3D font={tailFontUrl} fontSize={spec.registration.size} color={spec.camo ? '#2B2B2B' : palette3d.accent}
          anchorX="center" anchorY="middle" position={spec.registration.position}>
          {detail.tail_number}
        </Text3D>
        {showHotspots && ZONES.map((zone) => {
          const pos = anchors[zone];
          const info = zones[zone];
          if (!pos || !info.items.length) return null;
          return (
            <Html key={zone} position={pos} center zIndexRange={[10, 0]}>
              <button type="button" className={`ao-hotspot${info.tone === 'EXPIRED' && pulse ? ' ao-pulse' : ''}`}
                style={{ background: hotspotColor(info), outline: selectedZone === zone ? '3px solid #5B8DEF' : undefined }}
                data-zone={zone} data-tone={info.tone ?? 'OK'} aria-label={`${tk(`zone.${zone}`)}: ${info.items.length}`}
                onClick={(e) => { e.stopPropagation(); select(zone); }}>
                {info.items.length}
              </button>
            </Html>
          );
        })}
        <ContactShadows position={[0, groundY(shape) + 0.002, 0]} opacity={0.55} scale={spec.span * 1.7} blur={2.2} far={6} />
        <OrbitControls makeDefault enableDamping autoRotate={autoRotate} autoRotateSpeed={0.8}
          minDistance={spec.length * 0.6} maxDistance={spec.length * 3} maxPolarAngle={Math.PI / 2 - 0.05} />
        <CameraRig preset={preset} length={spec.length} nonce={nonce} />
      </Canvas>
    </div>
  );
}
