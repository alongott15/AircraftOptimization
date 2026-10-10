/**
 * Maintenance board (FR-90…FR-92): the work each aircraft needs as a kanban, manual scheduling (drag & drop or the
 * "move to" menu / schedule dialog for keyboard users) and mission-driven recommendations. Planning only — moving a
 * card never changes eligibility; the "verified" column fills from the maintenance source data.
 */
import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  CloseButton,
  Group,
  Menu,
  Modal,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCalendarPlus, IconCheck, IconDotsVertical, IconInfoCircle, IconTool } from '@tabler/icons-react';
import { useMemo, useState, type DragEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { ApiError, type FieldError } from '../api/client';
import { useMaintenanceBoard, useMaintenanceRecommendations, useReference, useUpdateTask } from '../api/hooks';
import type { MaintenanceItem, MaintenanceTaskInput, MissionMaintenance, TaskStatus } from '../api/types';
import { useAuth } from '../auth/AuthProvider';
import { BidiText, Disclaimer, ErrorState, LoadingState, Ltr, errorMessage, useServerNow } from '../components/common';
import { RecommendedAircraftRow, TASK_COLUMNS, TaskStatusBadge, missionShortText, toneStyle } from '../components/maintenance';
import { useNames } from '../components/results';
import { StatusBadge, ToneBadge } from '../components/StatusBadge';
import { formatDateTime, formatNumber, localInputToUtc, utcToLocalInput } from '../i18n/format';
import { t, tk } from '../i18n';
import { taskTitle, type Names } from '../i18n/reasonCodes';

type Editable = Exclude<TaskStatus, 'VERIFIED'>;
const EDITABLE: Editable[] = ['REQUIRED', 'SCHEDULED', 'IN_PROGRESS', 'DONE'];
const NEEDS_START = new Set<TaskStatus>(['SCHEDULED', 'IN_PROGRESS']);
const FILTER_KEYS = ['type_code', 'base_code', 'squadron_code'] as const;
type Draft = { item: MaintenanceItem; status: Editable };
const DRAG_TYPE = 'application/x-ao-task'; // only our cards can be dropped on a lane

function dueText(item: MaintenanceItem, names: Names): string | null {
  if (item.due_at) return t('maintenance.due', { date: formatDateTime(item.due_at) });
  if (item.remaining === null || !item.unit) return null;
  const unit = item.unit === 'IDLE_DAY' ? t('common.daysShort') : names.unit(item.unit);
  const value = formatNumber(Math.abs(item.remaining), 1);
  return item.remaining < 0 ? t('common.overBy', { value, unit }) : t('common.remaining', { value, unit });
}

function TaskCard({ item, canEdit, dragging, onDragStart, onDragEnd, onMove, onSchedule }: {
  item: MaintenanceItem;
  canEdit: boolean;
  dragging: boolean;
  onDragStart: (e: DragEvent<HTMLDivElement>) => void;
  onDragEnd: () => void;
  onMove: (status: Editable) => void;
  onSchedule: () => void;
}) {
  const names = useNames();
  const title = taskTitle(item.action, item.ref, names);
  const verified = item.status === 'VERIFIED';
  const movable = canEdit && !verified;
  const due = verified ? null : dueText(item, names);
  const tone = verified ? 'OK' : item.compliance_status;
  return (
    <Paper withBorder p="sm" radius="md" className="ao-kanban-card" data-tone={tone} data-dragging={dragging || undefined}
      draggable={movable} onDragStart={onDragStart} onDragEnd={onDragEnd} data-testid="task-card"
      data-key={item.card_id} data-tail={item.tail_number}>
      <Stack gap={6}>
        <Group justify="space-between" wrap="nowrap" gap="xs">
          <Group gap={6} wrap="nowrap" style={{ minWidth: 0 }}>
            <Anchor component={Link} to={`/aircraft/${encodeURIComponent(item.tail_number)}`} fw={700} className="ao-tail"
              draggable={false}>
              <Ltr>{item.tail_number}</Ltr>
            </Anchor>
            <Text size="xs" c="dimmed" truncate>{names.type(item.type_code)}</Text>
          </Group>
          {movable && (
            <Menu position="bottom-end" withinPortal>
              <Menu.Target>
                <ActionIcon variant="subtle" size="sm" aria-label={t('maintenance.moveMenu', { task: title, tail: item.tail_number })}
                  data-testid="task-move">
                  <IconDotsVertical size={16} />
                </ActionIcon>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Label>{t('maintenance.moveTo')}</Menu.Label>
                {EDITABLE.filter((s) => s !== item.status).map((s) => (
                  <Menu.Item key={s} onClick={() => onMove(s)} data-testid={`move-${s}`}>
                    {tk(`maintenance.column.${s}`)}
                  </Menu.Item>
                ))}
              </Menu.Dropdown>
            </Menu>
          )}
        </Group>
        <Text size="sm" fw={600}><BidiText text={title} /></Text>
        <Group gap={6}>
          {verified ? <ToneBadge tone="OK" size="sm">{t('maintenance.column.VERIFIED')}</ToneBadge>
            : <StatusBadge status={item.compliance_status} size="sm" />}
          {item.blocking && !verified && <ToneBadge tone="EXPIRED" size="sm">{t('maintenance.blocking')}</ToneBadge>}
        </Group>
        {due && (
          <Text size="xs" className={item.compliance_status === 'EXPIRED' ? 'ao-text-expired ao-num' : 'ao-num'}>{due}</Text>
        )}
        {item.planned_start && (
          <Text size="xs" className="ao-num" data-testid="task-planned">
            {item.planned_end
              ? t('maintenance.plannedRange', { start: formatDateTime(item.planned_start), end: formatDateTime(item.planned_end) })
              : t('maintenance.planned', { start: formatDateTime(item.planned_start) })}
          </Text>
        )}
        {item.assignee && <Text size="xs">{t('maintenance.assignee', { name: item.assignee })}</Text>}
        {item.notes && <Text size="xs" c="dimmed" lineClamp={2}>{item.notes}</Text>}
        {item.unblocks.length > 0 && (
          <Group gap={4} data-testid="task-unblocks">
            <Text size="xs" fw={600}>{t('maintenance.unblocks')}</Text>
            {item.unblocks.map((m) => (
              <Badge key={m.mission_id} size="sm" variant="light" component={Link}
                to={`/missions/${m.mission_id}`} style={{ ...toneStyle('INFO'), cursor: 'pointer' }} draggable={false}>
                {m.name}
              </Badge>
            ))}
          </Group>
        )}
        {verified && item.updated_at && (
          <Text size="xs" c="dimmed" className="ao-num">{t('maintenance.updated', { date: formatDateTime(item.updated_at) })}</Text>
        )}
        {movable && (
          <Button size="compact-xs" variant="subtle" leftSection={<IconCalendarPlus size={14} />} onClick={onSchedule}
            style={{ alignSelf: 'flex-start' }} data-testid="task-schedule">
            {t('maintenance.schedule')}
          </Button>
        )}
      </Stack>
    </Paper>
  );
}

function ScheduleForm({ draft, onClose }: { draft: Draft; onClose: () => void }) {
  const names = useNames();
  const update = useUpdateTask();
  const { item } = draft;
  const [status, setStatus] = useState<Editable>(draft.status);
  const [start, setStart] = useState(item.planned_start ? utcToLocalInput(item.planned_start) : '');
  const [end, setEnd] = useState(item.planned_end ? utcToLocalInput(item.planned_end) : '');
  const [assignee, setAssignee] = useState(item.assignee ?? '');
  const [notes, setNotes] = useState(item.notes ?? '');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const firstMission = item.unblocks[0];

  const submit = () => {
    const errs: Record<string, string> = {};
    if (NEEDS_START.has(status) && !start) errs.planned_start = t('val.FIELD_REQUIRED');
    if (start && end && end < start) errs.planned_end = t('val.INVALID_VALUE');
    setErrors(errs);
    if (Object.keys(errs).length) return;
    const body: MaintenanceTaskInput = {
      item_key: item.item_key, status, planned_start: start ? localInputToUtc(start) : null,
      planned_end: end ? localInputToUtc(end) : null, assignee: assignee || null, notes: notes || null,
    };
    update.mutate(body, {
      onSuccess: () => {
        notifications.show({ color: 'green', icon: <IconCheck size={16} />, message: t('maintenance.saved') });
        onClose();
      },
      onError: (err) => {
        if (err instanceof ApiError && err.body.errors?.length) {
          setErrors(Object.fromEntries(err.body.errors.map((fe: FieldError) => [fe.field, tk(`val.${fe.code}`, fe.params ?? {})])));
        } else {
          notifications.show({ color: 'red', message: errorMessage(err) });
        }
      },
    });
  };

  return (
    <Stack gap="sm">
      <Group gap="xs">
        <Text fw={700} className="ao-tail"><Ltr>{item.tail_number}</Ltr></Text>
        <Text><BidiText text={taskTitle(item.action, item.ref, names)} /></Text>
      </Group>
      {item.due_at && <Text size="sm" className="ao-num">{t('maintenance.due', { date: formatDateTime(item.due_at) })}</Text>}
      {firstMission?.start_at && (
        <Text size="sm" className="ao-num" fw={600}>
          {t('maintenance.rec.completeBy', { date: formatDateTime(firstMission.start_at) })} · {firstMission.name}
        </Text>
      )}
      <Select label={t('maintenance.field.status')} value={status} allowDeselect={false}
        data={EDITABLE.map((s) => ({ value: s, label: tk(`maintenance.column.${s}`) }))}
        onChange={(v) => v && setStatus(v as Editable)} data-testid="schedule-status" />
      <SimpleGrid cols={{ base: 1, xs: 2 }}>
        <TextInput type="datetime-local" label={t('maintenance.field.start')} value={start} required={NEEDS_START.has(status)}
          error={errors.planned_start} onChange={(e) => setStart(e.currentTarget.value)} data-testid="schedule-start" />
        <TextInput type="datetime-local" label={t('maintenance.field.end')} value={end} error={errors.planned_end}
          onChange={(e) => setEnd(e.currentTarget.value)} data-testid="schedule-end" />
      </SimpleGrid>
      <TextInput label={t('maintenance.field.assignee')} value={assignee} maxLength={80} error={errors.assignee}
        onChange={(e) => setAssignee(e.currentTarget.value)} data-testid="schedule-assignee" />
      <Textarea label={t('maintenance.field.notes')} value={notes} maxLength={500} autosize minRows={2} error={errors.notes}
        onChange={(e) => setNotes(e.currentTarget.value)} data-testid="schedule-notes" />
      <Text size="xs" c="dimmed">{t('maintenance.columnHint.DONE')}</Text>
      <Group justify="flex-end">
        <Button variant="default" onClick={onClose}>{t('action.cancel')}</Button>
        <Button onClick={submit} loading={update.isPending} data-testid="schedule-save">{t('action.save')}</Button>
      </Group>
    </Stack>
  );
}

function Recommendations({ recs, statusOf, canEdit, onSchedule }: {
  recs: MissionMaintenance[];
  statusOf: (key: string) => TaskStatus | undefined;
  canEdit: boolean;
  onSchedule: (key: string) => void;
}) {
  const short = recs.filter((r) => r.shortfall > 0);
  return (
    <Card data-testid="maint-recs">
      <Group gap="xs" mb={4}>
        <IconTool size={18} aria-hidden />
        <Title order={4}>{t('maintenance.rec.title')}</Title>
      </Group>
      <Text size="sm" c="dimmed" mb="sm">{t('maintenance.rec.intro')}</Text>
      {short.length === 0 ? <Text size="sm">{t('maintenance.rec.none')}</Text> : (
        <SimpleGrid cols={{ base: 1, md: Math.min(short.length, 2), xl: Math.min(short.length, 3) }}>
          {short.map((m) => (
            <Paper key={m.mission_id} withBorder p="sm" radius="md" data-testid="rec-mission">
              <Stack gap="xs">
                <Group justify="space-between" wrap="nowrap" align="flex-start">
                  <Stack gap={0}>
                    <Anchor component={Link} to={`/missions/${m.mission_id}`} fw={700}>{m.name}</Anchor>
                    <Text size="xs" c="dimmed" className="ao-num">{formatDateTime(m.start_at)}</Text>
                  </Stack>
                  <ToneBadge tone="EXPIRED" size="sm">{t('planning.shortfall', { count: m.shortfall })}</ToneBadge>
                </Group>
                <Text size="sm" className="ao-num">{missionShortText(m)}</Text>
                {m.complete_by && (
                  <Text size="xs" fw={600} className="ao-num">
                    {t('maintenance.rec.completeBy', { date: formatDateTime(m.complete_by) })}
                  </Text>
                )}
                {m.candidates.length === 0 ? <Text size="sm">{t('maintenance.rec.noCandidates')}</Text> : (
                  // a lone mission gets the full width: spread its aircraft over columns
                  <SimpleGrid cols={{ base: 1, sm: short.length === 1 ? 2 : 1, lg: short.length === 1 ? 3 : 1 }} spacing="sm">
                    {m.candidates.map((c) => (
                      <RecommendedAircraftRow key={c.aircraft_id} cand={c} statusOf={statusOf}
                        action={(key) => canEdit && statusOf(key) !== 'VERIFIED' ? (
                          <Button size="compact-xs" variant="light" onClick={() => onSchedule(key)} data-testid="rec-schedule">
                            {t('maintenance.schedule')}
                          </Button>
                        ) : null} />
                    ))}
                  </SimpleGrid>
                )}
              </Stack>
            </Paper>
          ))}
        </SimpleGrid>
      )}
    </Card>
  );
}

export default function MaintenancePage() {
  const { can } = useAuth();
  const canEdit = can('MAINTAIN');
  const now = useServerNow();
  const reference = useReference();
  const [params, setParams] = useSearchParams();
  const query = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) ?? undefined]));
  const board = useMaintenanceBoard(query);
  const recs = useMaintenanceRecommendations();
  const update = useUpdateTask();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [overCol, setOverCol] = useState<TaskStatus | null>(null);
  const onlyBlocking = params.get('blocking') === '1';
  const onlyMissions = params.get('missions') === '1';
  const missionId = Number(params.get('mission')) || null;

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const items = useMemo(() => board.data?.items ?? [], [board.data]);
  // open cards by task key (a verified card of an earlier cycle may share the key — never act on it)
  const byKey = useMemo(() => new Map(items.filter((i) => i.status !== 'VERIFIED').map((i) => [i.item_key, i])), [items]);
  const visible = items.filter((i) => (!onlyBlocking || i.blocking) && (!onlyMissions || i.unblocks.length > 0)
    && (!missionId || i.unblocks.some((m) => m.mission_id === missionId)));
  const missionName = missionId ? recs.data?.find((m) => m.mission_id === missionId)?.name : undefined;

  const move = (item: MaintenanceItem, status: Editable) => {
    if (status === item.status) return;
    if (NEEDS_START.has(status) && !item.planned_start) {
      setDraft({ item, status }); // a schedule needs a date — ask for it
      return;
    }
    update.mutate({ item_key: item.item_key, status, planned_start: item.planned_start, planned_end: item.planned_end,
      assignee: item.assignee, notes: item.notes }, {
      onSuccess: () => notifications.show({ color: 'green', icon: <IconCheck size={16} />, message: t('maintenance.saved') }),
      onError: (err) => notifications.show({ color: 'red', message: errorMessage(err) }),
    });
  };

  const drop = (col: TaskStatus) => (e: DragEvent<HTMLElement>) => {
    e.preventDefault();
    setOverCol(null);
    const item = byKey.get(e.dataTransfer.getData(DRAG_TYPE));
    setDragKey(null);
    if (item && col !== 'VERIFIED') move(item, col);
  };

  if (board.isPending) return <LoadingState />;
  if (board.isError) return <ErrorState error={board.error} onRetry={() => void board.refetch()} />;
  const ref = reference.data;

  return (
    <Stack mt="md" gap="md">
      <Group justify="space-between">
        <Title order={2}>{t('maintenance.title')}</Title>
        <Text c="dimmed" size="sm" className="ao-num">
          {t('maintenance.count', { count: visible.length })} · {t('maintenance.asOf', { date: formatDateTime(board.data.as_of ?? now) })}
        </Text>
      </Group>
      <Alert variant="light" color="blue" icon={<IconInfoCircle />}>
        {t('maintenance.intro')}
        {!canEdit && <Text size="sm" fw={600} mt={4}>{t('maintenance.readOnly')}</Text>}
      </Alert>
      {recs.isError ? <ErrorState error={recs.error} /> : recs.data && (
        <Recommendations recs={recs.data} statusOf={(k) => byKey.get(k)?.status} canEdit={canEdit}
          onSchedule={(k) => {
            const item = byKey.get(k);
            if (item && item.status !== 'VERIFIED') setDraft({ item, status: item.status === 'REQUIRED' ? 'SCHEDULED' : item.status });
          }} />
      )}
      <Card>
        <Group gap="sm" align="flex-end">
          <Select label={t('field.type')} clearable w={200} value={params.get('type_code')}
            data={(ref?.aircraft_types ?? []).map((x) => ({ value: String(x.type_code), label: String(x.name_he) }))}
            onChange={(v) => setParam('type_code', v)} />
          <Select label={t('field.base')} clearable w={150} value={params.get('base_code')}
            data={(ref?.bases ?? []).map((x) => ({ value: String(x.base_code), label: String(x.name_he) }))}
            onChange={(v) => setParam('base_code', v)} />
          <Select label={t('field.squadron')} clearable w={210} value={params.get('squadron_code')}
            data={(ref?.squadrons ?? []).map((x) => ({ value: String(x.squadron_code), label: String(x.name_he) }))}
            onChange={(v) => setParam('squadron_code', v)} />
          <Stack gap={6} pb={4}>
            <Switch label={t('maintenance.onlyBlocking')} checked={onlyBlocking}
              onChange={(e) => setParam('blocking', e.currentTarget.checked ? '1' : null)} data-testid="only-blocking" />
            <Switch label={t('maintenance.onlyMissions')} checked={onlyMissions}
              onChange={(e) => setParam('missions', e.currentTarget.checked ? '1' : null)} />
          </Stack>
          {missionId && (
            <Badge size="lg" variant="light" style={toneStyle('INFO')} data-testid="mission-filter"
              rightSection={<CloseButton size="xs" aria-label={t('action.clearFilters')} onClick={() => setParam('mission', null)} />}>
              {t('maintenance.unblocks')} {missionName ?? missionId}
            </Badge>
          )}
        </Group>
      </Card>
      {canEdit && <Text size="xs" c="dimmed">{t('maintenance.dragHint')}</Text>}
      <div className="ao-kanban" data-testid="maint-board">
        {TASK_COLUMNS.map((col) => {
          const cards = visible.filter((i) => i.status === col);
          const droppable = canEdit && col !== 'VERIFIED';
          return (
            <section key={col} className="ao-kanban-col" data-status={col} data-over={overCol === col || undefined}
              aria-label={tk(`maintenance.column.${col}`)} data-testid={`col-${col}`}
              onDragOver={(e) => {
                if (!droppable || !e.dataTransfer.types.includes(DRAG_TYPE)) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                if (overCol !== col) setOverCol(col);
              }}
              onDragLeave={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOverCol(null);
              }}
              onDrop={drop(col)}>
              <Group justify="space-between" mb={2} wrap="nowrap">
                <TaskStatusBadge status={col} />
                <Badge size="sm" variant="light" style={toneStyle('UNKNOWN')} className="ao-num" data-testid="col-count">
                  {cards.length}
                </Badge>
              </Group>
              <Text size="xs" c="dimmed" mb="xs">{tk(`maintenance.columnHint.${col}`)}</Text>
              <Stack gap="xs">
                {cards.length === 0 && <Text size="sm" c="dimmed" ta="center" py="md">{t('maintenance.empty')}</Text>}
                {cards.map((item) => (
                  <TaskCard key={item.card_id} item={item} canEdit={canEdit} dragging={dragKey === item.item_key}
                    onDragStart={(e) => {
                      e.dataTransfer.setData(DRAG_TYPE, item.item_key);
                      e.dataTransfer.effectAllowed = 'move';
                      setDragKey(item.item_key);
                    }}
                    onDragEnd={() => {
                      setDragKey(null);
                      setOverCol(null);
                    }}
                    onMove={(s) => move(item, s)}
                    onSchedule={() => setDraft({ item, status: item.status === 'REQUIRED' ? 'SCHEDULED' : (item.status as Editable) })} />
                ))}
              </Stack>
            </section>
          );
        })}
      </div>
      <Disclaimer />
      <Modal opened={draft !== null} onClose={() => setDraft(null)} title={t('maintenance.scheduleTitle')} size="lg">
        {draft && <ScheduleForm key={`${draft.item.item_key}:${draft.status}`} draft={draft} onClose={() => setDraft(null)} />}
      </Modal>
    </Stack>
  );
}
