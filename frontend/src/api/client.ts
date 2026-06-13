import axios from 'axios'

// indexes:null → array params serialize as repeated keys (sports=a&sports=b),
// which is what FastAPI's `Query(None)` list params expect (no `[]` brackets).
export const api = axios.create({
  baseURL: '/api/v1',
  paramsSerializer: { indexes: null },
})

export type MmpCurve = Record<string, number>

export interface SportFacet {
  key: string
  count: number
  label: string
}
export interface SportsFacetsResponse {
  sports: SportFacet[]
}

export interface TrailLoadPoint {
  date: string
  ctl: number
  atl: number
  tsb: number
  hr_tss: number
  r_tss: number | null
}

export interface TrailSummaryRecent {
  date: string | null
  gain_m: number
  distance_km: number
  vam: number | null
}
export interface TrailSummaryResponse {
  athlete_id: number
  total_gain_m: number
  activity_count: number
  recent: TrailSummaryRecent[]
}

export interface ChartInterpretation {
  status: string
  label: string
  color: string
  summary: string
  chart?: string
}

export interface SyncInventory {
  total: number
  by_source: Record<string, number>
  by_sport: Record<string, number>
  date_min: string | null
  date_max: string | null
  last_sync: { coros: string | null; tp: string | null }
}

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

export interface SettingsUpdatePayload {
  ftp_w?: number
  run_ftp_w?: number
  lthr?: number
  weight_kg?: number
  threshold_pace_s_per_km?: number
  initial_ctl_run?: number
  initial_atl_run?: number
  effective_date?: string
  ai_provider?: string
  ai_api_key?: string
  ai_model?: string
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

// ---- AI Coach ----

export interface AiStatusResponse {
  configured: boolean
  provider?: string
  model?: string
}

export interface AiModelsResponse {
  claude: string[]
  openai: string[]
  gemini?: string[]
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

// ---- Dashboard Summary ----

export interface DashboardSummary {
  tsb: number | null
  tsb_state: 'fresh' | 'optimal' | 'tired' | 'overreached' | null
  ctl: number | null
  ctl_trend: number | null
  weekly_tss: number
  weekly_hours: number
  weekly_count: number
  last_workout: {
    id: number
    date: string
    sport: string
    duration_s: number
  } | null
}

// ---- Trail Analysis ----

export interface TrailSeriesPoint {
  t: number
  dist_m: number
  alt_m: number
  grade_pct: number
  pace_s_km: number
  gap_s_km: number
  hr?: number
  cadence?: number
}

export interface ClimbSegment {
  start_m: number
  end_m: number
  gain_m: number
  distance_m: number
  grade_pct: number
  duration_s: number
  vam: number
}

export interface HrDrift {
  hr_first_half: number
  hr_second_half: number
  gap_first_half_s_per_km: number
  gap_second_half_s_per_km: number
  decoupling_pct: number
}

export interface GradeCadencePoint {
  grade_pct: number
  cadence: number
}

export interface TrailAnalysisResponse {
  workout_id: number
  is_trail: boolean
  series: TrailSeriesPoint[]
  climb_segments: ClimbSegment[]
  hr_drift: HrDrift | null
  grade_cadence: GradeCadencePoint[]
  total_gain_m: number
}

export interface AthleteSettingsResponse {
  athlete_id: number
  effective_date: string
  ftp_w: number | null
  run_ftp_w: number | null
  lthr: number | null
  weight_kg: number | null
  threshold_pace_s_per_km: number | null
  initial_ctl_run: number | null
  initial_atl_run: number | null
  ai_provider: string | null
  ai_model: string | null
  power_zones: PowerZone[]
  hr_zones: HrZone[]
}
