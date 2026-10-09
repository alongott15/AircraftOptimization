/** Small shared building blocks: bidi isolation, states, disclaimer, KPI card, freshness, banner. */
import { Alert, Button, Center, Group, Loader, Paper, Stack, Text, ThemeIcon, Tooltip } from '@mantine/core';
import { IconAlertOctagon, IconInfoCircle, IconRefresh } from '@tabler/icons-react';
import { createContext, useContext, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { ApiError } from '../api/client';
import { useFreshness } from '../api/hooks';
import { formatRelative } from '../i18n/format';
import { t, tk } from '../i18n';
import { toneVars, type Tone } from '../theme/tokens';

/** <bdi dir="ltr"> for tail numbers, MA numbers, codes, numbers with units (DESIGN_SYSTEM §1). */
export function Ltr({ children }: { children: ReactNode }) {
  return <bdi dir="ltr">{children}</bdi>;
}

export function LoadingState() {
  return (
    <Center py="xl" aria-busy="true">
      <Group gap="sm">
        <Loader size="sm" />
        <Text c="dimmed">{t('common.loading')}</Text>
      </Group>
    </Center>
  );
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return tk(`err.${error.code}`, { request_id: error.body.request_id ?? '', ...(error.body.params ?? {}) },
      t('err.INTERNAL_ERROR', { request_id: error.body.request_id ?? '' }));
  }
  return t('err.INTERNAL_ERROR', { request_id: '' });
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <Alert color="red" icon={<IconAlertOctagon />} role="alert" title={errorMessage(error)}>
      {onRetry && (
        <Button size="xs" variant="light" color="red" leftSection={<IconRefresh size={14} />} onClick={onRetry}>
          {t('action.retry')}
        </Button>
      )}
    </Alert>
  );
}

export function EmptyState({ text }: { text?: string }) {
  return (
    <Center py="lg">
      <Text c="dimmed">{text ?? t('common.noData')}</Text>
    </Center>
  );
}

export function Disclaimer() {
  return (
    <Alert variant="light" color="blue" icon={<IconInfoCircle />} data-testid="disclaimer">
      {t('disclaimer')}
    </Alert>
  );
}

export function KpiCard(props: { label: string; value: ReactNode; tone?: Tone; to?: string; testId?: string }) {
  const tone = props.tone ? toneVars[props.tone] : undefined;
  const body = (
    <Paper
      withBorder
      p="md"
      data-testid={props.testId}
      style={{
        borderInlineStart: tone ? `4px solid ${tone.fg}` : undefined,
        background: tone && props.tone === 'EXPIRED' ? tone.bg : undefined,
        height: '100%',
      }}
    >
      <Text size="sm" c="dimmed">
        {props.label}
      </Text>
      <Text fz={28} fw={700} className="ao-num" style={{ color: tone?.fg }}>
        {props.value}
      </Text>
    </Paper>
  );
  return props.to ? (
    <Link to={props.to} style={{ textDecoration: 'none', color: 'inherit' }}>
      {body}
    </Link>
  ) : (
    body
  );
}

export function AlertBanner({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <Alert
      color="red"
      variant="filled"
      role="alert"
      radius={0}
      icon={<IconAlertOctagon />}
      data-testid="expired-banner"
      className="ao-pulse ao-danger-fill"
    >
      <Group justify="space-between" wrap="nowrap">
        <Text fw={700} c="white">{t('banner.expired', { count })}</Text>
        <Button component={Link} to="/fleet?compliance_status=EXPIRED" size="xs" color="dark" variant="white">
          {t('action.show')}
        </Button>
      </Group>
    </Alert>
  );
}

const ServerNowContext = createContext<string | undefined>(undefined);
export const ServerNowProvider = ServerNowContext.Provider;
export const useServerNow = () => useContext(ServerNowContext);

export function DataFreshnessIndicator() {
  const { data } = useFreshness();
  const now = useServerNow();
  if (!data?.length) return null;
  const stale = data.some((d) => d.is_stale);
  const oldest = data.map((d) => d.loaded_at ?? '').sort()[0];
  const tone = stale ? toneVars.EXPIRED : toneVars.OK;
  return (
    <Tooltip
      label={
        <Stack gap={2}>
          {data.map((d) => (
            <Text key={d.source_name} size="xs">
              {tk(`feed.${d.source_name}`, undefined, d.source_name)}: {formatRelative(d.loaded_at, now)}
            </Text>
          ))}
        </Stack>
      }
    >
      <Group gap={6} aria-label={t('freshness.label')} wrap="nowrap">
        <ThemeIcon size={10} radius="xl" style={{ background: tone.fg }}>
          <span />
        </ThemeIcon>
        <Text size="sm" c={stale ? undefined : 'dimmed'} className={stale ? 'ao-text-expired' : undefined}>
          {stale ? t('freshness.stale') : t('freshness.updated', { relative: formatRelative(oldest, now) })}
        </Text>
      </Group>
    </Tooltip>
  );
}
