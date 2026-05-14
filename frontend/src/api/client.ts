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
