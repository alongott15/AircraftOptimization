/** Multi-mission horizon planning (FR-46): plan open missions together, then assign them all at once. */
import { Alert, Badge, Button, Card, Checkbox, Group, Modal, Stack, Table, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconInfoCircle, IconPlayerPlay } from '@tabler/icons-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { useHorizonApply, useHorizonPlan, useMissions } from '../api/hooks';
import { Disclaimer, ErrorState, LoadingState, Ltr, errorMessage, useServerNow } from '../components/common';
import { ToneBadge } from '../components/StatusBadge';
import { formatDateTime } from '../i18n/format';
import { t, tk } from '../i18n';

const HORIZON_MS = 14 * 24 * 3600 * 1000;

export default function PlanningPage() {
  const now = useServerNow();
  const missions = useMissions({ page_size: 200 });
  const planMut = useHorizonPlan();
  const apply = useHorizonApply();
  const [selected, setSelected] = useState<number[] | null>(null);
  const [confirming, setConfirming] = useState(false);

  const open = useMemo(() => {
    const start = now ? Date.parse(now) : Date.now();
    return (missions.data?.items ?? [])
      .filter((m) => (m.status === 'DRAFT' || m.status === 'OPTIMIZED'))
      .filter((m) => Date.parse(m.start_at) >= start - 5 * 60_000 && Date.parse(m.start_at) <= start + HORIZON_MS)
      .sort((a, b) => a.start_at.localeCompare(b.start_at));
  }, [missions.data, now]);
  const chosen = selected ?? open.map((m) => m.mission_id);
  const toggle = (id: number) =>
    setSelected(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id]);
  const plan = planMut.data;

  const doApply = () => {
    if (!plan) return;
    apply.mutate(plan.assignments.filter((a) => a.aircraft_ids.length > 0), {
      onSuccess: () => {
        setConfirming(false);
        planMut.reset();
        notifications.show({ color: 'green', icon: <IconCheck size={16} />, message: t('planning.applied') });
      },
      onError: (err) => {
        setConfirming(false);
        notifications.show({ color: 'red', message: errorMessage(err) });
      },
    });
  };

  if (missions.isPending) return <LoadingState />;
  if (missions.isError) return <ErrorState error={missions.error} />;

  return (
    <Stack mt="md" gap="md">
      <Title order={2}>{t('planning.title')}</Title>
      <Alert variant="light" color="blue" icon={<IconInfoCircle />}>{t('planning.intro')}</Alert>
      <Card>
        <Title order={5} mb="sm">{t('planning.openMissions')}</Title>
        {open.length === 0 ? <Text c="dimmed">{t('planning.noMissions')}</Text> : (
          <Table data-testid="planning-missions">
            <Table.Tbody>
              {open.map((m) => (
                <Table.Tr key={m.mission_id}>
                  <Table.Td w={40}>
                    <Checkbox checked={chosen.includes(m.mission_id)} onChange={() => toggle(m.mission_id)}
                      aria-label={m.name} />
                  </Table.Td>
                  <Table.Td fw={600}><Link to={`/missions/${m.mission_id}`}>{m.name}</Link></Table.Td>
                  <Table.Td className="ao-num">{formatDateTime(m.start_at)}</Table.Td>
                  <Table.Td>{tk(`missionType.${m.mission_type}`)}</Table.Td>
                  <Table.Td className="ao-num">{m.required_aircraft_count}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        <Group mt="md">
          <Button leftSection={<IconPlayerPlay size={16} />} disabled={chosen.length === 0} loading={planMut.isPending}
            onClick={() => planMut.mutate({ mission_ids: chosen })} data-testid="horizon-compute">
            {t('planning.compute')}
          </Button>
        </Group>
        {planMut.isError && <Alert color="red" mt="sm">{errorMessage(planMut.error)}</Alert>}
      </Card>

      {plan && (
        <Card aria-live="polite" data-testid="horizon-result">
          <Group justify="space-between" mb="sm">
            <Title order={5}>{t('planning.result')}</Title>
            <Text size="sm" c="dimmed">{t('planning.solverInfo', { solver: tk(`solver.${plan.solver_status}`), ms: plan.duration_ms })}</Text>
          </Group>
          <Table verticalSpacing="sm">
            <Table.Tbody>
              {plan.missions.map((m) => (
                <Table.Tr key={m.mission_id} data-mission={m.mission_id}>
                  <Table.Td fw={600}>{m.name}</Table.Td>
                  <Table.Td className="ao-num">{formatDateTime(m.start_at)}</Table.Td>
                  <Table.Td>
                    <ToneBadge tone={m.shortfall > 0 ? 'EXPIRED' : 'OK'} size="sm">
                      {t('planning.seats', { assigned: m.assigned.length, required: m.required })}
                    </ToneBadge>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4}>
                      {m.assigned.map((a) => (
                        <Badge key={a.aircraft_id} variant="light" component={Link} to={`/aircraft/${a.tail_number}`}
                          style={{ cursor: 'pointer' }}><Ltr>{a.tail_number}</Ltr></Badge>
                      ))}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Stack gap={2}>
                      <Text size="xs" c="dimmed">{t('planning.eligible', { count: m.eligible_count })}</Text>
                      {m.shortfall > 0 && <Text size="xs" className="ao-text-expired" fw={700}>{t('planning.shortfall', { count: m.shortfall })}</Text>}
                      {m.overlaps_with.length > 0 && (
                        <Text size="xs" c="dimmed">
                          {t('planning.overlaps', { list: m.overlaps_with.map((id) => plan.missions.find((x) => x.mission_id === id)?.name ?? id).join(', ') })}
                        </Text>
                      )}
                    </Stack>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Disclaimer />
          <Group mt="md">
            <Button color="teal" leftSection={<IconCheck size={16} />} onClick={() => setConfirming(true)}
              disabled={plan.assignments.every((a) => a.aircraft_ids.length === 0)} data-testid="horizon-apply">
              {t('planning.apply')}
            </Button>
          </Group>
        </Card>
      )}

      <Modal opened={confirming} onClose={() => setConfirming(false)} title={t('planning.confirmTitle')} centered>
        <Stack>
          <Text>{t('planning.confirmBody', { count: plan?.assignments.filter((a) => a.aircraft_ids.length).length ?? 0 })}</Text>
          <Disclaimer />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirming(false)}>{t('action.cancel')}</Button>
            <Button color="teal" loading={apply.isPending} onClick={doApply} data-testid="horizon-confirm">
              {t('action.confirmAssign')}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
