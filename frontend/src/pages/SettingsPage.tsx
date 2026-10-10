import { Alert, Button, Card, Grid, Group, NumberInput, ScrollArea, SegmentedControl, Select, Stack, Table, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconLock } from '@tabler/icons-react';
import { lazy, Suspense, useEffect, useState } from 'react';

import {
  useHangar, useModels, usePolicy, useReference, useRetrain, useSaveHangar, useSaveTgWeights, useSaveWeights,
  useTgWeights, useUpdatePolicy, useWeights,
} from '../api/hooks';
import type { HangarCapacity, Policy, TgWeight, Weights } from '../api/types';
import { useAuth } from '../auth/AuthProvider';
import { ErrorState, LoadingState, Ltr, errorMessage } from '../components/common';
import { formatDateTime } from '../i18n/format';
import { makeNames } from '../i18n/reasonCodes';
import { hasWebGL } from '../features/aircraft3d/webgl';
import { t, tk } from '../i18n';

const NUMERIC_FIELDS: (keyof Policy)[] = [
  'duration_buffer_pct', 'min_duration_buffer_hours', 'calendar_buffer_hours', 'landing_buffer', 'turnaround_hours',
  'expiring_soon_days', 'expiring_soon_pct', 'expiring_soon_fh', 'expiring_soon_ldg', 'low_margin_pct',
  'max_staleness_hours', 'risk_high_threshold',
];

function PolicyCard({ editable }: { editable: boolean }) {
  const policy = usePolicy();
  const save = useUpdatePolicy();
  const [draft, setDraft] = useState<Policy | null>(null);
  useEffect(() => {
    if (policy.data) setDraft(policy.data);
  }, [policy.data]);
  if (policy.isPending || !draft) return <LoadingState />;
  if (policy.isError) return <ErrorState error={policy.error} />;
  const submit = () => {
    const changes = Object.fromEntries(
      Object.entries(draft).filter(([k, v]) => policy.data[k as keyof Policy] !== v),
    );
    save.mutate(changes, {
      onSuccess: () => notifications.show({ color: 'green', message: t('settings.policySaved') }),
      onError: (err) => notifications.show({ color: 'red', message: errorMessage(err) }),
    });
  };
  return (
    <Card>
      <Title order={4} mb="sm">{t('settings.policy')}</Title>
      <Grid>
        {NUMERIC_FIELDS.map((key) => (
          <Grid.Col key={key} span={{ base: 12, sm: 6, md: 4 }}>
            <NumberInput label={tk(`settings.p_${key}`)} value={draft[key] as number} disabled={!editable}
              decimalScale={2} onChange={(v) => setDraft({ ...draft, [key]: Number(v) })} />
          </Grid.Col>
        ))}
        <Grid.Col span={{ base: 12, sm: 6, md: 4 }}>
          <Text size="sm" fw={500} mb={4}>{t('settings.idlePolicy')}</Text>
          <SegmentedControl disabled={!editable} value={draft.idle_policy}
            onChange={(v) => setDraft({ ...draft, idle_policy: v as Policy['idle_policy'] })}
            data={[{ value: 'EXCLUDE', label: t('settings.idleExclude') }, { value: 'WARN', label: t('settings.idleWarn') }]} />
        </Grid.Col>
        <Grid.Col span={{ base: 12, sm: 6, md: 4 }}>
          <Text size="sm" fw={500} mb={4}>{t('settings.tolerancePolicy')}</Text>
          <SegmentedControl disabled={!editable} value={draft.tolerance_policy} data-testid="tolerance-policy"
            onChange={(v) => setDraft({ ...draft, tolerance_policy: v as Policy['tolerance_policy'] })}
            data={[{ value: 'WARN', label: t('settings.toleranceWarn') }, { value: 'NEVER', label: t('settings.toleranceNever') }]} />
          <Text size="xs" c="dimmed" mt={4}>{t('settings.toleranceHint')}</Text>
        </Grid.Col>
        <Grid.Col span={{ base: 12, sm: 6, md: 4 }}>
          <Select label={t('field.weightsPreset')} disabled={!editable} value={draft.weights_preset} allowDeselect={false}
            data={['BALANCED', 'MAX_SAFETY', 'MAX_AVAILABILITY'].map((v) => ({ value: v, label: tk(`preset.${v}`) }))}
            onChange={(v) => v && setDraft({ ...draft, weights_preset: v as Policy['weights_preset'] })} />
        </Grid.Col>
      </Grid>
      {editable && (
        <Group mt="md">
          <Button onClick={submit} loading={save.isPending}>{t('action.save')}</Button>
        </Group>
      )}
    </Card>
  );
}

