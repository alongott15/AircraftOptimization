import {
  Alert,
  Button,
  Card,
  Collapse,
  Grid,
  Group,
  MultiSelect,
  NumberInput,
  SegmentedControl,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { IconAdjustments, IconBulb, IconDeviceFloppy, IconPlayerPlay } from '@tabler/icons-react';
import { useMemo, useState, type FormEvent } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiError, type FieldError } from '../api/client';
import {
  useAircraftList,
  useCreateMission,
  useDurationSuggestion,
  useMission,
  useOptimize,
  useReference,
  useUpdateMission,
} from '../api/hooks';
import type { Mission, MissionInput } from '../api/types';
import { Disclaimer, ErrorState, LoadingState, errorMessage, useServerNow } from '../components/common';
import { bandError, ExpiryBandsEditor, type BandRow } from '../components/ExpiryBands';
import { defaultStartLocal, formatHours, localInputToUtc, parseDuration, utcToLocalInput } from '../i18n/format';
import { t, tk } from '../i18n';

type EngineReq = MissionInput['engine_requirement'];
type MissionTypeCode = MissionInput['mission_type'];

interface FormState {
  name: string;
  mission_type: MissionTypeCode;
  priority: string;
  notes: string;
  required_aircraft_count: number;
  required_qualifications: string[];
  engine_requirement: EngineReq;
  same_type_required: boolean;
  start_local: string;
  duration: string;
  est_landings: number;
  est_touch_and_goes: number;
  est_engine_starts: number;
  departure_base_code: string | null;
  duration_buffer_pct: number | '';  // only set from the ML suggestion (FR-52); no manual buffer fields
  weights_preset: string | null;
  expiry_windows: BandRow[];
  squadron_codes: string[];
  aircraft_type_codes: string[];
  force_include: string[];
  force_exclude: string[];
}

function fromMission(m: Mission): FormState {
  const o = m.overrides as Record<string, number | string | null | undefined>;
  const num = (v: unknown): number | '' => (typeof v === 'number' ? v : '');
  return {
    name: m.name, mission_type: m.mission_type, priority: String(m.priority), notes: m.notes ?? '',
    required_aircraft_count: m.required_aircraft_count, required_qualifications: m.required_qualifications,
    engine_requirement: m.engine_requirement, same_type_required: m.same_type_required,
    start_local: utcToLocalInput(m.start_at), duration: formatHours(m.est_duration_hours), est_landings: m.est_landings,
    est_touch_and_goes: m.est_touch_and_goes, est_engine_starts: m.est_engine_starts,
    departure_base_code: m.departure_base_code, duration_buffer_pct: num(o.duration_buffer_pct),
    weights_preset: typeof o.weights_preset === 'string' ? o.weights_preset : null,
    force_include: m.force_include, force_exclude: m.force_exclude,
    expiry_windows: (m.expiry_windows ?? []).map((w) => ({ unit: w.unit, within: w.within, until: w.until })),
    squadron_codes: m.squadron_codes ?? [], aircraft_type_codes: m.aircraft_type_codes ?? [],
  };
}

/** /missions/new and /missions/:id/edit (only DRAFT / OPTIMIZED missions are editable). */
export function MissionFormPage() {
  const { id } = useParams();
  const missionId = id ? Number(id) : null;
  const existing = useMission(missionId ?? 0, missionId !== null);
  if (missionId === null) return <MissionForm />;
  if (existing.isPending) return <LoadingState />;
  if (existing.isError) return <ErrorState error={existing.error} />;
  return <MissionForm key={existing.data.updated_at} initial={fromMission(existing.data)} missionId={missionId} />;
}

