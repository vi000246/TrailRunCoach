import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import type {
  WorkoutList, WorkoutSummary, PmcPoint, MmpCurve,
  CorosLoginRequest, CorosLoginResponse, CorosStatus,
  AthleteSettingsResponse, SettingsUpdatePayload, BackfillResult,
  TimeseriesResponse, ZonesResponse, WeeklyResponse,
  RunLoadPoint, IntensityLoadPoint, RunVolumeResponse,
  AiStatusResponse, AiModelsResponse, DashboardSummary, TrailAnalysisResponse,
  SportsFacetsResponse, TrailLoadPoint, TrailSummaryResponse, ChartInterpretation,
  SyncInventory,
} from './client'

// Shared param shape for sport-filterable analytics hooks.
type LoadParams = { date_from?: string; date_to?: string; sports?: string[] }

export function useWorkouts(params?: Record<string, unknown>) {
  return useQuery<WorkoutList>({
    queryKey: ['workouts', params],
    queryFn: () => api.get('/workouts', { params }).then(r => r.data),
  })
}

export function useWorkoutMmp(workoutId: number | null) {
  return useQuery<{ curve: MmpCurve }>({
    queryKey: ['mmp', workoutId],
    queryFn: () => api.get(`/workouts/${workoutId}/mmp`).then(r => r.data),
    enabled: !!workoutId,
    staleTime: Infinity,
  })
}

export function usePmc(params?: LoadParams) {
  return useQuery<{ series: PmcPoint[] }>({
    queryKey: ['pmc', params],
    queryFn: () => api.get('/pmc', { params }).then(r => r.data),
  })
}

export function useDashboard(athleteId = 1) {
  return useQuery({
    queryKey: ['dashboard', athleteId],
    queryFn: () => api.get(`/dashboard/default/${athleteId}`).then(r => r.data),
  })
}

export function useSync() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => fetch('/api/v1/sync/start', { method: 'POST' }).then(r => r.body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workouts'] })
      qc.invalidateQueries({ queryKey: ['pmc'] })
    },
  })
}

export function useScan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post('/scan'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['workouts'] }),
  })
}

export function useCorosStatus(athleteId = 1) {
  return useQuery<CorosStatus>({
    queryKey: ['coros_status', athleteId],
    queryFn: () => api.get(`/auth/coros/status?athlete_id=${athleteId}`).then(r => r.data),
    staleTime: 30_000,
  })
}

export function useCorosLogin() {
  const qc = useQueryClient()
  return useMutation<CorosLoginResponse, Error, CorosLoginRequest>({
    mutationFn: (body) => api.post('/auth/coros/login', body).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['coros_status'] }),
  })
}

export function useCorosSync(athleteId = 1) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (since?: string) =>
      fetch(`/api/v1/sync/coros/start?athlete_id=${athleteId}${since ? `&since=${since}` : ''}`, {
        method: 'POST',
      }).then(r => r.body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workouts'] })
      qc.invalidateQueries({ queryKey: ['pmc'] })
      qc.invalidateQueries({ queryKey: ['coros_status'] })
    },
  })
}

export function useAthleteSettings(athleteId = 1) {
  return useQuery<AthleteSettingsResponse>({
    queryKey: ['athlete_settings', athleteId],
    queryFn: () => api.get(`/athletes/${athleteId}/settings`).then(r => r.data),
    retry: false,
  })
}

export function useUpdateSettings(athleteId = 1) {
  const qc = useQueryClient()
  return useMutation<{ saved: boolean }, Error, SettingsUpdatePayload>({
    mutationFn: (body) => api.put(`/athletes/${athleteId}/settings`, body).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['athlete_settings', athleteId] }),
  })
}

export function useWorkout(workoutId: number) {
  return useQuery<WorkoutSummary>({
    queryKey: ['workout', workoutId],
    queryFn: () => api.get(`/workouts/${workoutId}`).then(r => r.data),
    staleTime: Infinity,
  })
}

export function useTimeseries(workoutId: number) {
  return useQuery<TimeseriesResponse>({
    queryKey: ['timeseries', workoutId],
    queryFn: () => api.get(`/workouts/${workoutId}/timeseries`).then(r => r.data),
    staleTime: Infinity,
  })
}

export function useZones(workoutId: number) {
  return useQuery<ZonesResponse>({
    queryKey: ['zones', workoutId],
    queryFn: () => api.get(`/workouts/${workoutId}/zones`).then(r => r.data),
    staleTime: Infinity,
  })
}

export function useWeeklyLoad(params?: LoadParams) {
  return useQuery<WeeklyResponse>({
    queryKey: ['weekly', params],
    queryFn: () => api.get('/analytics/weekly', { params }).then(r => r.data),
  })
}

export function useRecomputePmc() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post('/pmc/recompute').then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pmc'] }),
  })
}

