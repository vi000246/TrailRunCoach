import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { useIntensityLoad } from '../../api/hooks'
import { BASE_AXIS_PROPS, BASE_GRID_PROPS, BASE_TOOLTIP_STYLE } from '../../lib/chartTheme'
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'

interface Props {
  dateFrom?: string
  dateTo?: string
}

export function IntensityLoadChart({ dateFrom, dateTo }: Props = {}) {
  const params = dateFrom || dateTo ? { date_from: dateFrom, date_to: dateTo } : undefined
  const { data, isLoading, isError } = useIntensityLoad(params)

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64 text-[#3e4e63] text-sm">
          Loading intensity load...
        </CardContent>
      </Card>
    )
  }

  if (isError || !data?.series?.length) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64 text-[#3e4e63] text-sm">
          No intensity data — running workouts with power required
        </CardContent>
      </Card>
    )
  }

  const series = data.series.map(p => ({
    ...p,
    date: p.date.slice(5),
  }))

  const tickCount = Math.min(series.length, 14)
  const step = Math.max(1, Math.floor(series.length / tickCount))
  const ticks = series.filter((_, i) => i % step === 0).map(p => p.date)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Intensity Load (High-Intensity Time)</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={series} margin={{ top: 4, right: 16, left: -12, bottom: 0 }}>
            <CartesianGrid {...BASE_GRID_PROPS} />
            <XAxis dataKey="date" ticks={ticks} {...BASE_AXIS_PROPS} />
            <YAxis {...BASE_AXIS_PROPS} tickFormatter={(v: number) => `${v}m`} />
            <Tooltip
              {...BASE_TOOLTIP_STYLE}
              formatter={(v: unknown, name: unknown) => [`${Number(v).toFixed(1)} min`, String(name)]}
            />
            <Legend wrapperStyle={{ fontSize: 11, color: '#7d8fa6', paddingTop: 8 }} />
            <Line type="monotone" dataKey="chronic_95pct_min" name="Chronic ≥95% FTP" stroke="#7c3aed" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="acute_95pct_min" name="Acute ≥95% FTP" stroke="#a78bfa" dot={false} strokeWidth={1.5} strokeDasharray="4 2" />
            <Line type="monotone" dataKey="chronic_103pct_min" name="Chronic ≥103% FTP" stroke="#ef4444" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="acute_103pct_min" name="Acute ≥103% FTP" stroke="#fca5a5" dot={false} strokeWidth={1.5} strokeDasharray="4 2" />
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