const TERMS = ['risk', 'best_fit', 'balance', 'capacity', 'base_match', 'warnings', 'expiry_window'] as const;
const PRESETS = ['BALANCED', 'MAX_SAFETY', 'MAX_AVAILABILITY'] as const;

/** FR-45 — scoring weights per preset (ranking of eligible aircraft only). */
function WeightsCard({ editable }: { editable: boolean }) {
  const weights = useWeights();
  const save = useSaveWeights();
  const [draft, setDraft] = useState<Weights['presets'] | null>(null);
  useEffect(() => {
    if (weights.data) setDraft(weights.data.presets);
  }, [weights.data]);
  if (weights.isPending || !draft) return <LoadingState />;
  if (weights.isError) return <ErrorState error={weights.error} />;
  const value = (preset: string, term: string) => draft[preset]?.[term] ?? 0;
  const setValue = (preset: string, term: string, v: number) =>
    setDraft({ ...draft, [preset]: { ...draft[preset], [term]: v } });
  const sum = (preset: string) => TERMS.reduce((acc, term) => acc + value(preset, term), 0);
  return (
    <Card data-testid="weights-card">
      <Title order={4} mb={4}>{t('settings.weights')}</Title>
      <Text size="sm" c="dimmed" mb="sm">{t('settings.weightsHint')}</Text>
      <ScrollArea>
        <Table withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('field.item')}</Table.Th>
              {PRESETS.map((p) => <Table.Th key={p}>{tk(`preset.${p}`)}</Table.Th>)}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {TERMS.map((term) => (
              <Table.Tr key={term}>
                <Table.Td>{tk(`score.${term}`)}</Table.Td>
                {PRESETS.map((p) => (
                  <Table.Td key={p}>
                    <NumberInput size="xs" w={90} min={0} max={1} step={0.05} decimalScale={4} value={value(p, term)}
                      disabled={!editable} aria-label={`${tk(`preset.${p}`)} ${tk(`score.${term}`)}`}
                      onChange={(v) => setValue(p, term, Number(v) || 0)} />
                  </Table.Td>
                ))}
              </Table.Tr>
            ))}
            <Table.Tr>
              <Table.Td fw={600}>{t('settings.weightsSum')}</Table.Td>
              {PRESETS.map((p) => <Table.Td key={p} className="ao-num" fw={600}>{sum(p).toFixed(2)}</Table.Td>)}
            </Table.Tr>
          </Table.Tbody>
        </Table>
      </ScrollArea>
      {editable && (
        <Group mt="md">
          <Button loading={save.isPending} onClick={() => save.mutate(draft, {
            onSuccess: () => notifications.show({ color: 'green', message: t('settings.weightsSaved') }),
            onError: (err) => notifications.show({ color: 'red', message: errorMessage(err) }),
          })}>{t('action.save')}</Button>
        </Group>
      )}
    </Card>
  );
}

const tgKey = (r: Pick<TgWeight, 'type_code' | 'inspection_type_code' | 'unit'>) =>
  `${r.type_code}|${r.inspection_type_code}|${r.unit}`;

