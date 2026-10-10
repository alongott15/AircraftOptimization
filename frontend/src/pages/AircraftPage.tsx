import {
  Anchor,
  Badge,
  Card,
  Grid,
  Group,
  Paper,
  ScrollArea,
  SimpleGrid,
  Stack,
  Table,
  Tabs,
  Text,
  Title,
} from '@mantine/core';
import { IconCertificate, IconChecklist, IconGauge, IconInfoCircle, IconShieldCheck, IconTool } from '@tabler/icons-react';
import { lazy, Suspense, useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';

import { useAircraft, useFlights, useReference } from '../api/hooks';
import type { AircraftDetail } from '../api/types';
import { ErrorState, LoadingState, Ltr, useServerNow } from '../components/common';
import { LimitProgress, useNames } from '../components/results';
import { OpStatusBadge, RiskBadge, SeverityBadge, StatusBadge, ToneBadge } from '../components/StatusBadge';
import { formatDate, formatDateTime, formatNumber, formatRelative } from '../i18n/format';
import { t, tk } from '../i18n';

const AircraftViewer = lazy(() => import('../features/aircraft3d/AircraftViewer'));

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <Stack gap={0}>
      <Text size="xs" c="dimmed">{label}</Text>
      <Text size="sm" component="div">{value}</Text>
    </Stack>
  );
}

