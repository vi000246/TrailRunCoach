import { useNavigate } from 'react-router'
import { Badge } from './ui/badge'
import type { WorkoutSummary } from '../api/client'

function formatDuration(s: number): string {
  if (!s) return '—'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
    : `${m}:${String(sec).padStart(2, '0')}`
}

function sportVariant(sport: string): 'cycling' | 'running' | 'swimming' | 'strength' | 'other' {
  const s = sport?.toLowerCase() || ''
  if (s.includes('cycling') || s.includes('bike') || s.includes('ride')) return 'cycling'
  if (s.includes('run') || s.includes('trail')) return 'running'
  if (s.includes('swim')) return 'swimming'
  if (s.includes('strength') || s.includes('gym')) return 'strength'
  return 'other'
}

interface Props {
  workout: WorkoutSummary
}

export function ActivityRow({ workout }: Props) {
  const navigate = useNavigate()
  const np = workout.metrics?.np || workout.metrics?.avg_power
  const tss = workout.metrics?.tss

  return (
    <tr
      onClick={() => navigate(`/activities/${workout.id}`)}
      className="border-b border-[#131824] hover:bg-[#0e1117] cursor-pointer transition-colors"
    >
      <td className="px-4 py-2.5 text-sm text-[#e8edf5] whitespace-nowrap">
        {workout.date || '—'}
      </td>
      <td className="px-4 py-2.5">
        <Badge variant={sportVariant(workout.sport)} className="text-xs">
          {workout.sport || 'unknown'}
        </Badge>
      </td>
      <td className="px-4 py-2.5 text-sm text-[#7d8fa6] tabular-nums">
        {formatDuration(workout.duration_s)}
      </td>
      <td className="px-4 py-2.5 text-sm text-[#7d8fa6] tabular-nums">
        {np ? `${Math.round(np)} W` : '—'}
      </td>
      <td className="px-4 py-2.5 text-sm text-[#7d8fa6] tabular-nums">
        {tss ? Math.round(tss) : '—'}
      </td>
      <td className="px-4 py-2.5 text-xs text-[#3e4e63]">
        {workout.source}
      </td>
    </tr>
  )
}