/** FR-60 — touch-and-go weight per landing/cycle limit (reference from the lake, admin override in the app store). */
function TgWeightsCard({ editable }: { editable: boolean }) {
  const rows = useTgWeights();
  const save = useSaveTgWeights();
  const names = makeNames(useReference().data);
  const [draft, setDraft] = useState<Record<string, number | null>>({});
  useEffect(() => {
    if (rows.data) setDraft(Object.fromEntries(rows.data.map((r) => [tgKey(r), r.override_tg_weight ?? null])));
  }, [rows.data]);
  if (rows.isPending) return <LoadingState />;
  if (rows.isError) return <ErrorState error={rows.error} />;
  const changed = rows.data.filter((r) => (draft[tgKey(r)] ?? null) !== (r.override_tg_weight ?? null));
  return (
    <Card data-testid="tg-weights-card">
      <Title order={4} mb={4}>{t('settings.tgWeights')}</Title>
      <Text size="sm" c="dimmed" mb="sm">{t('settings.tgHint')}</Text>
      <ScrollArea>
        <Table withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('field.type')}</Table.Th>
              <Table.Th>{t('field.item')}</Table.Th>
              <Table.Th>{t('field.unit')}</Table.Th>
              <Table.Th>{t('settings.tgReference')}</Table.Th>
              <Table.Th>{t('settings.tgOverride')}</Table.Th>
              <Table.Th>{t('settings.tgEffective')}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.data.map((r) => {
              const key = tgKey(r);
              const override = draft[key] ?? null;
              return (
                <Table.Tr key={key}>
                  <Table.Td><Ltr>{r.type_code}</Ltr></Table.Td>
                  <Table.Td>{names.inspection(r.inspection_type_code)}</Table.Td>
                  <Table.Td>{names.unit(r.unit)}</Table.Td>
                  <Table.Td className="ao-num">{r.reference_tg_weight}</Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="nowrap">
                      <NumberInput size="xs" w={80} min={r.reference_tg_weight} max={5} step={0.1} decimalScale={2}
                        disabled={!editable}
                        value={override ?? ''} placeholder={String(r.reference_tg_weight)}
                        aria-label={`${r.type_code} ${r.inspection_type_code} ${r.unit}`}
                        onChange={(v) => setDraft({ ...draft, [key]: v === '' ? null : Number(v) })} />
                      {editable && override !== null && (
                        <Button size="compact-xs" variant="subtle" onClick={() => setDraft({ ...draft, [key]: null })}>
                          {t('settings.tgClear')}
                        </Button>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td className="ao-num" fw={600}>{Math.max(override ?? 0, r.reference_tg_weight)}</Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </ScrollArea>
      {editable && (
        <Group mt="md">
          <Button disabled={!changed.length} loading={save.isPending} onClick={() => save.mutate(
            changed.map((r) => ({ type_code: r.type_code, inspection_type_code: r.inspection_type_code,
              unit: r.unit as 'LDG' | 'CYC', tg_weight: draft[tgKey(r)] ?? null })),
            {
              onSuccess: () => notifications.show({ color: 'green', message: t('settings.tgSaved') }),
              onError: (err) => notifications.show({ color: 'red', message: errorMessage(err) }),
            },
          )}>{t('action.save')}</Button>
        </Group>
      )}
    </Card>
  );
}

function HangarCard({ editable }: { editable: boolean }) {
  const hangar = useHangar();
  const save = useSaveHangar();
  const ref = useReference().data;
  const [rows, setRows] = useState<HangarCapacity[]>([]);
  useEffect(() => {
    if (hangar.data) setRows(hangar.data);
  }, [hangar.data]);
  if (hangar.isPending) return <LoadingState />;
  if (hangar.isError) return <ErrorState error={hangar.error} />;
  const weeks = [...new Set(rows.map((r) => r.iso_week))].sort();
  const bases = [...new Set(rows.map((r) => r.base_code))].sort();
  const get = (b: string, w: string) => rows.find((r) => r.base_code === b && r.iso_week === w)?.slots ?? 0;
  const setSlot = (b: string, w: string, slots: number) =>
    setRows(rows.map((r) => (r.base_code === b && r.iso_week === w ? { ...r, slots } : r)));
  return (
    <Card>
      <Title order={4} mb="sm">{t('settings.hangar')}</Title>
      <ScrollArea>
        <Table withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('field.base')}</Table.Th>
              {weeks.map((w) => <Table.Th key={w}><Ltr>{w}</Ltr></Table.Th>)}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {bases.map((b) => (
              <Table.Tr key={b}>
                <Table.Td fw={600}>{ref?.bases.find((x) => x.base_code === b)?.name_he?.toString() ?? b}</Table.Td>
                {weeks.map((w) => (
                  <Table.Td key={w}>
                    <NumberInput size="xs" w={64} min={0} max={50} value={get(b, w)} disabled={!editable} allowDecimal={false}
                      onChange={(v) => setSlot(b, w, Number(v) || 0)} aria-label={`${b} ${w}`} />
                  </Table.Td>
                ))}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </ScrollArea>
      {editable && (
        <Group mt="md">
          <Button loading={save.isPending} onClick={() => save.mutate(rows, {
            onSuccess: () => notifications.show({ color: 'green', message: t('settings.policySaved') }),
            onError: (err) => notifications.show({ color: 'red', message: errorMessage(err) }),
          })}>{t('action.save')}</Button>
        </Group>
      )}
    </Card>
  );
}

function ModelsCard({ editable }: { editable: boolean }) {
  const models = useModels();
  const retrain = useRetrain();
  if (models.isPending) return <LoadingState />;
  if (models.isError) return <ErrorState error={models.error} />;
  return (
    <Card>
      <Title order={4} mb="sm">{t('settings.models')}</Title>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t('field.model')}</Table.Th>
            <Table.Th>{t('field.version')}</Table.Th>
            <Table.Th>{t('field.status')}</Table.Th>
            <Table.Th>{t('field.trainedAt')}</Table.Th>
            <Table.Th>{t('field.metrics')}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {models.data.map((m) => (
            <Table.Tr key={`${m.model_name}-${m.version}`}>
              <Table.Td><Ltr>{m.model_name}</Ltr></Table.Td>
              <Table.Td><Ltr>{m.version}</Ltr></Table.Td>
              <Table.Td><Ltr>{m.status}</Ltr></Table.Td>
              <Table.Td className="ao-num">{formatDateTime(m.trained_at)}</Table.Td>
              <Table.Td><Ltr>{Object.entries(m.metrics).filter(([k]) => !k.startsWith('n_')).map(([k, v]) => `${k}=${String(v)}`).join(' · ')}</Ltr></Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {editable && (
        <Group mt="md">
          {['defect_risk', 'duration_buffer'].map((name) => (
            <Button key={name} variant="light" loading={retrain.isPending && retrain.variables === name}
              onClick={() => retrain.mutate(name, {
                onSuccess: () => notifications.show({ color: 'blue', message: t('settings.retrainStarted') }),
                onError: (err) => notifications.show({ color: 'red', message: errorMessage(err) }),
              })}>
              {t('action.retrain')}: <Ltr>{name}</Ltr>
            </Button>
          ))}
        </Group>
      )}
    </Card>
  );
}

const ModelPreview = lazy(() => import('../features/aircraft3d/ModelPreview'));

function ModelsPreviewCard() {
  return (
    <Card data-testid="models-3d-card">
      <Title order={4} mb={4}>{t('settings.models3d')}</Title>
      <Text size="sm" c="dimmed" mb="sm">{t('settings.models3dHint')}</Text>
      {hasWebGL() ? (
        <Suspense fallback={<LoadingState />}><ModelPreview /></Suspense>
      ) : (
        <Text size="sm">{t('viewer.fallback')}</Text>
      )}
    </Card>
  );
}

export default function SettingsPage() {
  const { can } = useAuth();
  const editable = can('ADMIN');
  return (
    <Stack mt="md">
      <Title order={2}>{t('settings.title')}</Title>
      {!editable && <Alert icon={<IconLock />} color="gray">{t('settings.readOnly')}</Alert>}
      <PolicyCard editable={editable} />
      <WeightsCard editable={editable} />
      <TgWeightsCard editable={editable} />
      <HangarCard editable={editable} />
      <ModelsCard editable={editable} />
      <ModelsPreviewCard />
    </Stack>
  );
}
