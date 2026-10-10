/** Maintenance tasks (FR-90…FR-92): titles, board-status badges and the mission-page recommendation hint. */
import { Anchor, Badge, Group, List, Loader, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { useMaintenanceRecommendations } from '../api/hooks';
import type { MissionMaintenance, RecommendedAircraft, TaskStatus } from '../api/types';
import { formatDateTime } from '../i18n/format';
import { t, tk } from '../i18n';
import { taskTitle } from '../i18n/reasonCodes';
import { toneVars, type Tone } from '../theme/tokens';
import { BidiText, Ltr } from './common';
import { useNames } from './results';

export const TASK_COLUMNS: TaskStatus[] = ['REQUIRED', 'SCHEDULED', 'IN_PROGRESS', 'DONE', 'VERIFIED'];

/** Badge colours from the status tokens (WCAG AA in light and dark — tokens test). */
export function toneStyle(tone: Tone) {
  return { background: toneVars[tone].bg, color: toneVars[tone].fg, border: `1px solid ${toneVars[tone].fg}`, maxWidth: 'none' };
}

const STATUS_TONE: Record<TaskStatus, Tone> = {
  REQUIRED: 'UNKNOWN', SCHEDULED: 'INFO', IN_PROGRESS: 'INFO', DONE: 'INFO', VERIFIED: 'OK',
};

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  return (
    <Badge size="sm" variant="light" style={{ ...toneStyle(STATUS_TONE[status]), flexShrink: 0 }}
      styles={{ label: { overflow: 'visible' } }} data-testid="task-status">
      {tk(`maintenance.column.${status}`)}
    </Badge>
  );
}

/** One recommended aircraft: tail, type and the tasks that would make it eligible (each verified by the engine). */
export function RecommendedAircraftRow({ cand, statusOf, action }: {
  cand: RecommendedAircraft;
  statusOf?: (itemKey: string) => TaskStatus | undefined;
  action?: (itemKey: string) => ReactNode;
}) {
  const names = useNames();
  return (
    <Stack gap={2} data-testid="rec-aircraft">
      <Group gap="xs">
        <Anchor component={Link} to={`/aircraft/${encodeURIComponent(cand.tail_number)}`} fw={700} className="ao-tail">
          <Ltr>{cand.tail_number}</Ltr>
        </Anchor>
        <Text size="sm" c="dimmed">{names.type(cand.type_code)}</Text>
      </Group>
      <List size="sm" spacing={2} ms="md">
        {cand.tasks.map((task) => {
          const status = statusOf?.(task.item_key);
          return (
            <List.Item key={task.item_key}>
              <Group gap="xs" wrap="wrap">
                <Text size="sm"><BidiText text={taskTitle(task.action, task.ref, names)} /></Text>
                {status && <TaskStatusBadge status={status} />}
                {action?.(task.item_key)}
              </Group>
            </List.Item>
          );
        })}
      </List>
    </Stack>
  );
}

export function missionShortText(m: MissionMaintenance): string {
  return t('maintenance.rec.mission', { eligible: m.eligible_count, required: m.required, shortfall: m.shortfall });
}

/** Mission page, inside the shortfall alert: which maintenance would close the gap (FR-92). */
export function MissionMaintenanceHint({ missionId }: { missionId: number }) {
  const recs = useMaintenanceRecommendations(missionId);
  if (recs.isPending) return <Loader size="xs" />;
  const rec = recs.data?.[0];
  if (!rec || rec.shortfall <= 0) return null;
  return (
    <Stack gap="xs" mt="xs" data-testid="mission-maint-hint">
      {rec.candidates.length === 0 ? (
        <Text size="sm">{t('maintenance.rec.noCandidates')}</Text>
      ) : (
        <>
          <Text size="sm" fw={600}>
            {t('maintenance.rec.missionHint')}{' '}
            {rec.complete_by && (
              <Text span size="sm" c="dimmed">
                ({t('maintenance.rec.completeBy', { date: formatDateTime(rec.complete_by) })})
              </Text>
            )}
          </Text>
          {rec.candidates.map((c) => <RecommendedAircraftRow key={c.aircraft_id} cand={c} />)}
        </>
      )}
      <Anchor component={Link} to={`/maintenance?mission=${missionId}`} size="sm" data-testid="to-maint-board">
        {t('maintenance.rec.toBoard')}
      </Anchor>
    </Stack>
  );
}
