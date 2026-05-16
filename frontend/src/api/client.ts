import axios from 'axios'

export const api = axios.create({ baseURL: '/api/v1' })

export type MmpCurve = Record<string, number>

export interface PmcPoint {
  date: string
  ctl: number
  atl: number
  tsb: number
  tss: number
}

export interface WorkoutSummary {
  id: number
  date: string
  sport: string
  duration_s: number
  metrics: Record<string, number>
  file_format: string
  source: string
}

export interface WorkoutList {
  total: number
  page: number
  per_page: number
  items: WorkoutSummary[]
}

export interface CorosLoginRequest {
  email: string
  password: string
  athlete_id?: number
}

export interface CorosLoginResponse {
  authenticated: boolean
  coros_user_id: string
  email: string
  token_expires: string | null
}

export interface CorosStatus {
  authenticated: boolean
  email: string | null
  token_expires: string | null
  last_sync: string | null
}

export interface PowerZone {
  zone: number
  label: string
  min_w: number
  max_w: number | null
}

export interface HrZone {
  zone: number
  label: string
  min_bpm: number
  max_bpm: number | null
}

export interface AthleteSettingsResponse {
  athlete_id: number
  effective_date: string
  ftp_w: number | null
  lthr: number | null
  weight_kg: number | null
  threshold_pace_s_per_km: number | null
  initial_ctl_run: number | null
  initial_atl_run: number | null
  power_zones: PowerZone[]
  hr_zones: HrZone[]
}

export interface SettingsUpdatePayload {
  ftp_w?: number
  lthr?: number
  weight_kg?: number
  threshold_pace_s_per_km?: number
  initial_ctl_run?: number
  initial_atl_run?: number
  effective_date?: string
}

export interface BackfillResult {
  recomputed_power_tss: number
  computed_rtss_pace: number
  skipped_no_data: number
  skipped_already_has_tss: number
}

export interface TimeseriesPoint {
  t: number
  power?: number
  hr?: number
  cadence?: number
}

export interface TimeseriesResponse {
  workout_id: number
  duration_s: number
  sample_rate_s: number
  series: TimeseriesPoint[]
}

export interface ZoneEntry {
  zone: number
  name: string
  min_w?: number
  max_w?: number | null
  min_bpm?: number
  max_bpm?: number | null
  time_s: number
}

export interface ZonesResponse {
  workout_id: number
  ftp: number
  lthr: number
  power_zones: ZoneEntry[]
  hr_zones: ZoneEntry[]
}

export interface WeeklyEntry {
  week_start: string
  tss: number
  hours: number
  count: number
}

export interface WeeklyResponse {
  weeks: WeeklyEntry[]
}

export interface RunLoadPoint {
  date: string
  ctl: number
  atl: number
  tsb: number
  tss: number
  acwr: number | null
  daily_pct_ctl: number | null
  ramp_rate: number | null
  ramp_pct_ctl: number | null
}

export interface IntensityLoadPoint {
  date: string
  chronic_95pct_min: number
  acute_95pct_min: number
  chronic_103pct_min: number
  acute_103pct_min: number
}

export interface RunVolumeWeek {
  week_start: string
  distance_km: number
  hours: number
  elevation_m: number
  tss: number
  count: number
}

export interface RunVolumeMonth {
  month: string
  distance_km: number
  hours: number
  elevation_m: number
  count: number
}

export interface RunVolumeResponse {
  weeks: RunVolumeWeek[]
  months: RunVolumeMonth[]
  athlete_id: number
}
