import {
  ComposedChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import type { TimeseriesResponse } from '../../api/client'
import { CHART_COLORS, BASE_GRID_PROPS, BASE_TOOLTIP_STYLE } from '../../lib/chartTheme'
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'

function fmtTime(t: number): string {
  const h = Math.floor(t / 3600)
  const m = Math.floor((t % 3600) / 60)
  const s = t % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

const AXIS = { tick: { fontSize: 11, fill: '#3e4e63' }, tickLine: false, axisLine: false } as const

interface Props {
  data: TimeseriesResponse
}

export function TimeseriesChart({ data }: Props) {
  const hasPower = data.series.some(p => p.power !== undefined)
  const hasHr = data.series.some(p => p.hr !== undefined)
  const hasCadence = data.series.some(p => p.cadence !== undefined)

  if (!hasPower && !hasHr) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-48 text-[#3e4e63] text-sm">
          No time series data available
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Power / HR over Time</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={data.series} margin={{ top: 4, right: 40, left: -12, bottom: 0 }}>
            <CartesianGrid {...BASE_GRID_PROPS} />
            <XAxis dataKey="t" tickFormatter={fmtTime} {...AXIS} interval="preserveStartEnd" />
            {hasPower && (
              <YAxis yAxisId="power" {...AXIS} domain={['auto', 'auto']} />
            )}
            {hasHr && (
              <YAxis yAxisId="hr" orientation="right" {...AXIS} domain={['auto', 'auto']} />
            )}
            <Tooltip
              {...BASE_TOOLTIP_STYLE}
              labelFormatter={(v: unknown) => fmtTime(Number(v))}
              formatter={(v: unknown, name: unknown) => {
                const n = String(name)
                const val = Number(v)
                if (n === 'Power') return [`${val} W`, n]
                if (n === 'HR') return [`${val} bpm`, n]
                if (n === 'Cadence') return [`${val} rpm`, n]
                return [val, n]
              }}
            />
            <Legend wrapperStyle={{ fontSize: 11, color: '#7d8fa6', paddingTop: 8 }} />
            {hasPower && (
              <Line
                yAxisId="power"
                type="monotone"
                dataKey="power"
                name="Power"
                stroke={CHART_COLORS.power}
                dot={false}
                strokeWidth={1.5}
                isAnimationActive={false}
              />
            )}
            {hasHr && (
              <Line
                yAxisId="hr"
                type="monotone"
                dataKey="hr"
                name="HR"
                stroke={CHART_COLORS.hr}
                dot={false}
                strokeWidth={1.5}
                isAnimationActive={false}
              />
            )}
            {hasCadence && (
              <Line
                yAxisId="power"
                type="monotone"
                dataKey="cadence"
                name="Cadence"
                stroke={CHART_COLORS.cadence}
                dot={false}
                strokeWidth={1}
                strokeDasharray="3 2"
                isAnimationActive={false}
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
