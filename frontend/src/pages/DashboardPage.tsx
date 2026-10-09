import {
  Anchor, Card, Grid, Group, Paper, Progress, ScrollArea, SegmentedControl, SimpleGrid, Stack, Table, Text, Title,
} from '@mantine/core';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { useAircraftList, useAlerts, useFleetSummary, useReference, useTimeline } from '../api/hooks';
import { EmptyState, ErrorState, KpiCard, LoadingState, Ltr } from '../components/common';
import { useNames, useRemainingText } from '../components/results';
import { StatusBadge } from '../components/StatusBadge';
import { formatDate, formatNumber } from '../i18n/format';
import { t } from '../i18n';

export function DashboardPage() {
  const summary = useFleetSummary();
  const alerts = useAlerts();
  const [days, setDays] = useState('30');  // FR-13: 14 / 30 / 90 days
  const timeline = useTimeline(Number(days));
  const fleet = useAircraftList({ page_size: 200 });
  const reference = useReference();
  const names = useNames();
  const remaining = useRemainingText();
  const navigate = useNavigate();

  if (summary.isPending) return <LoadingState />;
  if (summary.isError) return <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />;
  const s = summary.data;
  const typeName = (code: string) =>
    reference.data?.aircraft_types.find((x) => x.type_code === code)?.name_he?.toString() ?? code;

  return (
    <Stack gap="lg" mt="md">
      <Title order={2}>{t('dashboard.title')}</Title>
      <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }}>
        <KpiCard testId="kpi-availability" label={t('dashboard.availability')}
          value={`${formatNumber(s.availability_pct, 0)}%`} tone={s.availability_pct >= 70 ? 'OK' : 'EXPIRING_SOON'} />
        <KpiCard testId="kpi-available" label={t('dashboard.availableNow')}
          value={<Ltr>{`${s.available_now} / ${s.total_aircraft}`}</Ltr>} />
        <KpiCard testId="kpi-expired" label={t('dashboard.expiredAircraft')} value={s.aircraft_with_expired_items}
          tone={s.aircraft_with_expired_items ? 'EXPIRED' : 'OK'} to="/fleet?compliance_status=EXPIRED" />
        <KpiCard testId="kpi-expiring" label={t('dashboard.expiringItems')} value={s.items_expiring_7d}
          tone={s.items_expiring_7d ? 'EXPIRING_SOON' : 'OK'} to="/fleet?compliance_status=EXPIRING_SOON" />
        <KpiCard testId="kpi-unknown" label={t('dashboard.unknownAircraft')} value={s.aircraft_unknown}
          tone={s.aircraft_unknown ? 'UNKNOWN' : 'OK'} to="/fleet?compliance_status=UNKNOWN" />
        <KpiCard testId="kpi-grounding" label={t('dashboard.grounding')} value={s.open_grounding_mas}
          tone={s.open_grounding_mas ? 'EXPIRED' : 'OK'} to="/fleet?status=GROUNDED" />
      </SimpleGrid>

      <Grid>
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Card h="100%">
            <Title order={4} mb="sm">{t('dashboard.alerts')}</Title>
            {alerts.isPending ? <LoadingState /> : alerts.isError ? <ErrorState error={alerts.error} /> : (
              <ScrollArea h={360}>
                {alerts.data.length === 0 ? <EmptyState text={t('dashboard.noAlerts')} /> : (
                  <Stack gap={6} data-testid="alerts-list">
                    {alerts.data.map((a, i) => (
                      <Group key={`${a.tail_number}-${a.source}-${a.ref}-${i}`} gap="xs" wrap="nowrap"
                        className="ao-row" data-status={a.status} p={6}
                        onClick={() => navigate(`/aircraft/${a.tail_number}`)}>
                        <StatusBadge status={a.status} size="sm" />
                        <Anchor component={Link} to={`/aircraft/${a.tail_number}`} className="ao-tail" fw={600}>
                          <Ltr>{a.tail_number}</Ltr>
                        </Anchor>
                        <Text size="sm" style={{ flex: 1 }} lineClamp={1}>{names.ref(a.source, a.ref)}</Text>
                        <Text size="sm" c="dimmed" className="ao-num">{remaining(a)}</Text>
                      </Group>
                    ))}
                  </Stack>
                )}
              </ScrollArea>
            )}
          </Card>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Card h="100%">
            <Group justify="space-between" mb="sm" wrap="nowrap">
              <Title order={4}>{t('dashboard.timeline', { days })}</Title>
              <SegmentedControl size="xs" value={days} onChange={setDays} aria-label={t('dashboard.timelineRange')}
                data-testid="timeline-range" data={['14', '30', '90']} />
            </Group>
            {timeline.isPending ? <LoadingState /> : timeline.isError ? <ErrorState error={timeline.error} /> : (
              <ScrollArea h={360}>
                <Table striped highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>{t('field.date')}</Table.Th>
                      <Table.Th>{t('field.tailNumber')}</Table.Th>
                      <Table.Th>{t('field.item')}</Table.Th>
                      <Table.Th>{t('field.status')}</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {timeline.data.map((x, i) => (
                      <Table.Tr key={`${x.tail_number}-${x.ref}-${x.unit}-${i}`}>
                        <Table.Td className="ao-num">
                          {formatDate(x.date)} {x.predicted && <Text span size="xs" c="dimmed">({t('common.predicted')})</Text>}
                        </Table.Td>
                        <Table.Td><Anchor component={Link} to={`/aircraft/${x.tail_number}`}><Ltr>{x.tail_number}</Ltr></Anchor></Table.Td>
                        <Table.Td>{names.ref(x.source, x.ref)}</Table.Td>
                        <Table.Td><StatusBadge status={x.status} size="xs" /></Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </ScrollArea>
            )}
          </Card>
        </Grid.Col>
      </Grid>

      <Grid>
        <Grid.Col span={{ base: 12, md: 4 }}>
          <Card h="100%">
            <Title order={4} mb="sm">{t('dashboard.byType')}</Title>
            <Stack>
              {s.by_type.map((bt) => (
                <div key={bt.type_code}>
                  <Group justify="space-between">
                    <Text size="sm">{typeName(bt.type_code)}</Text>
                    <Text size="sm" className="ao-num"><Ltr>{`${bt.available} / ${bt.total}`}</Ltr></Text>
                  </Group>
                  <Progress value={bt.total ? (100 * bt.available) / bt.total : 0} size="lg"
                    aria-label={typeName(bt.type_code)} />
                </div>
              ))}
            </Stack>
          </Card>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 8 }}>
          <Card h="100%">
            <Title order={4} mb="sm">{t('dashboard.fleetGrid')}</Title>
            {fleet.data ? (
              <SimpleGrid cols={{ base: 3, sm: 4, lg: 6 }} spacing="xs" data-testid="fleet-grid">
                {fleet.data.items.map((a) => (
                  <Paper key={a.aircraft_id} withBorder p={8} className="ao-tile" data-status={a.compliance_status}
                    style={{ cursor: 'pointer' }} onClick={() => navigate(`/aircraft/${a.tail_number}`)}>
                    <Text fw={700} size="sm" className="ao-tail"><Ltr>{a.tail_number}</Ltr></Text>
                    <Text size="xs" c="dimmed">{t(`status.${a.compliance_status}`)}</Text>
                  </Paper>
                ))}
              </SimpleGrid>
            ) : <LoadingState />}
          </Card>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
