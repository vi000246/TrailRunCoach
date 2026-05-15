import {
  BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer,
} from 'recharts'
import { useRunLoad } from '../../api/hooks'
import { BASE_AXIS_PROPS, BASE_GRID_PROPS, BASE_TOOLTIP_STYLE } from '../../lib/chartTheme'
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'

interface Props {
  dateFrom?: string
  dateTo?: string
}

function barColor(pct: number | null): string {
  if (pct == null) return '#3e4e63'
  if (pct >= 3.0) return '#ef4444'
  if (pct >= 1.5) return '#f59e0b'
  return '#22c55e'
}

export function DailyPctCtlChart({ dateFrom, dateTo }: Props = {}) {
  const params = dateFrom || dateTo ? { date_from: dateFrom, date_to: dateTo } : undefined
  const { data, isLoading, isError } = useRunLoad(params)

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-48 text-[#3e4e63] text-sm">
          Loading daily % CTL...
        </CardContent>
      </Card>
    )
  }

  if (isError || !data?.series?.length) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-48 text-[#3e4e63] text-sm">
          No data available
        </CardContent>
      </Card>
    )
  }

  const series = data.series
    .filter(p => p.daily_pct_ctl != null && p.tss > 0)
    .map(p => ({
      date: p.date.slice(5),
      pct: p.daily_pct_ctl,
      pct_display: p.daily_pct_ctl != null ? Math.round(p.daily_pct_ctl * 100) : null,
    }))

  const tickCount = Math.min(series.length, 14)
  const step = Math.max(1, Math.floor(series.length / tickCount))
  const ticks = series.filter((_, i) => i % step === 0).map(p => p.date)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Daily % of CTL</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={series} margin={{ top: 4, right: 16, left: -12, bottom: 0 }}>
            <CartesianGrid {...BASE_GRID_PROPS} />
            <XAxis dataKey="date" ticks={ticks} {...BASE_AXIS_PROPS} />
            <YAxis
              {...BASE_AXIS_PROPS}
              tickFormatter={(v: number) => `${v * 100 | 0}%`}
            />
            <ReferenceLine y={1.5} stroke="#f59e0b" strokeDasharray="4 2" strokeWidth={1} />
            <ReferenceLine y={3.0} stroke="#ef4444" strokeDasharray="4 2" strokeWidth={1} />
            <Tooltip
              {...BASE_TOOLTIP_STYLE}
              formatter={(v: unknown) => [`${Math.round(Number(v) * 100)}%`, 'Daily % CTL']}
            />
            <Bar dataKey="pct" name="Daily % CTL" radius={[2, 2, 0, 0]}>
              {series.map((entry, i) => (
                <Cell key={i} fill={barColor(entry.pct)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <div className="flex gap-4 mt-2 text-xs text-[#7d8fa6]">
          <span><span className="inline-block w-2 h-2 rounded-sm bg-green-500 mr-1" />≤150%</span>
          <span><span className="inline-block w-2 h-2 rounded-sm bg-amber-500 mr-1" />150–300%</span>
          <span><span className="inline-block w-2 h-2 rounded-sm bg-red-500 mr-1" />≥300%</span>
        </div>
      </CardContent>
    </Card>
  )
}
