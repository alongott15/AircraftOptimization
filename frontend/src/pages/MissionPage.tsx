import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Modal,
  Paper,
  RingProgress,
  SimpleGrid,
  Stack,
  Table,
  Tabs,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle, IconCheck, IconCopy, IconCube, IconPencil, IconRefresh, IconX } from '@tabler/icons-react';
import { useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { ApiError } from '../api/client';
import { useAssign, useMission, useMissionAction, useOptimize, useReference, useRun } from '../api/hooks';
import type { Candidate, Mission, OptimizationRun } from '../api/types';
import { useAuth } from '../auth/AuthProvider';
import { Disclaimer, ErrorState, LoadingState, Ltr, errorMessage } from '../components/common';
import { MissionMaintenanceHint } from '../components/maintenance';
import { ReasonList, ScoreBreakdown, useNames } from '../components/results';
import { ToneBadge } from '../components/StatusBadge';
import { formatDateTime, formatHours, formatNumber } from '../i18n/format';
import { t, tk } from '../i18n';
import { renderReason } from '../i18n/reasonCodes';

function MissionSummary({ m }: { m: Mission }) {
  const names = useNames();
  const ref = useReference().data;
  const base = ref?.bases.find((b) => b.base_code === m.departure_base_code)?.name_he?.toString() ?? m.departure_base_code;
  const items: [string, ReactNode][] = [
    [t('field.missionType'), tk(`missionType.${m.mission_type}`)],
    [t('field.requiredCount'), m.required_aircraft_count],
    [t('field.startAt'), formatDateTime(m.start_at)],
    [t('mission.end'), formatDateTime(m.end_at)],
    [t('field.duration'), <Ltr key="d">{formatHours(m.est_duration_hours)}</Ltr>],
    [t('field.landings'), m.est_landings],
    [t('field.touchAndGoes'), m.est_touch_and_goes],
    [t('field.departureBase'), base],
    [t('field.engineRequirement'), tk(`engineReq.${m.engine_requirement}`)],
    [t('field.qualifications'), m.required_qualifications.map((q) => names.qualification(q)).join(', ') || t('common.none')],
    [t('field.squadrons'), m.squadron_codes.map((q) => names.squadron(q)).join(', ') || t('mission.anySquadron')],
    [t('field.aircraftTypes'), m.aircraft_type_codes.map((q) => names.type(q)).join(', ') || t('mission.anyType')],
  ];
  return (
    <SimpleGrid cols={{ base: 2, sm: 3, md: 5 }} spacing="sm">
      {items.map(([label, value]) => (
        <Stack key={label} gap={0}>
          <Text size="xs" c="dimmed">{label}</Text>
          <Text size="sm" fw={500}>{value}</Text>
        </Stack>
      ))}
    </SimpleGrid>
  );
}

function AircraftResultCard({ c }: { c: Candidate }) {
  const score = c.score ?? 0;
  return (
    <Paper withBorder p="md" data-testid="result-card" data-tail={c.tail_number}>
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Stack gap={2}>
          <Anchor component={Link} to={`/aircraft/${c.tail_number}`} fw={700} size="lg" className="ao-tail">
            <Ltr>{c.tail_number}</Ltr>
          </Anchor>
          <Text size="xs" c="dimmed"><Ltr>{c.type_code}</Ltr> · {t('mission.rank')} {c.rank}</Text>
          {typeof c.risk_probability === 'number' && (
            <Text size="xs" c="dimmed">{t('risk.label')}: {Math.round(c.risk_probability * 100)}%</Text>
          )}
        </Stack>
        <RingProgress size={74} thickness={7} roundCaps
          sections={[{ value: score, color: score >= 75 ? 'teal' : score >= 55 ? 'yellow' : 'orange' }]}
          label={<Text ta="center" fw={700} size="sm" className="ao-num">{formatNumber(score, 0)}</Text>} />
      </Group>
      {c.score_breakdown && <ScoreBreakdown breakdown={c.score_breakdown} />}
      {c.warnings.length > 0 && (
        <Stack gap={4} mt="sm">
          <Text size="xs" fw={600}>{t('mission.warnings')}</Text>
          <ReasonList reasons={c.warnings} kind="warning" />
        </Stack>
      )}
      <Button component={Link} to={`/aircraft/${c.tail_number}`} variant="subtle" size="xs" mt="xs"
        leftSection={<IconCube size={14} />}>
        {t('action.view3d')}
      </Button>
    </Paper>
  );
}

function ExcludedList({ candidates }: { candidates: Candidate[] }) {
  const names = useNames();
  if (!candidates.length) return null;
  return (
    <Accordion variant="separated" data-testid="excluded-list">
      {candidates.map((c) => (
        <Accordion.Item key={c.aircraft_id} value={String(c.aircraft_id)} data-tail={c.tail_number}>
          <Accordion.Control icon={<IconX size={16} color="var(--ao-expired-fg)" />}>
            <Group gap="sm" wrap="nowrap">
              <Text fw={700} className="ao-tail"><Ltr>{c.tail_number}</Ltr></Text>
              <Text size="sm" c="dimmed" lineClamp={1}>
                {c.reasons[0] ? (c.reasons.length > 1 ? `${c.reasons.length} · ` : '') : ''}
                {c.reasons[0] ? renderReason(c.reasons[0], names) : ''}
              </Text>
            </Group>
          </Accordion.Control>
          <Accordion.Panel>
            <ReasonList reasons={c.reasons} kind="reason" />
            {c.unblock_hints.length > 0 && (
              <Stack gap={4} mt="sm">
                <Text size="xs" fw={600}>{t('mission.whatUnblocks')}</Text>
                <ReasonList reasons={c.unblock_hints} kind="hint" />
              </Stack>
            )}
            {c.warnings.length > 0 && <ReasonList reasons={c.warnings} kind="warning" />}
          </Accordion.Panel>
        </Accordion.Item>
      ))}
    </Accordion>
  );
}

function Results({ run, mission, canPlan }: { run: OptimizationRun; mission: Mission; canPlan: boolean }) {
  const assign = useAssign();
  const navigate = useNavigate();
  const [setIndex, setSetIndex] = useState<string>('0');
  const [confirming, setConfirming] = useState(false);
  const byId = new Map(run.candidates.map((c) => [c.aircraft_id, c]));
  const eligible = run.candidates.filter((c) => c.eligible);
  const excluded = run.candidates.filter((c) => !c.eligible);
  const chosen = run.sets.find((s) => String(s.set_index) === setIndex) ?? run.sets[0];
  const chosenIds = new Set(chosen?.aircraft_ids ?? []);
  const others = eligible.filter((c) => !chosenIds.has(c.aircraft_id));
  const nearest = run.nearest_to_eligible.map((id) => byId.get(id)).filter((c): c is Candidate => Boolean(c));

  const doAssign = async () => {
    if (!chosen) return;
    try {
      await assign.mutateAsync({ id: mission.mission_id, runId: run.run_id, setIndex: chosen.set_index });
      notifications.show({ color: 'green', icon: <IconCheck size={16} />, message: t('mission.assigned') });
      setConfirming(false);
    } catch (err) {
      setConfirming(false);
      const stale = err instanceof ApiError && ['RUN_STALE', 'ELIGIBILITY_CHANGED'].includes(err.code);
      notifications.show({ color: 'red', message: errorMessage(err), autoClose: stale ? 8000 : 5000 });
      if (stale) navigate(0);
    }
  };

  return (
    <Stack gap="md" aria-live="polite">
      <Group justify="space-between">
        <Text fw={700} size="lg" data-testid="result-summary">
          {t('mission.summary', { selected: chosen?.aircraft_ids.length ?? 0, eligible: run.n_eligible })}
        </Text>
        <Text size="sm" c="dimmed">
          {t('mission.runInfo', { solver: tk(`solver.${run.solver_status}`), ms: run.duration_ms, eligible: run.n_eligible, total: run.n_aircraft })}
        </Text>
      </Group>
      {run.run_warnings.length > 0 && (
        <Alert color="yellow" icon={<IconAlertTriangle />}><ReasonList reasons={run.run_warnings} kind="warning" /></Alert>
      )}
      {run.shortfall > 0 && (
        <Alert color="red" icon={<IconAlertTriangle />} title={t('mission.shortfall', { count: run.shortfall })}>
          {nearest.length > 0 && (
            <Text size="sm">{t('mission.nearest')}: {nearest.map((c) => c.tail_number).join(', ')}</Text>
          )}
          {mission.status !== 'CANCELLED' && <MissionMaintenanceHint missionId={mission.mission_id} />}
        </Alert>
      )}
      {run.sets.length > 0 && (
        <Tabs value={setIndex} onChange={(v) => v && setSetIndex(v)}>
          <Tabs.List>
            {run.sets.map((s) => (
              <Tabs.Tab key={s.set_index} value={String(s.set_index)} data-testid={`set-tab-${s.set_index}`}>
                {s.set_index === 0 ? t('mission.recommended') : t('mission.alternative', { n: s.set_index })}
                <Badge size="xs" variant="light" ms={6} className="ao-num">{formatNumber(s.objective, 1)}</Badge>
              </Tabs.Tab>
            ))}
          </Tabs.List>
        </Tabs>
      )}
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} data-testid="recommended-set">
        {(chosen?.aircraft_ids ?? []).map((id) => {
          const c = byId.get(id);
          return c ? <AircraftResultCard key={id} c={c} /> : null;
        })}
      </SimpleGrid>
      <Disclaimer />
      {canPlan && chosen && mission.status !== 'CANCELLED' && (
        <Group>
          <Button size="md" color="teal" leftSection={<IconCheck size={18} />} onClick={() => setConfirming(true)}
            data-testid="assign-btn">
            {t('action.assign')}
          </Button>
        </Group>
      )}
      {others.length > 0 && (
        <Card>
          <Title order={5} mb="xs">{t('mission.eligibleOthers', { count: others.length })}</Title>
          <Table>
            <Table.Tbody>
              {others.map((c) => (
                <Table.Tr key={c.aircraft_id}>
                  <Table.Td fw={600}><Anchor component={Link} to={`/aircraft/${c.tail_number}`}><Ltr>{c.tail_number}</Ltr></Anchor></Table.Td>
                  <Table.Td className="ao-num">{formatNumber(c.score, 1)}</Table.Td>
                  <Table.Td><ReasonList reasons={c.warnings} kind="warning" /></Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      )}
      <Title order={4}>{t('mission.excluded', { count: excluded.length })}</Title>
      <ExcludedList candidates={excluded} />
      <Modal opened={confirming} onClose={() => setConfirming(false)} title={t('mission.assignTitle')} centered>
        <Stack>
          <Text>{t('mission.assignBody', { name: mission.name })}</Text>
          <Group gap="xs">
            {(chosen?.tail_numbers ?? []).map((tail) => <Badge key={tail} size="lg" variant="light"><Ltr>{tail}</Ltr></Badge>)}
          </Group>
          <Disclaimer />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirming(false)}>{t('action.cancel')}</Button>
            <Button color="teal" loading={assign.isPending} onClick={() => void doAssign()} data-testid="confirm-assign">
              {t('action.confirmAssign')}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}

export function MissionPage() {
  const { id = '0' } = useParams();
  const missionId = Number(id);
  const mission = useMission(missionId);
  const run = useRun(mission.data?.latest_run_id);
  const optimize = useOptimize();
  const duplicate = useMissionAction('duplicate');
  const cancel = useMissionAction('cancel');
  const navigate = useNavigate();
  const { can } = useAuth();

  if (mission.isPending) return <LoadingState />;
  if (mission.isError) return <ErrorState error={mission.error} />;
  const m = mission.data;
  const closed = m.status === 'CANCELLED' || m.status === 'COMPLETED';

  const rerun = () =>
    optimize.mutate({ id: missionId }, { onError: (err) => notifications.show({ color: 'red', message: errorMessage(err) }) });

  return (
    <Stack mt="md" gap="md">
      <Card>
        <Group justify="space-between" mb="sm">
          <Group gap="sm">
            <Title order={2} data-testid="mission-title">{m.name}</Title>
            <ToneBadge tone={m.status === 'ASSIGNED' ? 'OK' : m.status === 'CANCELLED' ? 'UNKNOWN' : 'INFO'} >
              <span data-testid="mission-status">{tk(`missionStatus.${m.status}`)}</span>
            </ToneBadge>
          </Group>
          {can('PLAN') && (
            <Group gap="xs">
              {(m.status === 'DRAFT' || m.status === 'OPTIMIZED') && (
                <Button component={Link} to={`/missions/${missionId}/edit`} variant="default"
                  leftSection={<IconPencil size={16} />} data-testid="edit-mission-btn">
                  {t('action.edit')}
                </Button>
              )}
              {!closed && (
                <Button leftSection={<IconRefresh size={16} />} loading={optimize.isPending} onClick={rerun} variant="light">
                  {m.latest_run_id ? t('action.reoptimize') : t('action.optimize')}
                </Button>
              )}
              <Button variant="default" leftSection={<IconCopy size={16} />} loading={duplicate.isPending}
                onClick={() => duplicate.mutate(missionId, { onSuccess: (dup) => navigate(`/missions/${dup.mission_id}`) })}>
                {t('action.duplicate')}
              </Button>
              {!closed && (
                <Button variant="subtle" color="red" leftSection={<IconX size={16} />} loading={cancel.isPending}
                  onClick={() => cancel.mutate(missionId, { onSuccess: () => notifications.show({ message: t('mission.cancelled') }) })}>
                  {t('action.cancelMission')}
                </Button>
              )}
            </Group>
          )}
        </Group>
        <MissionSummary m={m} />
        {m.assignments.length > 0 && (
          <Group mt="md" gap="xs">
            <Text fw={600} size="sm">{t('mission.assignedAircraft')}:</Text>
            {m.assignments.map((a) => (
              <Badge key={a.aircraft_id} color="teal" size="lg" variant="light" component={Link} to={`/aircraft/${a.tail_number ?? ''}`}
                style={{ cursor: 'pointer' }}>
                <Ltr>{a.tail_number}</Ltr>
              </Badge>
            ))}
          </Group>
        )}
      </Card>
      {optimize.isPending && <LoadingState />}
      {!m.latest_run_id ? (
        <Card><Text c="dimmed">{t('mission.noRun')}</Text></Card>
      ) : run.isPending ? <LoadingState /> : run.isError ? <ErrorState error={run.error} /> : (
        <Results key={run.data.run_id} run={run.data} mission={m} canPlan={can('PLAN') && !closed} />
      )}
    </Stack>
  );
}
