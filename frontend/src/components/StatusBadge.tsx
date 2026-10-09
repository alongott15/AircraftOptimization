/** Status is always color + icon + text (DESIGN_SYSTEM §2.2). */
import { Badge, type MantineSize } from '@mantine/core';
import {
  IconAlertOctagon,
  IconCircleCheck,
  IconClockExclamation,
  IconHelpCircle,
  IconInfoCircle,
  type Icon,
} from '@tabler/icons-react';
import type { ReactNode } from 'react';

import { tk } from '../i18n';
import { opStatusTone, riskTone, severityTone, toneVars, type Tone } from '../theme/tokens';

const ICONS: Record<Tone, Icon> = {
  EXPIRED: IconAlertOctagon,
  EXPIRING_SOON: IconClockExclamation,
  OK: IconCircleCheck,
  UNKNOWN: IconHelpCircle,
  INFO: IconInfoCircle,
};

export function ToneBadge({ tone, children, size = 'md', pulse }: {
  tone: Tone;
  children: ReactNode;
  size?: MantineSize;
  pulse?: boolean;
}) {
  const IconCmp = ICONS[tone];
  const colors = toneVars[tone];
  return (
    <Badge
      size={size}
      variant="light"
      radius="xl"
      className={pulse ? 'ao-pulse' : undefined}
      leftSection={<IconCmp size={14} aria-hidden />}
      style={{ background: colors.bg, color: colors.fg, border: `1px solid ${colors.fg}` }}
      data-tone={tone}
    >
      {children}
    </Badge>
  );
}

export function StatusBadge({ status, size, suffix }: { status: string; size?: MantineSize; suffix?: ReactNode }) {
  const tone = (['EXPIRED', 'EXPIRING_SOON', 'OK', 'UNKNOWN'].includes(status) ? status : 'UNKNOWN') as Tone;
  return (
    <ToneBadge tone={tone} size={size}>
      {tk(`status.${status}`)}
      {suffix}
    </ToneBadge>
  );
}

export function OpStatusBadge({ status, size }: { status: string; size?: MantineSize }) {
  return (
    <ToneBadge tone={opStatusTone[status] ?? 'UNKNOWN'} size={size}>
      {tk(`opStatus.${status}`)}
    </ToneBadge>
  );
}

export function SeverityBadge({ severity, size }: { severity: string; size?: MantineSize }) {
  return (
    <ToneBadge tone={severityTone[severity] ?? 'INFO'} size={size}>
      {tk(`severity.${severity}`)}
    </ToneBadge>
  );
}

export function RiskBadge({ band, probability, size }: { band: string; probability?: number | null; size?: MantineSize }) {
  return (
    <ToneBadge tone={riskTone[band] ?? 'UNKNOWN'} size={size}>
      {tk(`risk.${band}`)}
      {typeof probability === 'number' ? ` · ${Math.round(probability * 100)}%` : ''}
    </ToneBadge>
  );
}