export function useRunLoad(params?: LoadParams) {
  return useQuery<{ series: RunLoadPoint[] }>({
    queryKey: ['run_load', params],
    queryFn: () => api.get('/analytics/run-load', { params }).then(r => r.data),
  })
}

export function useIntensityLoad(params?: LoadParams) {
  return useQuery<{ series: IntensityLoadPoint[] }>({
    queryKey: ['intensity_load', params],
    queryFn: () => api.get('/analytics/intensity-load', { params }).then(r => r.data),
  })
}

export function useRunVolume(params?: LoadParams) {
  return useQuery<RunVolumeResponse>({
    queryKey: ['run_volume', params],
    queryFn: () => api.get('/analytics/run-volume', { params }).then(r => r.data),
  })
}

export function useSportsFacets(athleteId = 1) {
  return useQuery<SportsFacetsResponse>({
    queryKey: ['sports_facets', athleteId],
    queryFn: () => api.get('/sports/facets', { params: { athlete_id: athleteId } }).then(r => r.data),
  })
}

export function useTrailLoad(params?: { date_from?: string; date_to?: string; athlete_id?: number }) {
  return useQuery<{ series: TrailLoadPoint[] }>({
    queryKey: ['trail_load', params],
    queryFn: () => api.get('/analytics/trail-load', { params }).then(r => r.data),
  })
}

export function useTrailSummary(params?: { date_from?: string; date_to?: string; athlete_id?: number }) {
  return useQuery<TrailSummaryResponse>({
    queryKey: ['trail_summary', params],
    queryFn: () => api.get('/analytics/trail-summary', { params }).then(r => r.data),
  })
}

export function useInterpretation(chart: string, athleteId = 1) {
  return useQuery<ChartInterpretation>({
    queryKey: ['interpretation', chart, athleteId],
    queryFn: () => api.get('/analytics/chart-interpretation', {
      params: { chart, athlete_id: athleteId },
    }).then(r => r.data),
    enabled: !!chart,
    staleTime: 60_000,
  })
}

export function useUpdateClassification() {
  const qc = useQueryClient()
  return useMutation<
    { id: number; trail_classification: string; classification_overridden: boolean },
    Error,
    { id: number; trail_classification: string }
  >({
    mutationFn: (v) => api.patch(`/workouts/${v.id}/classification`, {
      trail_classification: v.trail_classification,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workouts'] })
      qc.invalidateQueries({ queryKey: ['trail_load'] })
      qc.invalidateQueries({ queryKey: ['trail_summary'] })
    },
  })
}

export function useBackfillTss(athleteId = 1) {
  const qc = useQueryClient()
  return useMutation<BackfillResult, Error>({
    mutationFn: () => api.post(`/athletes/${athleteId}/backfill-tss`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['run_load'] })
      qc.invalidateQueries({ queryKey: ['run_volume'] })
      qc.invalidateQueries({ queryKey: ['intensity_load'] })
    },
  })
}

export function useAiStatus(athleteId = 1) {
  return useQuery<AiStatusResponse>({
    queryKey: ['ai_status', athleteId],
    queryFn: () => api.get(`/ai/status/${athleteId}`).then(r => r.data),
    staleTime: 60_000,
  })
}

export function useAiModels() {
  return useQuery<AiModelsResponse>({
    queryKey: ['ai_models'],
    queryFn: () => api.get('/ai/models').then(r => r.data),
    staleTime: Infinity,
  })
}

export function useDashboardSummary(athleteId = 1) {
  return useQuery<DashboardSummary>({
    queryKey: ['dashboard_summary', athleteId],
    queryFn: () => api.get('/analytics/dashboard-summary', { params: { athlete_id: athleteId } }).then(r => r.data),
    staleTime: 60_000,
  })
}

export function useTrailAnalysis(workoutId: number) {
  return useQuery<TrailAnalysisResponse>({
    queryKey: ['trail', workoutId],
    queryFn: () => api.get(`/workouts/${workoutId}/trail`).then(r => r.data),
    staleTime: Infinity,
    retry: false,
  })
}

export function useSyncInventory(athleteId = 1) {
  return useQuery<SyncInventory>({
    queryKey: ['sync_inventory', athleteId],
    queryFn: () => api.get('/sync/inventory', { params: { athlete_id: athleteId } }).then(r => r.data),
  })
}

export function useTpStatus(athleteId = 1) {
  return useQuery<{ authenticated: boolean; last_sync?: string | null; token_expires?: string | null }>({
    queryKey: ['tp_status', athleteId],
    queryFn: () => api.get(`/auth/tp/status?athlete_id=${athleteId}`).then(r => r.data),
  })
}

export function useTpLogin() {
  const qc = useQueryClient()
  return useMutation<unknown, Error, { username: string; password: string }>({
    mutationFn: (body) => api.post('/auth/tp/login', body).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tp_status'] }),
  })
}