function MissionForm({ initial, missionId }: { initial?: FormState; missionId?: number }) {
  const now = useServerNow();
  const reference = useReference();
  const fleet = useAircraftList({ page_size: 200, sort: 'tail_number' });
  const create = useCreateMission();
  const update = useUpdateMission();
  const optimize = useOptimize();
  const navigate = useNavigate();
  const [advanced, { toggle }] = useDisclosure(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [f, setF] = useState<FormState>(() => initial ?? ({
    name: '', mission_type: 'TRAINING', priority: '3', notes: '', required_aircraft_count: 1,
    required_qualifications: [], engine_requirement: 'ANY', same_type_required: false,
    start_local: defaultStartLocal(now), duration: '2:00', est_landings: 1, est_touch_and_goes: 0, est_engine_starts: 1,
    departure_base_code: null, duration_buffer_pct: '',
    weights_preset: null, force_include: [], force_exclude: [], expiry_windows: [], squadron_codes: [],
    aircraft_type_codes: [],
  }));
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setF((cur) => ({ ...cur, [key]: value }));
  const ref = reference.data;
  const durationHours = parseDuration(f.duration);
  const tails = useMemo(() => (fleet.data?.items ?? []).map((a) => a.tail_number), [fleet.data]);
  const suggestionType = f.engine_requirement === 'SINGLE' ? 'SE-100' : 'TP-200';
  const suggestion = useDurationSuggestion(
    { mission_type: f.mission_type, type_code: suggestionType, duration_hours: durationHours ?? 0,
      landings: f.est_landings, touch_and_goes: f.est_touch_and_goes },
    durationHours !== null && durationHours > 0,
  );

  const submit = async (e: FormEvent, runOptimizer = true) => {
    e.preventDefault();
    setErrors({});
    setFormError(null);
    const local: Record<string, string> = {};
    if (!f.name.trim()) local.name = t('val.FIELD_REQUIRED');
    if (durationHours === null) local.est_duration_hours = t('val.DURATION_FORMAT');
    if (!f.departure_base_code) local.departure_base_code = t('val.FIELD_REQUIRED');
    if (!f.start_local) local.start_at = t('val.FIELD_REQUIRED');
    const bands = bandError(f.expiry_windows);
    if (bands) local.expiry_windows = bands;
    if (Object.keys(local).length) {
      setErrors(local);
      return;
    }
    const body: MissionInput = {
      name: f.name.trim(), mission_type: f.mission_type, priority: Number(f.priority), notes: f.notes || null,
      required_aircraft_count: f.required_aircraft_count, required_qualifications: f.required_qualifications,
      engine_requirement: f.engine_requirement, same_type_required: f.same_type_required,
      start_at: localInputToUtc(f.start_local), est_duration_hours: durationHours ?? 0, est_landings: f.est_landings,
      est_touch_and_goes: f.est_touch_and_goes, est_engine_starts: f.est_engine_starts,
      departure_base_code: f.departure_base_code ?? '', force_include: f.force_include, force_exclude: f.force_exclude,
      squadron_codes: f.squadron_codes, aircraft_type_codes: f.aircraft_type_codes,
      expiry_windows: f.expiry_windows.map((w) => ({
        unit: w.unit as NonNullable<MissionInput['expiry_windows']>[number]['unit'],
        within: Number(w.within || 0),
        until: Number(w.until),
      })),
      overrides: {
        // calendar/landing buffers are admin policy only (FR-34 replaced them); the FH buffer can only be raised
        duration_buffer_pct: f.duration_buffer_pct === '' ? null : f.duration_buffer_pct,
        weights_preset: (f.weights_preset ?? null) as NonNullable<MissionInput['overrides']>['weights_preset'],
      },
    };
    try {
      const mission = missionId
        ? await update.mutateAsync({ id: missionId, body })
        : await create.mutateAsync(body);
      notifications.show({ color: 'green', message: t(missionId ? 'mission.saved' : 'mission.created') });
      if (runOptimizer) {
        try {
          await optimize.mutateAsync({ id: mission.mission_id });
        } catch (err) {
          notifications.show({ color: 'red', message: errorMessage(err) });
        }
      }
      navigate(`/missions/${mission.mission_id}`);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'VALIDATION_ERROR') {
        const mapped: Record<string, string> = {};
        (err.body.errors ?? []).forEach((fe: FieldError) => {
          mapped[fe.field || '_'] = tk(`val.${fe.code}`, fe.params ?? undefined, t('val.INVALID_VALUE'));
        });
        setErrors(mapped);
        setFormError(t('err.VALIDATION_ERROR'));
      } else {
        setFormError(errorMessage(err));
      }
    }
  };

  const typeOptions = (['TRAINING', 'TRANSPORT', 'FERRY', 'OTHER'] as const).map((v) => ({ value: v, label: tk(`missionType.${v}`) }));
  const busy = create.isPending || update.isPending || optimize.isPending;

  return (
    <form onSubmit={(e) => void submit(e)} noValidate>
      <Stack mt="md" gap="md" maw={1100}>
        <Title order={2}>{missionId ? t('mission.editTitle') : t('mission.newTitle')}</Title>
        {formError && <Alert color="red" role="alert">{formError}{errors._ ? ` — ${errors._}` : ''}</Alert>}
        <Grid>
          <Grid.Col span={{ base: 12, md: 6 }}>
            <Card h="100%">
              <Title order={5} mb="sm">{t('mission.sectionDetails')}</Title>
              <Stack gap="sm">
                <TextInput label={t('field.name')} required value={f.name} error={errors.name}
                  onChange={(e) => set('name', e.currentTarget.value)} data-testid="mission-name" />
                <Group grow>
                  <Select label={t('field.missionType')} data={typeOptions} value={f.mission_type} allowDeselect={false}
                    onChange={(v) => v && set('mission_type', v as MissionTypeCode)} />
                  <Select label={t('field.priority')} data={['1', '2', '3', '4', '5']} value={f.priority} allowDeselect={false}
                    onChange={(v) => v && set('priority', v)} />
                </Group>
                <Textarea label={t('field.notes')} autosize minRows={2} value={f.notes}
                  onChange={(e) => set('notes', e.currentTarget.value)} />
              </Stack>
            </Card>
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 6 }}>
            <Card h="100%">
              <Title order={5} mb="sm">{t('mission.sectionAircraft')}</Title>
              <Stack gap="sm">
                <NumberInput label={t('field.requiredCount')} min={1} max={20} value={f.required_aircraft_count}
                  error={errors.required_aircraft_count} allowDecimal={false}
                  onChange={(v) => set('required_aircraft_count', Number(v) || 1)} data-testid="mission-count" />
                <Group grow align="flex-start">
                  <MultiSelect label={t('field.squadrons')} searchable clearable value={f.squadron_codes}
                    placeholder={f.squadron_codes.length ? undefined : t('mission.anySquadron')}
                    data={(ref?.squadrons ?? []).map((q) => ({ value: String(q.squadron_code), label: String(q.name_he) }))}
                    onChange={(v) => set('squadron_codes', v)} error={errors.squadron_codes} data-testid="mission-squadrons" />
                  <MultiSelect label={t('field.aircraftTypes')} searchable clearable value={f.aircraft_type_codes}
                    placeholder={f.aircraft_type_codes.length ? undefined : t('mission.anyType')}
                    data={(ref?.aircraft_types ?? []).map((x) => ({ value: String(x.type_code), label: String(x.name_he) }))}
                    onChange={(v) => set('aircraft_type_codes', v)} error={errors.aircraft_type_codes} data-testid="mission-types" />
                </Group>
                <Text size="xs" c="dimmed" mt={-6}>{t('mission.scopeHint')}</Text>
                <MultiSelect label={t('field.qualifications')} searchable value={f.required_qualifications}
                  data={(ref?.qualifications ?? []).map((q) => ({ value: String(q.qualification_code), label: String(q.name_he) }))}
                  onChange={(v) => set('required_qualifications', v)} error={errors.required_qualifications}
                  data-testid="mission-quals" />
                <Stack gap={4}>
                  <Text size="sm" fw={500}>{t('field.engineRequirement')}</Text>
                  <SegmentedControl value={f.engine_requirement} onChange={(v) => set('engine_requirement', v as EngineReq)}
                    data={(['ANY', 'SINGLE', 'TWIN', 'MIN_2'] as const).map((v) => ({ value: v, label: tk(`engineReq.${v}`) }))}
                    data-testid="mission-engines" />
                </Stack>
                <Switch label={t('field.sameType')} checked={f.same_type_required}
                  onChange={(e) => set('same_type_required', e.currentTarget.checked)} />
              </Stack>
            </Card>
          </Grid.Col>
          <Grid.Col span={12}>
            <Card>
              <Title order={5} mb="sm">{t('mission.sectionTime')}</Title>
              <Grid>
                <Grid.Col span={{ base: 12, sm: 6, md: 4 }}>
                  <TextInput type="datetime-local" label={t('field.startAt')} value={f.start_local} required
                    error={errors.start_at} onChange={(e) => set('start_local', e.currentTarget.value)}
                    data-testid="mission-start" />
                </Grid.Col>
                <Grid.Col span={{ base: 12, sm: 6, md: 4 }}>
                  <TextInput label={t('field.duration')} description={t('field.durationHint')} value={f.duration} dir="ltr"
                    error={errors.est_duration_hours} onChange={(e) => set('duration', e.currentTarget.value)}
                    data-testid="mission-duration" />
                </Grid.Col>
                <Grid.Col span={{ base: 12, sm: 6, md: 4 }}>
                  <Select label={t('field.departureBase')} required value={f.departure_base_code} error={errors.departure_base_code}
                    data={(ref?.bases ?? []).map((b) => ({ value: String(b.base_code), label: String(b.name_he) }))}
                    onChange={(v) => set('departure_base_code', v)} data-testid="mission-base" />
                </Grid.Col>
                <Grid.Col span={{ base: 12, sm: 4 }}>
                  <NumberInput label={t('field.landings')} min={1} max={50} value={f.est_landings} allowDecimal={false}
                    error={errors.est_landings} onChange={(v) => set('est_landings', Number(v) || 1)} data-testid="mission-landings" />
                </Grid.Col>
                <Grid.Col span={{ base: 12, sm: 4 }}>
                  <NumberInput label={t('field.touchAndGoes')} min={0} max={100} value={f.est_touch_and_goes} allowDecimal={false}
                    error={errors.est_touch_and_goes} onChange={(v) => set('est_touch_and_goes', Number(v) || 0)}
                    data-testid="mission-tg" />
                </Grid.Col>
                <Grid.Col span={{ base: 12, sm: 4 }}>
                  <NumberInput label={t('field.engineStarts')} min={1} max={20} value={f.est_engine_starts} allowDecimal={false}
                    onChange={(v) => set('est_engine_starts', Number(v) || 1)} />
                </Grid.Col>
              </Grid>
              {suggestion.data && (
                <Alert mt="sm" variant="light" color="blue" icon={<IconBulb />}>
                  <Group justify="space-between">
                    <Text size="sm">
                      {t('mission.suggestedBuffer', { pct: suggestion.data.suggested_buffer_pct })}
                      {f.duration_buffer_pct !== '' && ` — ${t('mission.bufferApplied', { pct: f.duration_buffer_pct })}`}
                    </Text>
                    {f.duration_buffer_pct === '' ? (
                      <Button size="xs" variant="light" onClick={() => set('duration_buffer_pct', suggestion.data.suggested_buffer_pct)}>
                        {t('action.apply')}
                      </Button>
                    ) : (
                      <Button size="xs" variant="subtle" onClick={() => set('duration_buffer_pct', '')}>{t('action.cancel')}</Button>
                    )}
                  </Group>
                </Alert>
              )}
            </Card>
          </Grid.Col>
          <Grid.Col span={12}>
            <Card>
              <Title order={5} mb={4}>{t('expiry.title')}</Title>
              <Text size="sm" c="dimmed" mb="sm">{t('expiry.hint')}</Text>
              <ExpiryBandsEditor rows={f.expiry_windows} onChange={(rows) => set('expiry_windows', rows)} testId="expiry"
                error={Object.entries(errors).find(([k]) => k.startsWith('expiry_windows'))?.[1]} />
            </Card>
          </Grid.Col>
          <Grid.Col span={12}>
            <Card>
              <Button variant="subtle" leftSection={<IconAdjustments size={16} />} onClick={toggle}>{t('action.advanced')}</Button>
              <Collapse in={advanced}>
                <Grid mt="sm">
                  <Grid.Col span={{ base: 12, sm: 4 }}>
                    <Select label={t('field.weightsPreset')} clearable value={f.weights_preset}
                      data={['BALANCED', 'MAX_SAFETY', 'MAX_AVAILABILITY'].map((v) => ({ value: v, label: tk(`preset.${v}`) }))}
                      onChange={(v) => set('weights_preset', v)} />
                  </Grid.Col>
                  <Grid.Col span={{ base: 12, sm: 6 }}>
                    <MultiSelect label={t('field.forceInclude')} searchable data={tails} value={f.force_include}
                      error={errors.force_include} onChange={(v) => set('force_include', v)} />
                  </Grid.Col>
                  <Grid.Col span={{ base: 12, sm: 6 }}>
                    <MultiSelect label={t('field.forceExclude')} searchable data={tails} value={f.force_exclude}
                      error={errors.force_exclude} onChange={(v) => set('force_exclude', v)} />
                  </Grid.Col>
                </Grid>
              </Collapse>
            </Card>
          </Grid.Col>
        </Grid>
        <Disclaimer />
        <Group>
          <Button type="submit" size="md" loading={busy} leftSection={<IconPlayerPlay size={18} />} data-testid="optimize-btn">
            {t('action.optimize')}
          </Button>
          <Button size="md" variant="default" disabled={busy} leftSection={<IconDeviceFloppy size={18} />}
            onClick={(e) => void submit(e, false)} data-testid="save-draft-btn">
            {t('action.saveDraft')}
          </Button>
        </Group>
      </Stack>
    </form>
  );
}
