import {
  ComposedChart, Bar, Cell, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ReferenceLine, ResponsiveContainer,
} from 'recharts'
import { useRunLoad } from '../../api/hooks'
import { CHART_COLORS, BASE_AXIS_PROPS, BASE_GRID_PROPS, BASE_TOOLTIP_STYLE } from '../../lib/chartTheme'
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'

interface Props {
  dateFrom?: string
  dateTo?: string
}

export function RampRateChart({ dateFrom, dateTo }: Props = {}) {
  const params = dateFrom || dateTo ? { date_from: dateFrom, date_to: dateTo } : undefined
  const { data, isLoading, isError } = useRunLoad(params)

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-48 text-[#3e4e63] text-sm">
          Loading ramp rate...
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

  // Show weekly ramp rate — sample to one point per week to reduce noise
  const allSeries = data.series.map(p => ({
    date: p.date.slice(5),
    ramp_rate: p.ramp_rate,
    ctl: p.ctl,
  }))

  const tickCount = Math.min(allSeries.length, 14)
  const step = Math.max(1, Math.floor(allSeries.length / tickCount))
  const ticks = allSeries.filter((_, i) => i % step === 0).map(p => p.date)

  return (
    <Card>
      <CardHeader>
        <CardTitle>CTL Ramp Rate (TSS/day/week)</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={allSeries} margin={{ top: 4, right: 40, left: -12, bottom: 0 }}>
            <CartesianGrid {...BASE_GRID_PROPS} />
            <XAxis dataKey="date" ticks={ticks} {...BASE_AXIS_PROPS} />
            <YAxis yAxisId="ramp" {...BASE_AXIS_PROPS} />
            <YAxis yAxisId="ctl" orientation="right" {...BASE_AXIS_PROPS} />
            <ReferenceLine yAxisId="ramp" y={0} stroke="#7d8fa6" strokeWidth={1} />
            <ReferenceLine yAxisId="ramp" y={7} stroke="#f59e0b" strokeDasharray="4 2" strokeWidth={1} label={{ value: '+7', position: 'right', fontSize: 10, fill: '#f59e0b' }} />
            <Tooltip {...BASE_TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: 11, color: '#7d8fa6', paddingTop: 8 }} />
            <Bar yAxisId="ramp" dataKey="ramp_rate" name="Ramp Rate" radius={[2, 2, 0, 0]}>
              {allSeries.map((entry, i) => (
                <Cell key={i} fill={(entry.ramp_rate ?? 0) >= 0 ? '#7c3aed' : '#ef4444'} />
              ))}
            </Bar>
            <Line yAxisId="ctl" type="monotone" dataKey="ctl" name="CTL" stroke={CHART_COLORS.ctl} dot={false} strokeWidth={1.5} opacity={0.5} />
          </ComposedChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
