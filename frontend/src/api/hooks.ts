import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import type { WorkoutList, PmcPoint, MmpCurve, CorosLoginRequest, CorosLoginResponse, CorosStatus } from './client'

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
