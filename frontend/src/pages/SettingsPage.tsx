import { Alert, Button, Card, Grid, Group, NumberInput, ScrollArea, SegmentedControl, Select, Stack, Table, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconLock } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { useHangar, useModels, usePolicy, useReference, useRetrain, useSaveHangar, useUpdatePolicy } from '../api/hooks';
import type { HangarCapacity, Policy } from '../api/types';
import { useAuth } from '../auth/AuthProvider';
import { ErrorState, LoadingState, Ltr, errorMessage } from '../components/common';
import { formatDateTime } from '../i18n/format';
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
      Object.entries(draft).filter(([k, v]) => policy.data[k as keyof Policy] !== v && k !== 'tolerance_policy'),
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

export default function SettingsPage() {
  const { can } = useAuth();
  const editable = can('ADMIN');
  return (
    <Stack mt="md">
      <Title order={2}>{t('settings.title')}</Title>
      {!editable && <Alert icon={<IconLock />} color="gray">{t('settings.readOnly')}</Alert>}
      <PolicyCard editable={editable} />
      <HangarCard editable={editable} />
      <ModelsCard editable={editable} />
    </Stack>
  );
}
