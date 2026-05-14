import { usePmc } from '../../api/hooks'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, ReferenceLine,
} from 'recharts'

export function PmcWidget({ dateFrom, dateTo }: { dateFrom?: string; dateTo?: string }) {
  const { data, isLoading } = usePmc({ date_from: dateFrom, date_to: dateTo })
  if (isLoading) {
    return <div className="flex items-center justify-center h-full text-gray-400">Loading PMC...</div>
  }
  const series = data?.series ?? []
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={series} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#333" />
        <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={v => v.slice(5)} />
        <YAxis />
        <Tooltip labelFormatter={l => `Date: ${l}`} />
        <Legend />
        <ReferenceLine y={0} stroke="#666" />
        <Line type="monotone" dataKey="ctl" stroke="#4ade80" dot={false} name="CTL (Fitness)" strokeWidth={2} />
        <Line type="monotone" dataKey="atl" stroke="#f97316" dot={false} name="ATL (Fatigue)" strokeWidth={2} />
        <Line type="monotone" dataKey="tsb" stroke="#60a5fa" dot={false} name="TSB (Form)" strokeWidth={1.5} strokeDasharray="4 2" />
      </LineChart>
    </ResponsiveContainer>
  )
}
