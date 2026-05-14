import { useWorkoutMmp } from '../../api/hooks'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'

function formatDuration(s: number): string {
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return `${Math.floor(s / 3600)}h`
}

export function MmpCurveWidget({ workoutId }: { workoutId: number | null }) {
  const { data, isLoading } = useWorkoutMmp(workoutId)

  if (!workoutId) {
    return <div className="flex items-center justify-center h-full text-gray-400">Select a workout</div>
  }
  if (isLoading) {
    return <div className="flex items-center justify-center h-full text-gray-400">Computing MMP...</div>
  }

  const curve = data?.curve ?? {}
  const points = Object.entries(curve)
    .map(([d, v]) => ({ duration: parseInt(d), power: v, label: formatDuration(parseInt(d)) }))
    .sort((a, b) => a.duration - b.duration)

  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={points} margin={{ top: 5, right: 10, bottom: 20, left: 40 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#333" />
        <XAxis dataKey="label" tick={{ fontSize: 9 }} angle={-45} textAnchor="end" />
        <YAxis label={{ value: 'Watts', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
        <Tooltip
          formatter={(v: unknown) => [`${(v as number).toFixed(0)} W`, 'Power']}
          labelFormatter={l => `Duration: ${l}`}
        />
        <Line type="monotone" dataKey="power" stroke="#a78bfa" dot={false} strokeWidth={2} />
      </LineChart>
    </ResponsiveContainer>
  )
}
