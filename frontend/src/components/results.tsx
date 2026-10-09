/** Explainability components: reasons, score breakdown, limit progress, item descriptions. */
import { Group, List, Progress, Stack, Text, ThemeIcon } from '@mantine/core';
import { IconAlertTriangle, IconBulb, IconX } from '@tabler/icons-react';
import { useMemo } from 'react';

import { useReference } from '../api/hooks';
import type { Reason } from '../api/types';
import { formatNumber, formatRelative } from '../i18n/format';
import { t, tk } from '../i18n';
import { makeNames, renderReason, type Names } from '../i18n/reasonCodes';
import { STATUS_RANK, toneVars, type ComplianceStatus } from '../theme/tokens';
import { useServerNow } from './common';

export function useNames(): Names {
  const ref = useReference();
  return useMemo(() => makeNames(ref.data), [ref.data]);
}

export function ReasonList({ reasons, kind = 'reason' }: { reasons: Reason[]; kind?: 'reason' | 'warning' | 'hint' }) {
  const names = useNames();
  if (!reasons.length) return null;
  const icon = kind === 'reason' ? <IconX size={14} /> : kind === 'warning' ? <IconAlertTriangle size={14} /> : <IconBulb size={14} />;
  const tone = kind === 'reason' ? toneVars.EXPIRED : kind === 'warning' ? toneVars.EXPIRING_SOON : toneVars.INFO;
  return (
    <List
      spacing={4}
      size="sm"
      icon={
        <ThemeIcon size={20} radius="xl" style={{ background: tone.bg, color: tone.fg }}>
          {icon}
        </ThemeIcon>
      }
      data-testid={`${kind}-list`}
    >
      {reasons.map((r, i) => (
        <List.Item key={`${r.code}-${i}`}>
          <Text size="sm" fw={kind === 'reason' && i === 0 ? 700 : 400}>
            {renderReason(r, names)}
          </Text>
        </List.Item>
      ))}
    </List>
  );
}

export function ScoreBreakdown({ breakdown }: {
  breakdown: Record<string, { value: number; weight: number; contribution: number }>;
}) {
  // expiry_window is active only when the mission defines expiration windows (weight 0 otherwise)
  const terms = ['risk', 'best_fit', 'balance', 'capacity', 'base_match', 'warnings', 'expiry_window']
    .filter((term) => breakdown[term] && (term !== 'expiry_window' || breakdown[term].weight > 0));
  const totalWeight = terms.reduce((acc, term) => acc + (breakdown[term]?.weight ?? 0), 0) || 1;
  return (
    <Stack gap={6} data-testid="score-breakdown">
      {terms.map((term) => {
        const b = breakdown[term];
        if (!b) return null;
        return (
          <div key={term}>
            <Group justify="space-between" gap="xs">
              <Text size="xs">{tk(`score.${term}`)}</Text>
              <Text size="xs" c="dimmed" className="ao-num">
                {formatNumber(b.contribution, 1)} / {formatNumber((b.weight / totalWeight) * 100, 0)}
              </Text>
            </Group>
            <Progress value={b.value * 100} size="sm" aria-label={tk(`score.${term}`)} />
          </div>
        );
      })}
    </Stack>
  );
}

/** Used-vs-interval bar; fills from the inline-start edge (right in RTL). */
export function LimitProgress({ remaining, interval, status, unit }: {
  remaining: number | null | undefined;
  interval: number | null | undefined;
  status: string;
  unit: string;
}) {
  const names = useNames();
  const tone = toneVars[(status as ComplianceStatus) in STATUS_RANK ? (status as ComplianceStatus) : 'UNKNOWN'];
  if (remaining === null || remaining === undefined || !interval) {
    return <Text size="xs" c="dimmed">{t('common.dash')}</Text>;
  }
  const used = Math.min(Math.max(((interval - remaining) / interval) * 100, 0), 100);
  const label = remaining < 0
    ? t('common.overBy', { value: formatNumber(-remaining, 1), unit: names.unit(unit) })
    : t('common.remaining', { value: formatNumber(remaining, 1), unit: names.unit(unit) });
  return (
    <Stack gap={2} miw={120}>
      <Progress value={used} color={tone.fg} size="sm" aria-label={label} />
      <Text size="xs" className="ao-num" style={{ color: tone.fg }}>
        {label}
      </Text>
    </Stack>
  );
}

/** Human description of an item's remaining time/units, relative to server now. */
export function useRemainingText() {
  const names = useNames();
  const now = useServerNow();
  return (item: { remaining?: number | null; due_at?: string | null; unit?: string | null; status: string }) => {
    if (item.status === 'UNKNOWN') return t('status.UNKNOWN');
    if (item.due_at) return formatRelative(item.due_at, now);
    if (item.remaining === null || item.remaining === undefined) return t('common.dash');
    const unit = names.unit(item.unit ?? '');
    return item.remaining < 0
      ? t('common.overBy', { value: formatNumber(-item.remaining, 1), unit })
      : t('common.remaining', { value: formatNumber(item.remaining, 1), unit });
  };
}
