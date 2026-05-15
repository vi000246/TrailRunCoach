import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import { usePmc } from '../api/hooks'

export function PmcChart() {
  const { data, isLoading, isError } = usePmc()

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-500 text-sm">
        Loading PMC...
      </div>
    )
  }

  if (isError || !data?.series?.length) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-600 text-sm">
        No PMC data — sync workouts to populate.
      </div>
    )
  }

  const series = data.series.map(p => ({
    ...p,
    date: p.date.slice(5), // MM-DD
  }))

  const tickCount = Math.min(series.length, 12)
  const step = Math.max(1, Math.floor(series.length / tickCount))
  const ticks = series.filter((_, i) => i % step === 0).map(p => p.date)

  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <h2 className="text-sm font-semibold text-gray-400 mb-3">Performance Management Chart</h2>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={series} margin={{ top: 4, right: 12, left: -8, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey="date"
            ticks={ticks}
            tick={{ fontSize: 11, fill: '#9ca3af' }}
            tickLine={false}
          />
          <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} axisLine={false} />
          <Tooltip
            contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 12 }}
            labelStyle={{ color: '#d1d5db' }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line
            type="monotone"
            dataKey="ctl"
            name="CTL (Fitness)"
            stroke="#a855f7"
            dot={false}
            strokeWidth={2}
          />
          <Line
            type="monotone"
            dataKey="atl"
            name="ATL (Fatigue)"
            stroke="#ef4444"
            dot={false}
            strokeWidth={2}
          />
          <Line
            type="monotone"
            dataKey="tsb"
            name="TSB (Form)"
            stroke="#22c55e"
            dot={false}
            strokeWidth={1.5}
            strokeDasharray="4 2"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
