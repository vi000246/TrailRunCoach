import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import type {
  WorkoutList, WorkoutSummary, PmcPoint, MmpCurve,
  CorosLoginRequest, CorosLoginResponse, CorosStatus,
  AthleteSettingsResponse, SettingsUpdatePayload, BackfillResult,
  TimeseriesResponse, ZonesResponse, WeeklyResponse,
  RunLoadPoint, IntensityLoadPoint, RunVolumeResponse,
  AiStatusResponse, AiModelsResponse, DashboardSummary, TrailAnalysisResponse,
} from './client'

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

export function usePmc(params?: { date_from?: string; date_to?: string }) {
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

export function useWeeklyLoad(params?: { date_from?: string; date_to?: string }) {
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

export function useRunLoad(params?: { date_from?: string; date_to?: string }) {
  return useQuery<{ series: RunLoadPoint[] }>({
    queryKey: ['run_load', params],
    queryFn: () => api.get('/analytics/run-load', { params }).then(r => r.data),
  })
}

export function useIntensityLoad(params?: { date_from?: string; date_to?: string }) {
  return useQuery<{ series: IntensityLoadPoint[] }>({
    queryKey: ['intensity_load', params],
    queryFn: () => api.get('/analytics/intensity-load', { params }).then(r => r.data),
  })
}

export function useRunVolume(params?: { date_from?: string; date_to?: string }) {
  return useQuery<RunVolumeResponse>({
    queryKey: ['run_volume', params],
    queryFn: () => api.get('/analytics/run-volume', { params }).then(r => r.data),
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
