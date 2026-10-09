/** TanStack Query hooks — components never call fetch directly (CODE_STYLE §3.2). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api, ApiError, type Query } from './client';
import type {
  AircraftDetail,
  AircraftPage,
  Alert,
  Assignment,
  AuditPage,
  DurationSuggestion,
  FleetSummary,
  FlightRow,
  HangarCapacity,
  HorizonPlan,
  Mission,
  MissionInput,
  MissionPage,
  Model,
  OptimizationRun,
  Policy,
  Reference,
  SourceFreshness,
  TgWeight,
  TgWeightInput,
  TimelineItem,
  User,
  Weights,
} from './types';

const retry = (count: number, error: unknown) =>
  !(error instanceof ApiError && [401, 403, 404, 422].includes(error.status)) && count < 2;

export const qk = {
  me: ['me'] as const,
  reference: ['reference'] as const,
  summary: ['fleet', 'summary'] as const,
  alerts: ['fleet', 'alerts'] as const,
  timeline: (days: number) => ['fleet', 'timeline', days] as const,
  freshness: ['fleet', 'freshness'] as const,
  aircraftList: (q: Query) => ['aircraft', 'list', q] as const,
  aircraft: (tail: string) => ['aircraft', 'detail', tail] as const,
  flights: (tail: string) => ['aircraft', 'flights', tail] as const,
  missions: (q: Query) => ['missions', 'list', q] as const,
  mission: (id: number) => ['missions', 'detail', id] as const,
  run: (id: string) => ['runs', id] as const,
  policy: ['settings', 'policy'] as const,
  hangar: ['settings', 'hangar'] as const,
  weights: ['settings', 'weights'] as const,
  tgWeights: ['settings', 'tg-weights'] as const,
  models: ['ml', 'models'] as const,
  audit: (q: Query) => ['audit', q] as const,
};

export const useMe = () =>
  useQuery({ queryKey: qk.me, queryFn: () => api<User>('/auth/me'), retry: false, staleTime: 60_000 });

export const useReference = () =>
  useQuery({ queryKey: qk.reference, queryFn: () => api<Reference>('/reference'), staleTime: Infinity, retry });

export const useFleetSummary = () =>
  useQuery({ queryKey: qk.summary, queryFn: () => api<FleetSummary>('/fleet/summary'), retry, staleTime: 30_000 });

export const useAlerts = () =>
  useQuery({ queryKey: qk.alerts, queryFn: () => api<Alert[]>('/fleet/alerts', { query: { limit: 200 } }), retry });

export const useTimeline = (days: number) =>
  useQuery({
    queryKey: qk.timeline(days),
    queryFn: () => api<TimelineItem[]>('/fleet/timeline', { query: { days } }),
    retry,
  });

export const useFreshness = () =>
  useQuery({ queryKey: qk.freshness, queryFn: () => api<SourceFreshness[]>('/fleet/data-freshness'), retry });

export const useAircraftList = (q: Query) =>
  useQuery({ queryKey: qk.aircraftList(q), queryFn: () => api<AircraftPage>('/aircraft', { query: q }), retry });

export const useAircraft = (tail: string) =>
  useQuery({
    queryKey: qk.aircraft(tail),
    queryFn: () => api<AircraftDetail>(`/aircraft/${encodeURIComponent(tail)}`),
    retry,
  });

export const useFlights = (tail: string, enabled: boolean) =>
  useQuery({
    queryKey: qk.flights(tail),
    queryFn: () =>
      api<{ items: FlightRow[]; total: number }>(`/aircraft/${encodeURIComponent(tail)}/flights`, {
        query: { page_size: 100 },
      }),
    enabled,
    retry,
  });

export const useMissions = (q: Query) =>
  useQuery({ queryKey: qk.missions(q), queryFn: () => api<MissionPage>('/missions', { query: q }), retry });

export const useMission = (id: number, enabled = true) =>
  useQuery({ queryKey: qk.mission(id), queryFn: () => api<Mission>(`/missions/${id}`), retry, enabled });

export const useRun = (runId: string | null | undefined) =>
  useQuery({
    queryKey: qk.run(runId ?? 'none'),
    queryFn: () => api<OptimizationRun>(`/optimization-runs/${runId ?? ''}`),
    enabled: Boolean(runId),
    retry,
    staleTime: Infinity,
  });

export const usePolicy = () => useQuery({ queryKey: qk.policy, queryFn: () => api<Policy>('/settings/policy'), retry });

export const useHangar = () =>
  useQuery({ queryKey: qk.hangar, queryFn: () => api<HangarCapacity[]>('/settings/hangar-capacity'), retry });

export const useModels = () => useQuery({ queryKey: qk.models, queryFn: () => api<Model[]>('/ml/models'), retry });

export const useAudit = (q: Query) =>
  useQuery({ queryKey: qk.audit(q), queryFn: () => api<AuditPage>('/audit', { query: q }), retry });

export const useDurationSuggestion = (q: Query, enabled: boolean) =>
  useQuery({
    queryKey: ['ml', 'duration', q],
    queryFn: () => api<DurationSuggestion>('/ml/duration-buffer', { query: q }),
    enabled,
    retry: false,
    staleTime: 60_000,
  });

// ── mutations ──────────────────────────────────────────────────────────
export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { username: string; password: string }) =>
      api<User>('/auth/login', { method: 'POST', body }),
    onSuccess: (user) => {
      qc.setQueryData(qk.me, user);
      void qc.invalidateQueries();
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<unknown>('/auth/logout', { method: 'POST' }),
    onSettled: () => {
      qc.clear();
      qc.setQueryData(qk.me, null);
    },
  });
}

export function useCreateMission() {
  return useMutation({ mutationFn: (body: MissionInput) => api<Mission>('/missions', { method: 'POST', body }) });
}

export function useUpdateMission() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: MissionInput }) =>
      api<Mission>(`/missions/${id}`, { method: 'PATCH', body }),
    onSuccess: (m) => {
      qc.setQueryData(qk.mission(m.mission_id), m);
      void qc.invalidateQueries({ queryKey: ['missions', 'list'] });
    },
  });
}

export function useAuthConfig() {
  return useQuery({ queryKey: ['auth', 'config'], queryFn: () => api<{ mode: 'local' | 'oidc' }>('/auth/config'),
    staleTime: Infinity, retry: false });
}

export function useHorizonPlan() {
  return useMutation({
    mutationFn: (body: { mission_ids?: number[] | null; weights_preset?: string | null }) =>
      api<HorizonPlan>('/planning/horizon', { method: 'POST', body }),
  });
}

export function useHorizonApply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (assignments: { mission_id: number; aircraft_ids: number[] }[]) =>
      api<{ applied: { mission_id: number; tail_numbers: string[] }[] }>('/planning/horizon/apply',
        { method: 'POST', body: { assignments } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['missions'] });
      void qc.invalidateQueries({ queryKey: ['fleet'] });
    },
  });
}

export function useOptimize() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, preset }: { id: number; preset?: string | null }) =>
      api<OptimizationRun>(`/missions/${id}/optimize`, { method: 'POST', body: { weights_preset: preset ?? null } }),
    onSuccess: (run) => {
      qc.setQueryData(qk.run(run.run_id), run);
      void qc.invalidateQueries({ queryKey: ['missions'] });
    },
  });
}

export function useAssign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, runId, setIndex }: { id: number; runId: string; setIndex: number }) =>
      api<Assignment[]>(`/missions/${id}/assign`, { method: 'POST', body: { run_id: runId, set_index: setIndex } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['missions'] });
      void qc.invalidateQueries({ queryKey: ['fleet'] });
      void qc.invalidateQueries({ queryKey: ['aircraft'] });
    },
  });
}

export function useMissionAction(action: 'duplicate' | 'cancel' | 'unassign') {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<Mission>(`/missions/${id}/${action}`, { method: 'POST' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['missions'] }),
  });
}

export function useUpdatePolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<Policy>) => api<Policy>('/settings/policy', { method: 'PUT', body }),
    onSuccess: (p) => qc.setQueryData(qk.policy, p),
  });
}

export const useWeights = () =>
  useQuery({ queryKey: qk.weights, queryFn: () => api<Weights>('/settings/weights'), retry });

export const useTgWeights = () =>
  useQuery({ queryKey: qk.tgWeights, queryFn: () => api<TgWeight[]>('/settings/tg-weights'), retry });

export function useSaveWeights() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (presets: Weights['presets']) => api<Weights>('/settings/weights', { method: 'PUT', body: { presets } }),
    onSuccess: (w) => {
      qc.setQueryData(qk.weights, w);
      void qc.invalidateQueries({ queryKey: qk.reference });
    },
  });
}

export function useSaveTgWeights() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rows: TgWeightInput[]) => api<TgWeight[]>('/settings/tg-weights', { method: 'PUT', body: rows }),
    onSuccess: (rows) => {
      qc.setQueryData(qk.tgWeights, rows);
      // eligibility and fleet status depend on the weights
      void qc.invalidateQueries({ queryKey: ['fleet'] });
      void qc.invalidateQueries({ queryKey: ['aircraft'] });
    },
  });
}

export function useSaveHangar() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rows: HangarCapacity[]) =>
      api<HangarCapacity[]>('/settings/hangar-capacity', { method: 'PUT', body: rows }),
    onSuccess: (rows) => qc.setQueryData(qk.hangar, rows),
  });
}

export function useRetrain() {
  return useMutation({
    mutationFn: (name: string) => api<{ job_id: string }>(`/ml/models/${name}/retrain`, { method: 'POST' }),
  });
}
