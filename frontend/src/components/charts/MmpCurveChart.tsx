import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
} from 'recharts'
import type { MmpCurve } from '../../api/client'
import { CHART_COLORS, BASE_GRID_PROPS, BASE_TOOLTIP_STYLE } from '../../lib/chartTheme'
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'

function fmtDur(s: number): string {
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  return `${(s / 3600).toFixed(1)}h`
}

const LOG_TICKS = [5, 10, 30, 60, 300, 600, 1200, 3600, 7200]
const AXIS = { tick: { fontSize: 11, fill: '#3e4e63' }, tickLine: false, axisLine: false } as const

interface Props {
  curve: MmpCurve
}

export function MmpCurveChart({ curve }: Props) {
  const entries = Object.entries(curve)
    .map(([d, v]) => ({ dur: Number(d), power: Math.round(v) }))
    .filter(e => e.power > 0)
    .sort((a, b) => a.dur - b.dur)

  if (!entries.length) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-48 text-[#3e4e63] text-sm">
          No MMP data available
        </CardContent>
      </Card>
    )
  }

  const ticks = LOG_TICKS.filter(t => t >= entries[0].dur && t <= entries[entries.length - 1].dur)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Mean Maximal Power</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={entries} margin={{ top: 4, right: 16, left: -12, bottom: 0 }}>
            <CartesianGrid {...BASE_GRID_PROPS} />
            <XAxis
              dataKey="dur"
              scale="log"
              type="number"
              domain={['dataMin', 'dataMax']}
              ticks={ticks}
              tickFormatter={fmtDur}
              {...AXIS}
            />
            <YAxis {...AXIS} domain={['auto', 'auto']} />
            <Tooltip
              {...BASE_TOOLTIP_STYLE}
              labelFormatter={(v: unknown) => `Duration: ${fmtDur(Number(v))}`}
              formatter={(v: unknown) => [`${Number(v)} W`, 'MMP']}
            />
            <Line
              type="monotone"
              dataKey="power"
              name="MMP"
              stroke={CHART_COLORS.ctl}
              dot={false}
              strokeWidth={2}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
