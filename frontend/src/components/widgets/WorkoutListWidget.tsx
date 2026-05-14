import { useWorkouts } from '../../api/hooks'
import type { WorkoutSummary } from '../../api/client'

function fmtDuration(s: number): string {
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h ? `${h}:${String(m).padStart(2, '0')}h` : `${m}min`
}

interface Props {
  lastN?: number
  onSelect?: (id: number) => void
}

export function WorkoutListWidget({ lastN = 10, onSelect }: Props) {
  const { data, isLoading } = useWorkouts({ per_page: lastN })
  if (isLoading) return <div className="text-gray-400 text-sm p-2">Loading...</div>
  const workouts = data?.items ?? []
  return (
    <div className="overflow-auto h-full">
      <table className="w-full text-sm text-left">
        <thead className="text-gray-400 border-b border-gray-700">
          <tr>
            <th className="pb-1 pr-3">Date</th>
            <th className="pr-3">Sport</th>
            <th className="pr-3">Duration</th>
            <th className="pr-3">NP</th>
            <th>TSS</th>
          </tr>
        </thead>
        <tbody>
          {workouts.map((w: WorkoutSummary) => (
            <tr
              key={w.id}
              className="border-b border-gray-800 hover:bg-gray-800 cursor-pointer"
              onClick={() => onSelect?.(w.id)}
            >
              <td className="py-1 pr-3">{w.date}</td>
              <td className="pr-3 capitalize">{w.sport}</td>
              <td className="pr-3">{w.duration_s ? fmtDuration(w.duration_s) : '—'}</td>
              <td className="pr-3">
                {w.metrics?.normalized_power_w ? `${w.metrics.normalized_power_w.toFixed(0)}W` : '—'}
              </td>
              <td>{w.metrics?.tss ? w.metrics.tss.toFixed(1) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