function Overview({ a }: { a: AircraftDetail }) {
  const names = useNames();
  const now = useServerNow();
  const ref = useReference().data;
  const baseName = ref?.bases.find((b) => b.base_code === a.base_code)?.name_he?.toString() ?? a.base_code;
  return (
    <Stack>
      <SimpleGrid cols={2}>
        <Field label={t('field.type')} value={a.type_name_he} />
        <Field label={t('field.base')} value={baseName} />
        <Field label={t('field.squadron')} value={a.squadron_code ? names.squadron(a.squadron_code) : t('common.dash')} />
        <Field label={t('field.serial')} value={<Ltr>{a.serial_number}</Ltr>} />
        <Field label={t('field.manufactured')} value={<Ltr>{a.manufacture_date.slice(0, 4)}</Ltr>} />
        <Field label={t('field.engines')} value={a.engine_count} />
        <Field label={t('field.endurance')} value={`${formatNumber(a.max_endurance_hours, 1)} ${t('common.hoursShort')}`} />
        <Field label={t('field.lastFlight')} value={`${formatDateTime(a.last_flight_at)} (${formatRelative(a.last_flight_at, now)})`} />
        <Field label={t('field.idleDays')}
          value={a.idle ? <StatusBadge status={a.idle.status} size="sm" suffix={<>{' '}<Ltr>{formatNumber(a.idle.idle_days, 1)}</Ltr></>} /> : t('common.dash')} />
      </SimpleGrid>
      <Paper withBorder p="sm">
        <Group justify="space-between" mb={6}>
          <Text fw={600}>{t('risk.label')}</Text>
          <RiskBadge band={a.risk.band} probability={a.risk.probability} />
        </Group>
        {a.risk.top_factors.length > 0 && (
          <Text size="sm" c="dimmed">
            {t('aircraft.riskFactors')}:{' '}
            {a.risk.top_factors.map((f) => `${tk(`riskFeature.${f.feature_code}`)} (${tk(`riskFeature.${f.direction}`)})`).join(' · ')}
          </Text>
        )}
      </Paper>
      <Table withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t('field.counter')}</Table.Th>
            <Table.Th>{t('field.value')}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {a.counters.map((c) => (
            <Table.Tr key={c.unit}>
              <Table.Td>{tk(`source.${c.unit}`, undefined, names.unit(c.unit))}</Table.Td>
              <Table.Td className="ao-num"><Ltr>{formatNumber(c.value, 1)}</Ltr> {names.unit(c.unit)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}

function Inspections({ a, onZone }: { a: AircraftDetail; onZone: (z: string) => void }) {
  const names = useNames();
  const now = useServerNow();
  return (
    <Table verticalSpacing="xs" data-testid="inspections-table">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>{t('field.item')}</Table.Th>
          <Table.Th>{t('field.remaining')}</Table.Th>
          <Table.Th>{t('field.dueDate')}</Table.Th>
          <Table.Th>{t('field.status')}</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {a.inspections.map((ins) => {
          const binding = ins.limits.length > 1
            ? [...ins.limits].sort((x, y) => (x.remaining ?? 0) / (x.interval || 1) - (y.remaining ?? 0) / (y.interval || 1))[0]
            : undefined;
          return ins.limits.map((l, i) => (
            <Table.Tr key={`${ins.inspection_code}-${l.unit}`} className="ao-row" data-status={l.status}
              onClick={() => ins.zone_code && onZone(ins.zone_code)}>
              <Table.Td>
                {i === 0 && <Text fw={600} size="sm">{names.inspection(ins.inspection_code)}</Text>}
                {binding === l && <Badge size="xs" variant="outline">{t('aircraft.binding')}</Badge>}
              </Table.Td>
              <Table.Td>
                {l.due_at
                  ? <Text size="sm" className="ao-num">{formatRelative(l.due_at, now)}</Text>
                  : <LimitProgress remaining={l.remaining} interval={l.interval} status={l.status} unit={l.unit} />}
              </Table.Td>
              <Table.Td className="ao-num">
                {l.due_at ? formatDate(l.due_at)
                  : ins.predicted_due_at ? <Text size="sm">{formatDate(ins.predicted_due_at)} <Text span size="xs" c="dimmed">({t('common.predicted')})</Text></Text>
                    : t('common.dash')}
              </Table.Td>
              <Table.Td><StatusBadge status={l.status} size="sm" /></Table.Td>
            </Table.Tr>
          ));
        })}
      </Table.Tbody>
    </Table>
  );
}

function Permits({ a }: { a: AircraftDetail }) {
  const names = useNames();
  const now = useServerNow();
  return (
    <Table verticalSpacing="xs" data-testid="permits-table">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>{t('field.item')}</Table.Th>
          <Table.Th>{t('field.number')}</Table.Th>
          <Table.Th>{t('field.expires')}</Table.Th>
          <Table.Th>{t('field.restrictions')}</Table.Th>
          <Table.Th>{t('field.status')}</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {a.permits.map((p) => (
          <Table.Tr key={p.permit_type_code} className="ao-row" data-status={p.status}>
            <Table.Td fw={600}>{names.permit(p.permit_type_code)}</Table.Td>
            <Table.Td>{p.permit_number ? <Ltr>{p.permit_number}</Ltr> : t('aircraft.missingPermit')}</Table.Td>
            <Table.Td className="ao-num">
              {p.expires_at ? `${formatDateTime(p.expires_at)} (${formatRelative(p.expires_at, now)})` : t('common.dash')}
            </Table.Td>
            <Table.Td>
              <Group gap={4}>
                {p.restrictions.map((r) => <Badge key={r} size="sm" variant="outline" color="orange">{tk(`restriction.${r}`, undefined, r)}</Badge>)}
              </Group>
            </Table.Td>
            <Table.Td><StatusBadge status={p.status} size="sm" /></Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function MaintenanceActions({ a, selectedZone, onZone }: {
  a: AircraftDetail; selectedZone: string | null; onZone: (z: string) => void;
}) {
  const names = useNames();
  const now = useServerNow();
  if (!a.maintenance_actions.length) return <Text c="dimmed">{t('aircraft.noMas')}</Text>;
  return (
    <Stack gap="xs" data-testid="ma-list">
      {a.maintenance_actions.map((m) => (
        <Paper key={m.ma_number} withBorder p="sm" style={{ cursor: 'pointer', outline: selectedZone === m.zone_code ? '2px solid var(--mantine-primary-color-filled)' : undefined }}
          onClick={() => m.zone_code && onZone(m.zone_code)} data-ma={m.ma_number}>
          <Group justify="space-between" wrap="nowrap">
            <Group gap="xs">
              <SeverityBadge severity={m.severity} size="sm" />
              <Text fw={700} size="sm"><Ltr>{m.ma_number}</Ltr></Text>
            </Group>
            <Text size="xs" c="dimmed">{names.zone(m.zone_code)} · {t('field.ata')} <Ltr>{m.ata_chapter ?? '—'}</Ltr></Text>
          </Group>
          <Text size="sm" mt={4}>{m.title}</Text>
          <Group gap="md" mt={4}>
            <Text size="xs" c="dimmed">{t('field.opened')}: {formatDate(m.opened_at)}</Text>
            {m.deferral && (
              <Group gap={4}>
                <Text size="xs" c="dimmed">{t('field.deferralUntil')}: {formatDateTime(m.deferral.expires_at)} ({formatRelative(m.deferral.expires_at, now)})</Text>
                <StatusBadge status={m.deferral.status} size="xs" />
              </Group>
            )}
            {m.affects_qualification_code && (
              <ToneBadge tone="EXPIRED" size="xs">{t('field.revokedBy')}: {names.qualification(m.affects_qualification_code)}</ToneBadge>
            )}
          </Group>
        </Paper>
      ))}
    </Stack>
  );
}

function Qualifications({ a }: { a: AircraftDetail }) {
  const names = useNames();
  return (
    <Table verticalSpacing="xs" data-testid="quals-table">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>{t('field.item')}</Table.Th>
          <Table.Th>{t('field.validTo')}</Table.Th>
          <Table.Th>{t('field.effective')}</Table.Th>
          <Table.Th>{t('field.status')}</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {a.qualifications.map((q) => (
          <Table.Tr key={q.qualification_code} className="ao-row" data-status={q.status}>
            <Table.Td fw={600} td={q.effective ? undefined : 'line-through'}>{names.qualification(q.qualification_code)}</Table.Td>
            <Table.Td className="ao-num">{q.valid_to ? formatDate(q.valid_to) : t('common.dash')}</Table.Td>
            <Table.Td>
              {q.effective ? t('common.yes') : q.revoked_by
                ? (q.revoked_by.kind === 'MA'
                  ? t('aircraft.revokedByMa', { ref: q.revoked_by.ref })
                  : t('aircraft.revokedByPermit', { permit: names.permit(q.revoked_by.ref), restriction: tk(`restriction.${q.revoked_by.restriction ?? ''}`) }))
                : t('common.no')}
            </Table.Td>
            <Table.Td><StatusBadge status={q.status} size="sm" /></Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function Usage({ tail, active }: { tail: string; active: boolean }) {
  const flights = useFlights(tail, active);
  if (!active) return null;
  if (flights.isPending) return <LoadingState />;
  if (flights.isError) return <ErrorState error={flights.error} />;
  return (
    <Stack gap="xs">
      <Text fw={600}>{t('aircraft.recentFlights')}</Text>
      <ScrollArea h={420}>
        <Table striped verticalSpacing={4}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('field.takeoff')}</Table.Th>
              <Table.Th>{t('field.missionType')}</Table.Th>
              <Table.Th>{t('field.flightHours')}</Table.Th>
              <Table.Th>{t('field.landings')}</Table.Th>
              <Table.Th>{t('field.touchAndGoes')}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {flights.data.items.map((f) => (
              <Table.Tr key={f.takeoff_at}>
                <Table.Td className="ao-num">{formatDateTime(f.takeoff_at)}</Table.Td>
                <Table.Td>{tk(`missionType.${f.mission_type}`)}</Table.Td>
                <Table.Td className="ao-num">{formatNumber(f.flight_hours, 1)}</Table.Td>
                <Table.Td className="ao-num">{f.landings}</Table.Td>
                <Table.Td className="ao-num">{f.touch_and_goes}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </ScrollArea>
    </Stack>
  );
}

export default function AircraftPage() {
  const { tail = '' } = useParams();
  const detail = useAircraft(tail);
  const [zone, setZone] = useState<string | null>(null);
  const [tab, setTab] = useState<string | null>('overview');

  if (detail.isPending) return <LoadingState />;
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />;
  const a = detail.data;
  const selectZone = (z: string | null) => setZone((cur) => (cur === z ? null : z));

  return (
    <Stack mt="md" gap="md">
      <Card>
        <Group justify="space-between" wrap="wrap" gap="sm">
          <Group gap="sm">
            <Title order={2} className="ao-tail" data-testid="aircraft-title"><Ltr>{a.tail_number}</Ltr></Title>
            <Text c="dimmed">{a.type_name_he}</Text>
          </Group>
          <Group gap="xs">
            <OpStatusBadge status={a.derived_status} />
            <StatusBadge status={a.compliance_status} />
            {a.expired_count > 0 && (
              <ToneBadge tone="EXPIRED" pulse>{t('aircraft.expiredCount', { count: a.expired_count })}</ToneBadge>
            )}
            {a.grounding_ma_count > 0 && (
              <ToneBadge tone="EXPIRED">{t('aircraft.groundingMas', { count: a.grounding_ma_count })}</ToneBadge>
            )}
            <Anchor component={Link} to="/fleet" size="sm">{t('action.back')}</Anchor>
          </Group>
        </Group>
      </Card>
      <Grid gutter="md">
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Card h="100%">
            <Tabs value={tab} onChange={setTab} keepMounted={false}>
              <Tabs.List>
                <Tabs.Tab value="overview" leftSection={<IconInfoCircle size={16} />}>{t('tab.overview')}</Tabs.Tab>
                <Tabs.Tab value="inspections" leftSection={<IconChecklist size={16} />}>{t('tab.inspections')}</Tabs.Tab>
                <Tabs.Tab value="permits" leftSection={<IconCertificate size={16} />}>{t('tab.permits')}</Tabs.Tab>
                <Tabs.Tab value="mas" leftSection={<IconTool size={16} />}>{t('tab.mas')} ({a.open_ma_count})</Tabs.Tab>
                <Tabs.Tab value="qualifications" leftSection={<IconShieldCheck size={16} />}>{t('tab.qualifications')}</Tabs.Tab>
                <Tabs.Tab value="usage" leftSection={<IconGauge size={16} />}>{t('tab.usage')}</Tabs.Tab>
              </Tabs.List>
              <ScrollArea.Autosize mah={620} mt="sm">
                <Tabs.Panel value="overview"><Overview a={a} /></Tabs.Panel>
                <Tabs.Panel value="inspections"><Inspections a={a} onZone={selectZone} /></Tabs.Panel>
                <Tabs.Panel value="permits"><Permits a={a} /></Tabs.Panel>
                <Tabs.Panel value="mas"><MaintenanceActions a={a} selectedZone={zone} onZone={selectZone} /></Tabs.Panel>
                <Tabs.Panel value="qualifications"><Qualifications a={a} /></Tabs.Panel>
                <Tabs.Panel value="usage"><Usage tail={a.tail_number} active={tab === 'usage'} /></Tabs.Panel>
              </ScrollArea.Autosize>
            </Tabs>
          </Card>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Suspense fallback={<LoadingState />}>
            <AircraftViewer detail={a} selectedZone={zone} onSelectZone={(z) => { selectZone(z); if (z) setTab('mas'); }} />
          </Suspense>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
