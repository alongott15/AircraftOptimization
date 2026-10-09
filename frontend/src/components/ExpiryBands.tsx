/**
 * "תוך … ועד …" expiration bands per activity unit (FR-14 / FR-34). Both bounds are in the row's unit
 * (days for calendar days). Used by the mission form (ranking preference) and the fleet-list filter.
 */
import { ActionIcon, Button, Group, NumberInput, Select, Stack, Text } from '@mantine/core';
import { IconPlus, IconTrash } from '@tabler/icons-react';

import { useReference } from '../api/hooks';
import { t } from '../i18n';
import { useNames } from './results';

export interface BandRow {
  unit: string;
  within: number | '';
  until: number | '';
}

export const BAND_UNITS = ['FH', 'LDG', 'TG', 'CYC', 'ENG_START', 'CAL_DAY'] as const;

/** Hebrew validation message, or null when every row is a valid band (ועד required, תוך < ועד). */
export function bandError(rows: BandRow[]): string | null {
  if (rows.some((r) => r.until === '')) return t('val.FIELD_REQUIRED');
  if (rows.some((r) => Number(r.within || 0) >= Number(r.until))) return t('val.WINDOW_RANGE');
  return null;
}

/** URL form used by the fleet list: `FH:0:50,CAL_DAY:0:7`. */
export const encodeBands = (rows: BandRow[]): string =>
  rows.map((r) => `${r.unit}:${Number(r.within || 0)}:${Number(r.until)}`).join(',');

export function decodeBands(raw: string | null): BandRow[] {
  return (raw ?? '').split(',').filter(Boolean).map((part) => {
    const [unit = 'FH', within = '0', until = ''] = part.split(':');
    return { unit, within: Number(within), until: until === '' ? '' : Number(until) };
  });
}

export function ExpiryBandsEditor({ rows, onChange, error, testId }: {
  rows: BandRow[];
  onChange: (rows: BandRow[]) => void;
  error?: string | null;
  testId: string;
}) {
  const ref = useReference().data;
  const names = useNames();
  const unitLabel = (code: string) =>
    ref?.activity_units.find((u) => u.unit_code === code)?.name_he?.toString() ?? code;
  const suffix = (code: string) => (code === 'CAL_DAY' ? t('expiry.days') : names.unit(code));
  const used = new Set(rows.map((r) => r.unit));
  const free = BAND_UNITS.filter((u) => !used.has(u));
  const update = (i: number, patch: Partial<BandRow>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <Stack gap="xs" data-testid={testId}>
      {rows.map((row, i) => (
        <Group key={row.unit} gap="sm" align="flex-end" wrap="wrap" data-testid={`${testId}-row-${row.unit}`}>
          <Select label={t('expiry.unit')} w={200} allowDeselect={false} value={row.unit}
            data={[row.unit, ...free].map((u) => ({ value: u, label: unitLabel(u) }))}
            onChange={(v) => v && update(i, { unit: v })} />
          <NumberInput label={t('expiry.within')} w={150} min={0} decimalScale={1} value={row.within}
            rightSection={<Text size="xs" c="dimmed">{suffix(row.unit)}</Text>} rightSectionWidth={56}
            onChange={(v) => update(i, { within: v === '' ? '' : Number(v) })} data-testid={`${testId}-within-${row.unit}`} />
          <NumberInput label={t('expiry.until')} w={150} min={0} decimalScale={1} value={row.until} required
            rightSection={<Text size="xs" c="dimmed">{suffix(row.unit)}</Text>} rightSectionWidth={56}
            onChange={(v) => update(i, { until: v === '' ? '' : Number(v) })} data-testid={`${testId}-until-${row.unit}`} />
          <ActionIcon variant="subtle" color="gray" size="lg" aria-label={t('expiry.remove')}
            onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            <IconTrash size={18} />
          </ActionIcon>
        </Group>
      ))}
      {error && <Text size="sm" c="red" role="alert">{error}</Text>}
      <Group>
        <Button variant="light" size="xs" leftSection={<IconPlus size={14} />} disabled={!free.length}
          onClick={() => onChange([...rows, { unit: free[0] ?? 'FH', within: 0, until: '' }])} data-testid={`${testId}-add`}>
          {t('expiry.add')}
        </Button>
      </Group>
    </Stack>
  );
}
